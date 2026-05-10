"""

Monthly summary generator for FinMan.

"""

import base64
import json
import logging
import urllib.parse
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml
from google.genai import types
from googleapiclient.discovery import build

from config import CONFIG
from clients.gemini_client import _client as _gemini_client
from clients.google_auth import get_credentials
from clients.sheets_writer import (_month_name_for_date, _prev_month_name, _col_letter, _all_configured_prefixes)

log = logging.getLogger(__name__)

# Monthly-summary prompt
_PROMPTS_FILE = Path("prompts.yaml")
_SUMMARY_PROMPT = yaml.safe_load(_PROMPTS_FILE.read_text())["monthly_summary"]
SUMMARY_MODEL = _SUMMARY_PROMPT["model"]
SUMMARY_TEMPLATE = _SUMMARY_PROMPT["template"]

# Persistent state for the monthly summary: tracks last sent month + history
# of past summaries (used as prior_summary context for trend comparisons).
SUMMARY_STATE_FILE = Path("summary_state.json")

CHART_PALETTE = [
    "#b39ddb",  # lavender
    "#f48fb1",  # pink
    "#90caf9",  # sky blue
    "#a5d6a7",  # mint
    "#ffab91",  # peach
    "#80cbc4",  # turquoise
    "#ce93d8",  # orchid
    "#ef9a9a",  # coral
    "#ffb74d",  # amber
    "#c5e1a5",  # sage
]

# JSON schema enforced on Gemini's response. Guarantees we get back exactly these fields.
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "total_income": {"type": "number"},
        "total_expenses": {"type": "number"},
        "net": {"type": "number"},
        "top_merchants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "total_spend": {"type": "number"},
                    "count": {"type": "integer"},
                },
                "required": ["name", "total_spend", "count"],
            },
        },
        "spend_by_category": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["category", "amount"],
            },
        },
        "observations": {"type": "array", "items": {"type": "string"}},
        "commentary": {"type": "string"},
    },
    "required": [
        "total_income", "total_expenses", "net",
        "top_merchants", "spend_by_category",
        "observations", "commentary",
    ],
}

def send_email(gmail_service, to: str, sender: str, subject: str, html_body: str) -> str:
    """ Send an HTML email via Gmail API. Returns the sent message ID"""
    msg = MIMEMultipart("alternative")
    msg["to"] = to
    msg["from"] = sender
    msg["subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = gmail_service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()
    return sent["id"]

def _gather_month_transactions(service, spreadsheet_id: str, date_str: str) -> list[dict]:
    # Read all transactions from the month tab matching date_str. And returns a list of {description, amount, table_prefix}
    month_name = _month_name_for_date(date_str)
    prev_month = _prev_month_name(date_str)
    
    metadata = service.spreadsheets().get(spreadsheetId = spreadsheet_id).execute()
    sheet = next(
        (s for s in metadata["sheets"]
         if s["properties"]["title"] == month_name),
        None
    )
    if sheet is None:
        raise RuntimeError(f"Month tab '{month_name}' not found")
    
    # Build set of carryover descriptions to exclude
    carryover_descriptions = set()
    for spec in CONFIG.get("balance_carryover") or []:
        carryover_descriptions.add(
            spec["description"].format(prev_month=prev_month)
        )

    transactions = []
    for table in sheet.get("tables", []):
        prefix = next(
            (p for p in _all_configured_prefixes() if table["name"].startswith(p)),
            None,
        )
        if prefix is None:
            continue  # skip Fidelity, Chase - not in routing

        start_row = table["range"]["startRowIndex"]
        end_row = table["range"]["endRowIndex"]
        start_col = table["range"]["startColumnIndex"]
        data_start = start_row + 2
        data_end = end_row - 1
        desc_col = _col_letter(start_col)
        amount_col = _col_letter(start_col + 1)
        range_to_read = f"'{month_name}'!{desc_col}{data_start}:{amount_col}{data_end}"

        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_to_read,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()

        for row in result.get("values", []):
            # Filtering the carryover balances so we only analyze data for the current month
            # No transactions
            if len(row) < 2 or not row[0]:
                continue
            description = str(row[0]).strip()
            # Ignore carry
            if description in carryover_descriptions:
                continue
            try:
                amount = float(row[1])
            except (ValueError, TypeError):
                continue
            transactions.append({
                "description": description,
                "amount": amount,
                "table_prefix": prefix,
            })

    log.info(f"Gathered {len(transactions)} transactions from {month_name}")
    return transactions


def _classify_transactions(transactions: list[dict]) -> list[dict]:
    """Add a 'direction' field (income or expense) to each transaction.
    Uses account_routing to know which prefixes are single-table (sign decides) vs two-table (table decides)."""
    income_prefixes = set()
    outflow_prefixes = set()
    single_table_prefixes = set()
    for entry in CONFIG["account_routing"].values():
        ip = entry["income_table_prefix"]
        op = entry["outflow_table_prefix"]
        if ip == op:
            single_table_prefixes.add(ip)
        else:
            income_prefixes.add(ip)
            outflow_prefixes.add(op)

    classified = []
    for tx in transactions:
        prefix = tx["table_prefix"]
        amount = tx["amount"]
        if prefix in single_table_prefixes:
            direction = "expense" if amount > 0 else "income"
            display_amount = abs(amount)
        elif prefix in outflow_prefixes:
            direction = "expense"
            display_amount = amount
        elif prefix in income_prefixes:
            direction = "income"
            display_amount = amount
        else:
            continue  # not a configured prefix, shouldn't happen after gather
        classified.append({
            "description": tx["description"],
            "amount": display_amount,
            "direction": direction,
        })
    return classified


def _summarize_transactions(transactions: list[dict], month_name: str, prior_summary: dict | None = None) -> dict:
    """Send transactions to Gemini with response_schema enforced; return parsed summary dict."""
    classified = _classify_transactions(transactions)

    tx_lines = [
        f"- {tx['description']} | ${tx['amount']:.2f} | {tx['direction']}"
        for tx in classified
    ]
    transactions_text = "\n".join(tx_lines)

    if prior_summary:
        prior_text = (
            "\nFor context, the prior month had:\n"
            f"  Total income:   ${prior_summary.get('total_income', 0):.2f}\n"
            f"  Total expenses: ${prior_summary.get('total_expenses', 0):.2f}\n"
            f"  Net:            ${prior_summary.get('net', 0):.2f}\n"
        )
    else:
        prior_text = "\n(No prior month available for comparison.)\n"

    prompt = SUMMARY_TEMPLATE.format(
        month_name=month_name,
        transactions=transactions_text,
        prior_context=prior_text,
    )

    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
        response_schema=SUMMARY_SCHEMA,
    )

    log.info(f"Calling Gemini for {month_name} summary ({len(classified)} transactions)...")
    response = _gemini_client.models.generate_content(
        model=SUMMARY_MODEL,
        contents=prompt,
        config=config,
    )
    summary = json.loads(response.text)
    log.info(
        f"Summary received: income ${summary['total_income']:.2f}, "
        f"expenses ${summary['total_expenses']:.2f}, "
        f"net ${summary['net']:.2f}"
    )
    return summary


def _build_summary_email_body(summary: dict, month_name: str, chart_url: str, prior_summary: dict | None = None) -> str:
    """Compose the HTML body for the monthly summary email.
    Uses inline styles only (email clients drop <style> blocks)."""
    net = summary["net"]
    net_color = "#2e7d32" if net >= 0 else "#c62828"

    # Prior-month comparison line, if context available.
    if prior_summary:
        prior_net = prior_summary.get("net", 0)
        delta = net - prior_net
        delta_sign = "+" if delta >= 0 else ""
        prior_line = (
            f"<p style='color:#666;font-size:13px;margin-top:0'>"
            f"vs prior month net (${prior_net:,.2f}): {delta_sign}${delta:,.2f}"
            f"</p>"
        )
    else:
        prior_line = ""

    merchants_html = "".join(
        f"<li><b>{m['name']}</b> - ${m['total_spend']:,.2f} "
        f"<span style='color:#888'>({m['count']}x)</span></li>"
        for m in summary["top_merchants"]
    )

    observations_html = "".join(f"<li>{o}</li>" for o in summary["observations"])

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 640px; color:#222">
      <h2 style="margin-bottom:4px">{month_name} Summary</h2>
      <p style="color:#888;margin-top:0;font-size:13px">Generated by Finance Manager</p>

      <p style="font-size:18px;margin:16px 0 4px 0">
        Net: <b style="color:{net_color}">${net:,.2f}</b>
      </p>
      {prior_line}

      <img src="{chart_url}" alt="Spending by category" style="max-width:600px;display:block;margin:16px 0">

      <h3 style="margin-bottom:4px">Top merchants</h3>
      <ul style="margin-top:4px">{merchants_html}</ul>

      <h3 style="margin-bottom:4px">Observations</h3>
      <ul style="margin-top:4px">{observations_html}</ul>

      <h3 style="margin-bottom:4px">Commentary</h3>
      <p style="line-height:1.5">{summary['commentary']}</p>
    </div>
    """


def _load_summary_state() -> dict:
    """Load summary_state.json, or return an empty default if not present."""
    if not SUMMARY_STATE_FILE.exists():
        return {"last_summarized": None, "history": {}}
    return json.loads(SUMMARY_STATE_FILE.read_text())


def _save_summary_state(state: dict) -> None:
    """Persist state to disk (overwrite)."""
    SUMMARY_STATE_FILE.write_text(json.dumps(state, indent=2))


def _prior_month_key(month_key: str) -> str:
    """'2026-05' -> '2026-04'. Handles year wrap ('2027-01' -> '2026-12')."""
    year, month = map(int, month_key.split("-"))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def _should_send_summary(today: date, state: dict) -> str | None:
    """Decide whether a summary is due. Returns the 'YYYY-MM' month to summarize, or None.

    Target month = the month immediately before today's month. Send if state's
    last_summarized is null or older than the target month. This makes the cron
    self-recovering: a missed day on the 1st gets caught up on the 2nd, etc."""
    if today.month == 1:
        target_year = today.year - 1
        target_month_num = 12
    else:
        target_year = today.year
        target_month_num = today.month - 1
    target_month = f"{target_year}-{target_month_num:02d}"

    last = state.get("last_summarized")
    if last is None or last < target_month:
        return target_month
    return None


def maybe_send_monthly_summary(creds, today: date | None = None) -> str | None:
    """If a monthly summary is due, generate and send it. Returns the month
    summarized ('YYYY-MM') or None if skipped. State only updates on a fully
    successful send, so any failure is naturally retried by tomorrow's run."""
    if today is None:
        today = date.today()

    state = _load_summary_state()
    target_month = _should_send_summary(today, state)
    if target_month is None:
        log.info(f"No summary due (last_summarized={state.get('last_summarized')})")
        return None

    target_date_str = f"{target_month}-01"
    month_name = _month_name_for_date(target_date_str)
    log.info(f"Summary due for {month_name} ({target_month})")

    sheets = build("sheets", "v4", credentials=creds)
    spreadsheet_id = CONFIG["sheet"]["spreadsheet_id"]
    transactions = _gather_month_transactions(sheets, spreadsheet_id, target_date_str)

    prior_summary = state.get("history", {}).get(_prior_month_key(target_month))
    summary = _summarize_transactions(transactions, month_name, prior_summary=prior_summary)

    chart_url = _build_chart_url(
        summary["spend_by_category"],
        title=f"{month_name} Spending by Category",
    )
    html_body = _build_summary_email_body(summary, month_name, chart_url, prior_summary)

    gmail = build("gmail", "v1", credentials=creds)
    profile = gmail.users().getProfile(userId="me").execute()
    my_email = profile["emailAddress"]
    msg_id = send_email(
        gmail_service=gmail,
        to=my_email,
        sender=my_email,
        subject=f"Finance Manager - {month_name} Summary - Net ${summary['net']:.2f}",
        html_body=html_body,
    )
    log.info(f"Sent {month_name} summary email. Message ID: {msg_id}")

    # Persist state ONLY after successful send.
    state["last_summarized"] = target_month
    state.setdefault("history", {})[target_month] = {
        "net": summary["net"],
        "total_income": summary["total_income"],
        "total_expenses": summary["total_expenses"],
        "spend_by_category": summary["spend_by_category"],
    }
    _save_summary_state(state)
    log.info(f"Updated summary_state.json: last_summarized={target_month}")
    return target_month


def _build_chart_url(spend_by_category: list[dict], title: str = "Spending by Category") -> str:
    """Return a QuickChart.io URL for a pie chart of category spending.
    Email clients fetch the URL on open and render the resulting PNG inline.

    Percentages are baked into the legend label text (e.g. "Groceries 34% - $320")
    because QuickChart's JS formatter evaluation is unreliable across versions."""
    amounts = [item["amount"] for item in spend_by_category]
    total = sum(amounts) or 1
    percentages = [round(a / total * 100) for a in amounts]
    # Legend label carries the percentage AND dollar amount - reliable across renderers.
    # Slice labels show the bare percentage number (datalabels formatter just appends nothing).
    labels = [
        f"{item['category']} {percentages[i]}% - ${round(item['amount'])}"
        for i, item in enumerate(spend_by_category)
    ]

    config = {
        "type": "pie",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": percentages,
                "backgroundColor": CHART_PALETTE[:len(labels)],
            }],
        },
        "options": {
            "title": {"display": True, "text": title, "fontSize": 16},
            "legend": {"position": "right"},
            "plugins": {
                "datalabels": {
                    "color": "#ffffff",
                    "font": {"weight": "bold", "size": 13},
                },
            },
        },
    }

    json_config = json.dumps(config, separators=(",", ":"))
    return (
        "https://quickchart.io/chart"
        f"?c={urllib.parse.quote(json_config)}"
        "&w=600&h=400&bkg=white"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    creds = get_credentials()

    # In production this is just date.today(). For testing before June 1 we
    # override to pretend it's June 1 so the orchestrator targets Mayo.
    fake_today = date(2026, 6, 1)
    log.info(f"Running maybe_send_monthly_summary with today={fake_today} (test override)")

    result = maybe_send_monthly_summary(creds, today=fake_today)
    if result:
        log.info(f"Summary sent for {result}. summary_state.json updated.")
    else:
        log.info("No summary needed; state file unchanged.")