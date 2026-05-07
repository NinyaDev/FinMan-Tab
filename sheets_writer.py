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

    log.info(f"Created tab '{target_name}' (sheet_id={new_sheet_id}) and made visible")
    return {"title": new_props["title"], "sheetId": new_sheet_id}

def find_table_in_tab(service, spreadsheet_id: str, sheet_id: int, table_name_prefix: str) -> dict:
    # Find a table withing a specific tab by name prefix.
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id, includeGridData=False).execute()
    
    target_sheet = None
    for sheet in metadata["sheets"]:
        if sheet["properties"]["sheetId"] == sheet_id:
            target_sheet = sheet
            break
    if target_sheet is None:
        raise RuntimeError(f"Sheet with ID {sheet_id} not found in spreadsheet.")
    
    tables = target_sheet.get("tables", [])
    for table in tables:
        if table["name"].startswith(table_name_prefix):
            log.info(f"Found table '{table['name']}'"
                     f" Matching prefix '{table_name_prefix}'")
            return table
    raise RuntimeError(f"No table starting with '{table_name_prefix}' in sheet {sheet_id}")
    

def insert_transaction_into_table(service, spreadsheet_id: str, table: dict, description: str, amount: float) -> int:
    """Write transaction to first empty row in table; returns 1-indexed row written."""
    sheet_id = table["range"]["sheetId"]
    start_row = table["range"]["startRowIndex"]
    end_row = table["range"]["endRowIndex"]
    start_col = table["range"]["startColumnIndex"]

    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tab_name = next(
        (s["properties"]["title"] for s in metadata["sheets"]
         if s["properties"]["sheetId"] == sheet_id),
        None,
    )
    if tab_name is None:
        raise RuntimeError(f"Sheet ID {sheet_id} not found")

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
    
    routing = CONFIG["account_routing"]
    # Collect unique prefixes
    prefixes = set()
    for entry in routing.values():
        prefixes.add(entry["income_table_prefix"])
        prefixes.add(entry["outflow_table_prefix"])
    
    for prefix in sorted(prefixes):
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

