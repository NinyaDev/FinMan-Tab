# Finance Manager

A Python pipeline that pulls bank transactions through **Plaid**, cleans the merchant descriptions through **Google Gemini Flash** (in your own voice via few-shot prompting), and writes them into the right table of the right tab in **your own Google Sheet**. At the start of every month it sends you an HTML email summary of the prior month with an LLM-generated commentary and a category breakdown pie chart. Runs unattended on a free-tier GitHub Actions cron.

Built around an existing personal-finance spreadsheet rather than replacing it: month tabs, structured tables, running balances, and TOTAL formulas all keep working. The pipeline is config-driven and fork-friendly - your account IDs, your tab names, and your prompt examples live in a few YAML files, not in the code.

---

## Demo

<!-- Demo video coming soon -->

### Screenshots

<p align="center">
  <img src="https://github.com/user-attachments/assets/9de14ae7-6493-4242-bae5-10a1a13d5919" alt="Pipeline running in GitHub Actions" width="860">
</p>
<p align="center"><sub><b>Pipeline running in GitHub Actions</b> - Mayo tab auto-created, carryover written, 21 transactions cleaned by Gemini and routed to the right tables.</sub></p>

<p align="center">
  <sub><i>Mayo tab in Google Sheets - placeholder, screenshot coming soon</i></sub>
</p>

---

## Features

- **Bank ingest via Plaid `/transactions/sync`:** incremental, cursor-based fetch - each run only sees transactions that posted since the previous run. No duplicates, no manual de-dup logic.
- **Spanglish description cleaning via Gemini 3.1 Flash Lite:** few-shot prompted with ~20 of your own real descriptions, so output stays in your voice (`"Walmart - Groceries"`, `"Gas en Chevron"`, `"Paycheck from Student Job"`). Fail-soft - if Gemini is overloaded for a transaction, the raw merchant string lands in the sheet so nothing is ever lost.
- **Date-driven tab routing:** uses `tx.date` (not `today`) so a cron at 12:01 AM on June 1 still files May 30 transactions into Mayo, not Junio.
- **Auto-creates new month tabs from a hidden Template:** duplicates the Template, makes the new tab visible, renames every table inside it to `<prefix><MonthName>` for cleanliness.
- **Configurable balance carryover:** writes a starting-balance row at the top of each new month tab - pulled either from a specific cell in the prior tab (e.g. running balance) or the prior month's table footer (e.g. credit-card total).
- **Monthly summary email:** on the first run of each new month, Gemini analyzes the prior month's transactions and produces a structured summary (net, top merchants, spend-by-category, observations, commentary). An HTML email with an embedded pie chart (via QuickChart.io) lands in your inbox. State persists across runs so each month is summarized exactly once, with prior-month context fed into Gemini for trend comparisons.
- **Idempotent and crash-safe:** Plaid cursors persist per-bank to `access_tokens.json` only after the bank's transaction loop fully succeeds, so a mid-run failure retries cleanly on the next run. The summary state file works the same way - only marks a month as summarized after the email send succeeds.
- **Optional first-sync date filter:** ignore historical transactions on the very first run via `pipeline.start_date` in `config.yaml`. Doesn't affect any future run.
- **Single-process, no database, no server:** state lives in JSON / YAML files; runs as a single Python script on a free-tier GitHub Actions cron every 3 days at 3 AM Mountain Time.

---

## Tech stack

- **Language:** Python 3.10+ (developed on 3.14)
- **Banks:** [Plaid](https://plaid.com/) Production via `plaid-python` (`/transactions/sync` endpoint)
- **LLM:** Google Gemini 3.1 Flash Lite Preview via `google-genai` SDK (free tier, `thinking_budget=0` for speed)
- **Spreadsheet:** Google Sheets API v4 via `google-api-python-client` - uses the structured **Tables** feature so column layouts and TOTAL formulas survive duplication
- **Auth:** Google OAuth user-flow with combined Gmail + Sheets scopes (`google-auth-oauthlib`)
- **Bank linking UI:** one-time Flask page at `setup/link_banks.py` to run Plaid Link
- **Scheduler:** GitHub Actions cron (free for public repos)

---

## Architecture

```
                                ┌────────────────────┐
                                │   pipeline.py      │
                                │   (orchestrator)   │
                                └─────────┬──────────┘
                                          │
        ┌────────────────────┬────────────┼────────────┬────────────────────┐
        ▼                    ▼            ▼            ▼                    ▼
 ┌──────────────┐    ┌──────────────┐  ┌─────┐  ┌──────────────┐    ┌──────────────────┐
 │ clients/     │    │ clients/     │  │ ... │  │ clients/     │    │ clients/         │
 │ plaid_client │ tx │ gemini_client│  │     │  │ sheets_writer│    │ insights         │
 │ sync+cursor  │───>│ few-shot     │->│     │->│ tabs/tables/ │    │ monthly summary  │
 │              │    │ + retries    │  │     │  │ carryover    │    │ email + chart    │
 └──────────────┘    └──────────────┘  └─────┘  └──────────────┘    └──────────────────┘
        │                    │                          │                    │
        ▼                    ▼                          ▼                    ▼
 access_tokens.json    prompts.yaml              Google Sheet         Gmail + Gemini +
 (cursor state)        (~20 examples)            (your existing)      summary_state.json
```

Per transaction the orchestrator runs: **fetch → route by `account_id` → clean via Gemini → find/create month tab → find table by prefix → write to first empty row.** The carryover step runs once at tab creation, before any transaction is written. After the bank loop, `maybe_send_monthly_summary` checks state and (when the prior month hasn't been summarized) sends an HTML email summary with an embedded category pie chart.

---

## Local setup

### 1. Prerequisites

- Python 3.10+
- A [Plaid Developer account](https://dashboard.plaid.com/signup) (free Trial tier - 3 banks)
- A [Google Cloud project](https://console.cloud.google.com/) with the **Sheets API** enabled
- A [Gemini API key](https://aistudio.google.com/app/apikey) (free tier)
- An existing Google Sheet you'd like to write into, with at least one tab named `Template` containing the structured Tables you want populated

### 2. Clone and install

```bash
git clone https://github.com/<your-username>/Finance-Manager.git
cd Finance-Manager
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 3. Configure secrets

Copy the env template and fill in your keys:

```bash
cp .env.example .env
```

Edit `.env`:

```
PLAID_CLIENT_ID=<from Plaid dashboard>
PLAID_PRODUCTION_SECRET=<from Plaid dashboard>
PLAID_SANDBOX_SECRET=<optional, for testing>
GEMINI_API_KEY=<from AI Studio>
```

Place your Google OAuth credentials file (downloaded from Cloud Console) at the project root as `credentials.json`. The first time you run anything that needs Sheets, a browser window will open for the OAuth consent flow and `token.json` will be written for subsequent runs.

### 4. Configure your sheet

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:
- `sheet.spreadsheet_id` - copy from your Sheet's URL
- `sheet.template_tab` - name of the hidden template tab (default: `Template`)
- `tab_strategy.naming` - `spanish` / `english` / `numeric`
- `account_routing` - see step 5
- `balance_carryover` - optional; see "Balance carryover" below

`config.yaml` is **gitignored**, so your spreadsheet ID and routing stay private.

### 5. Link your banks

Run the one-time Plaid Link UI and follow each bank's OAuth flow:

```bash
python setup/link_banks.py
```

This opens a local web page that lets you authenticate one institution at a time. Tokens get appended to `access_tokens.json` (gitignored). Then dump the resulting account IDs:

```bash
python setup/inspect_accounts.py
```

Copy each `account_id` you want to track into `account_routing` in `config.yaml`, mapping it to your bank nickname plus the table prefixes for income / outflow tables in your sheet.

### 6. Run

```bash
python pipeline.py
```

You should see banks sync one at a time, transactions clean through Gemini, and rows appear in the correct month tab.

---

## Configuration

### `account_routing`

Maps Plaid `account_id` to your bank + sheet table prefixes:

```yaml
account_routing:
  "PLAID_ACCOUNT_ID_HERE":
    bank: "discover"
    income_table_prefix: "Discover_"
    outflow_table_prefix: "Discover_"   # same prefix → single-table mode (sign preserved)

  "PLAID_ACCOUNT_ID_HERE":
    bank: "sofi"
    income_table_prefix: "Sofi_Checkings_"
    outflow_table_prefix: "Gastos_Checkings_"   # different → two-table mode (abs amount)
```

When `income_table_prefix == outflow_table_prefix`, the transaction's sign is preserved (used for credit cards). When they differ, `abs(amount)` is used and the transaction routes to the appropriate table by sign.

### `balance_carryover`

Optional. Writes a starting-balance row at the top of each new month tab on creation:

```yaml
balance_carryover:
  - prefix: "Sofi_Checkings_"
    cell: "J21"                          # explicit cell to read in prior tab
    description: "Cuenta Checkings"

  - prefix: "Discover_"
    description: "{prev_month} Expenses"  # no cell → uses prior table's footer
```

`{prev_month}` is substituted with the prior month's tab name. If `cell` is absent, the prior month's same-prefix table is found and its footer Amount cell is read. Empty list (or omitted key) disables the feature.

### `pipeline`

```yaml
pipeline:
  start_date: "2026-05-01"   # one-time first-sync filter; ignored after cursor is set
  pacing_second: 0.5          # delay between Gemini calls
  max_tx_per_bank: null       # null = no cap; integer caps per run for dev
```

---

## Project structure

```
.
├── pipeline.py             # Orchestrator (entry point)
├── config.py               # Loads config.yaml at import
├── clients/
│   ├── plaid_client.py     # Plaid SDK + token / cursor I/O
│   ├── gemini_client.py    # Gemini wrapper with retries + fail-soft
│   ├── sheets_writer.py    # Tab creation, table rename, carryover, writes
│   ├── google_auth.py      # Shared OAuth helper (Gmail + Sheets scopes)
│   └── insights.py         # Monthly summary: gather, Gemini, chart URL, email
├── setup/
│   ├── link_banks.py       # One-time Flask page for Plaid Link
│   ├── inspect_accounts.py # Dumps account IDs per linked bank
│   └── hello_*.py          # Historical API smoke tests
├── tests/
│   ├── test_routing.py             # route_transaction sign handling
│   ├── test_sheets_helpers.py      # _col_letter, _prev_month_name, prefix gathering
│   └── test_insights.py            # summary state + categorize + email body + chart URL
├── .github/workflows/
│   └── cron.yml            # Daily GitHub Actions cron + state-file caching
├── prompts.yaml            # Gemini prompt templates + few-shot examples (cleaner + summary)
├── config.example.yaml     # Config template for forks
├── .env.example            # Env-var template for forks
├── requirements.txt
├── LICENSE
└── README.md
```

`config.yaml`, `.env`, `credentials.json`, `token.json`, `access_tokens.json`, `accounts.json`, and `summary_state.json` are all gitignored - secrets and personal IDs never get committed.

---

## Running tests

```bash
python -m unittest discover tests
```

34 tests covering routing, sheets helpers, and insights (state file round-trip, classification, chart URL, email body composition). Tests use stdlib `unittest` only - no extra dependency.

---

## Roadmap and known limitations

**Currently shipped:**
- Plaid → Gemini → Sheets pipeline runs end-to-end and is verified live in production
- Cursor-based incremental sync (idempotent reruns)
- Month-tab auto-creation with table rename + balance carryover
- Monthly summary email with category breakdown pie chart, structured Gemini output, and trend comparisons against prior-month history
- GitHub Actions cron deployment - runs every 3 days at 3 AM Mountain Time, fully unattended
- Cache-based state persistence so cursors + summary state survive between cron runs
- 34 passing unit tests

**Potential future work:**
- Per-transaction category labeling in the sheet itself (not just in the monthly summary)
- Per-table batching to reduce Sheets API chatter (currently ~3 metadata fetches per transaction)
- Cross-year carryover automation

**Limitations to be aware of:**
- Gemini Free Tier has 30 RPM and 500 RPD limits. Typical daily volume uses far less than that, but a heavy backfill could hit the per-minute cap.
- The Sheets API is chatty - ~3 calls per transaction. Fine for personal volumes (<100 tx/day); would need batching for higher volumes.
- The first call to Plaid `/transactions/sync` for a brand-new Item sometimes returns `added=[]` while Plaid does its background pull. Re-run after a minute and it catches up.
- Cross-year carryover (Diciembre 2026 → Enero 2027) requires manually filling the January carryover row by hand the first time, since each year's transactions live in a separate spreadsheet.
- GitHub Actions scheduled runs can drift by up to ~30 minutes during peak load (GitHub's own caveat). Not a problem for daily personal use.

---

## License

Released under the [MIT License](LICENSE).

---

## Contact

**Adrian Ninanya**

* **GitHub:** [NinyaDev](https://github.com/NinyaDev)
* **LinkedIn:** [Adrian Ninanya](https://www.linkedin.com/in/adrian-ninanya/)
