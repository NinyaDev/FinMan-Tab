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
                "visibility": "VISIBLE"
            }
        }]
    }
    response = service.spreadsheets().batchUpdate(spreadsheetId = spreadsheet_id, body = request_body).execute()
    
    new_props = response["replies"][0]["duplicateSheet"]["properties"]
    log.info(f"Created tab '{target_name}' (sheet_id={new_props['sheetId']})")
    return {"title": new_props["title"], "sheetId": new_props["sheetId"]}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    creds = get_credentials()
    
    service = build('sheets', 'v4', credentials=creds)
    spreadsheet_id = CONFIG["sheet"]["spreadsheet_id"]
    # Test with today's date
    test_date = "2026-05-06"
    result = get_or_create_month_tab(service, spreadsheet_id, test_date)
    print(f"Got tab: {result}")
    
    # Test again. should not create duplicate
    result2 = get_or_create_month_tab(service, spreadsheet_id, test_date)
    print(f"Got tab: {result2}")
    
    # Test again with different date
    result3 = get_or_create_month_tab(service, spreadsheet_id, "2026-06-15")
    print(f"Got tab: {result3}")