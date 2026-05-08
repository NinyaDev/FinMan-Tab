"""

Google sheets writer for the finance pipeline.

"""

import logging
from datetime import datetime
from googleapiclient.discovery import build
from config import CONFIG
from google_auth import get_credentials

log = logging.getLogger(__name__)

SPANISH_MONTHS = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre"
}
ENGLISH_MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December"
}
def _col_letter(idx_0: int) -> str:
    # Convert 0-indexed column to letters
    result = ""
    idx = idx_0+1
    while idx > 0:
        idx, remainder = divmod(idx -1, 26)
        result = chr(65+remainder) + result
    return result

def _month_name_for_date(date: str) -> str:
    # Convert a date string (YYYY-MM-DD) to a month name based on config
    dt = datetime.strptime(date, "%Y-%m-%d")
    naming = CONFIG["tab_strategy"]["naming"]
    if naming == "spanish":
        return SPANISH_MONTHS[dt.month]
    elif naming == "english":
        return ENGLISH_MONTHS[dt.month]
    elif naming == "numeric":
        return f"{dt.year}-{dt.month:02d}"
    else:
        raise ValueError(f"Unknown naming strategy: {naming}")

def _prev_month_name(date_str: str) -> str:
    # Month name for the month BEFORE the given date
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.month == 1:
        prev_dt = dt.replace(year=dt.year - 1, month=12, day=1)
    else:
        prev_dt = dt.replace(month=dt.month - 1, day = 1)
    return _month_name_for_date(prev_dt.strftime("%Y-%m-%d"))

def _get_sheet_meta(service, spreadsheet_id: str, sheet_id: int) -> dict:
    # Return the sheet object (properties, tables, etc.) for a given sheet_id.
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in metadata["sheets"]:
        if sheet["properties"]["sheetId"] == sheet_id:
            return sheet
    raise RuntimeError(f"Sheet ID {sheet_id} not found in spreadsheet")

def _all_configured_prefixes() -> set:
    # Unique table prefixes from account_routing config.
    prefixes = set()
    for entry in CONFIG["account_routing"].values():
        prefixes.add(entry["income_table_prefix"])
        prefixes.add(entry["outflow_table_prefix"])
    return prefixes

def _rename_tables_with_month(service, spreadsheet_id: str, sheet_id: int, month_name: str) -> None:
    # Rename each table in this sheet to <prefix><month_name>. Idempotent.
    sheet = _get_sheet_meta(service, spreadsheet_id, sheet_id)
    tables = sheet.get("tables", [])
    prefixes = sorted(_all_configured_prefixes(), key=len, reverse=True)

    requests = []
    for table in tables:
        for prefix in prefixes:
            if table["name"].startswith(prefix):
                new_name = f"{prefix}{month_name}"
                if table["name"] != new_name:
                    requests.append({
                        "updateTable": {
                            "table": {"tableId": table["tableId"], "name": new_name},
                            "fields": "name",
                        }
                    })
                break

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
        log.info(f"Renamed {len(requests)} tables in '{month_name}' tab")

def _carry_over_balances(service, spreadsheet_id: str, new_sheet_id: int, date_str: str) -> None:
    # Carry prior month balances into newly created tab per Config
    specs = CONFIG.get("balance_carryover") or []
    if not specs:
        return
    
    prev_month = _prev_month_name(date_str)
    
    metadata = service.spreadsheets().get(spreadsheetId = spreadsheet_id).execute()
    prior_sheet_id = next(
        (s["properties"]["sheetId"] for s in metadata["sheets"]
         if s["properties"]["title"] ==prev_month), None
    )
    if prior_sheet_id is None:
        log.info(f"No prior month tab '{prev_month}' - skipping carryover")
        return
    
    for spec in specs:
        try:
            _apply_carryover_spec(service, spreadsheet_id, spec, prev_month, prior_sheet_id, new_sheet_id)
        except Exception:
            log.warning(f"Carryover failed for {spec.get('prefix','?')}", exc_info=True)

def _apply_carryover_spec(service, spreadsheet_id, spec, prev_month, prior_sheet_id, new_sheet_id):
    prefix = spec["prefix"]
    description = spec["description"].format(prev_month=prev_month)
    
    # Source range: explicit cell, or prior table's footer amount cell
    if "cell" in spec:
        source_range = f"'{prev_month}'!{spec['cell']}"
    else:
        prior_table = find_table_in_tab(service, spreadsheet_id, prior_sheet_id, prefix)
        footer_row = prior_table["range"]["endRowIndex"]
        amount_col = _col_letter(prior_table["range"]["startColumnIndex"] +1)
        source_range = f"'{prev_month}'!{amount_col}{footer_row}"

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=source_range,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    
    values = result.get("values", [])
    if not values or not values[0] or values[0][0] in ("", None):
        log.info(f"Carryover source {source_range} is empty - skipping {prefix}")
        return
    amount = round(float(values[0][0]), 2)
    dest_table = find_table_in_tab(service, spreadsheet_id, new_sheet_id, prefix)
    insert_transaction_into_table(service, spreadsheet_id, dest_table, description, amount)
    log.info(f"Carryover: '{description}' ${amount:.2f} -> {dest_table['name']}")

def get_or_create_month_tab(service, spreadsheet_id: str, date: str) -> dict:
    # If the tab already exists, return it. Otherwise duplicate template and rename duplicate to month name
    
    target_name = _month_name_for_date(date)
    template_name = CONFIG["sheet"]["template_tab"]
    
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = metadata["sheets"]
    
    # Look for existing tab with target name
    for sheet in sheets:
        props = sheet["properties"]
        if props["title"] == target_name:
            logging.info(f"Found existing tab for {target_name}")
            return {"title": props["title"], "sheetId": props["sheetId"]}
    # Find the Template tab to duplicate from.
    template_id = None
    for sheet in sheets:
        if sheet["properties"]["title"] == template_name:
            template_id = sheet["properties"]["sheetId"]
            break
    if template_id is None:
        raise RuntimeError(f"Template tab '{template_name}' not found in spreadsheet.")
    
    log.info(f"Tab '{target_name}' not found, duplicating from template")
    request_body = {
        "requests": [{
            "duplicateSheet": {
                "sourceSheetId": template_id,
                "newSheetName": target_name,
                # Place tab after the last existing tab
                "insertSheetIndex": len(sheets),
            }
        }]
    }
    response = service.spreadsheets().batchUpdate(spreadsheetId = spreadsheet_id, body = request_body).execute()

    new_props = response["replies"][0]["duplicateSheet"]["properties"]
    new_sheet_id = new_props["sheetId"]

    # Template is hidden -> duplicate inherits hidden. Force the new tab visible.
    visibility_request = {
        "requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": new_sheet_id, "hidden": False},
                "fields": "hidden",
            }
        }]
    }
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body=visibility_request,
    ).execute()

    try:
        _rename_tables_with_month(service, spreadsheet_id, new_sheet_id, target_name)
    except Exception:
        log.warning(f"Failed to rename tables in '{target_name}' (continuing)", exc_info=True)

    try:
        _carry_over_balances(service, spreadsheet_id, new_sheet_id, date)
    except Exception:
        log.warning(f"Failed to carry over balances for '{target_name}' (continuing)", exc_info=True)

    log.info(f"Created tab '{target_name}' (sheet_id={new_sheet_id}) and made visible")
    return {"title": new_props["title"], "sheetId": new_sheet_id}

def find_table_in_tab(service, spreadsheet_id: str, sheet_id: int, table_name_prefix: str) -> dict:
    # Find a table within a specific tab by name prefix.
    sheet = _get_sheet_meta(service, spreadsheet_id, sheet_id)
    tables = sheet.get("tables", [])
    for table in tables:
        if table["name"].startswith(table_name_prefix):
            log.info(f"Found table '{table['name']}' matching prefix '{table_name_prefix}'")
            return table
    raise RuntimeError(f"No table starting with '{table_name_prefix}' in sheet {sheet_id}")
    

def insert_transaction_into_table(service, spreadsheet_id: str, table: dict, description: str, amount: float) -> int:
    """Write transaction to first empty row in table; returns 1-indexed row written."""
    sheet_id = table["range"]["sheetId"]
    start_row = table["range"]["startRowIndex"]
    end_row = table["range"]["endRowIndex"]
    start_col = table["range"]["startColumnIndex"]

    sheet = _get_sheet_meta(service, spreadsheet_id, sheet_id)
    tab_name = sheet["properties"]["title"]

    data_start = start_row + 2
    data_end = end_row - 1

    desc_col = _col_letter(start_col)
    amount_col = _col_letter(start_col + 1)
    range_to_read = f"'{tab_name}'!{desc_col}{data_start}:{desc_col}{data_end}" # This is like 'Mayo'!C12:C23
    
    # Return values based on the range
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_to_read,
    ).execute()
    values = result.get("values", [])

    target_row = None
    for i in range(data_end - data_start + 1):
        if i >= len(values) or not values[i] or not values[i][0].strip():
            target_row = data_start + i
            break

    if target_row is None:
        raise RuntimeError(f"Table '{table['name']}' has no empty rows")

    target_range = f"'{tab_name}'!{desc_col}{target_row}:{amount_col}{target_row}" # 'Mayo'!C12D12
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=target_range,
        valueInputOption="USER_ENTERED",
        body={"values": [[description, amount]]},
    ).execute()

    log.info(f"Wrote '{description}' (${amount}) to {tab_name}!{desc_col}{target_row}")
    return target_row

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    creds = get_credentials()
    
    service = build('sheets', 'v4', credentials=creds)
    spreadsheet_id = CONFIG["sheet"]["spreadsheet_id"]
    # Test with today's date
    mayo = get_or_create_month_tab(service, spreadsheet_id, "2026-05-06")
    print(f"\nLooking up tables in '{mayo['title']}' (sheet_id={mayo['sheetId']}):\n")
    
    for prefix in sorted(_all_configured_prefixes()):
        try:
            table = find_table_in_tab(service, spreadsheet_id, mayo["sheetId"], prefix)
            r = table["range"]
            print(
                f"  {prefix:25s} → {table['name']:30s} "
                f"rows {r['startRowIndex']}-{r['endRowIndex']}  "
                f"cols {r['startColumnIndex']}-{r['endColumnIndex']}"
            )
        except RuntimeError as e:
            print(f"  {prefix:25s} → NOT FOUND ({e})")

    # Phase 2.3c test: insert a fake transaction into Gastos_Checkings
    print("\n--- Inserting test transaction ---")
    test_table = find_table_in_tab(service, spreadsheet_id, mayo["sheetId"], "Gastos_Checkings_")
    row = insert_transaction_into_table(
        service, spreadsheet_id, test_table,
        description="Test - delete me",
        amount=1.23,
    )
    print(f"\n Wrote test transaction to row {row}. Open Mayo and verify, then delete it.")

