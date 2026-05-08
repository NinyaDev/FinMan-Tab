"""

Monthly summary generator for FinMan.

"""

import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from config import CONFIG
from clients.google_auth import get_credentials
from clients.sheets_writer import (_month_name_for_date, _prev_month_name, _col_letter, _all_configured_prefixes)

log = logging.getLogger(__name__)

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
            if len(row) < 2 or not row[0]:
                continue
            description = str(row[0]).strip()
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    creds = get_credentials()

    sheets = build("sheets", "v4", credentials=creds)
    spreadsheet_id = CONFIG["sheet"]["spreadsheet_id"]

    transactions = _gather_month_transactions(sheets, spreadsheet_id, "2026-05-01")

    # Print first 10 to terminal
    preview_lines = []
    for tx in transactions[:10]:
        line = f"  {tx['table_prefix']:20s} {tx['description'][:30]:30s} ${tx['amount']:>9.2f}"
        log.info(line)
        preview_lines.append(line)

    # Email the preview so we end-to-end-test the gather + send flow
    gmail = build("gmail", "v1", credentials=creds)
    profile = gmail.users().getProfile(userId="me").execute()
    my_email = profile["emailAddress"]

    body = (
        f"<p>Gathered <b>{len(transactions)}</b> transactions from Mayo.</p>"
        f"<pre style='font-family: monospace'>{chr(10).join(preview_lines)}</pre>"
    )
    msg_id = send_email(
        gmail_service=gmail,
        to=my_email,
        sender=my_email,
        subject="Finance Manager - Mayo Preview",
        html_body=body,
    )
    log.info(f"Sent preview email. Message ID: {msg_id}")