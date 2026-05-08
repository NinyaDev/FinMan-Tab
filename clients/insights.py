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
    for spec in CONFIG_get

if __name__ == "__main__":
    # Smoke test
    logging.basicConfig(level = logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    creds = get_credentials()
    gmail = build("gmail", "v1", credentials=creds)
    
    profile = gmail.users().getProfile(userId="me").execute()
    my_email = profile["emailAddress"]
    log.info(f"Authenticated as {my_email}")
    
    msg_id = send_email(gmail_service = gmail, to = my_email, sender = my_email, subject="Finance-Manager", html_body="<h2>HELLO FROM FINANCE MANAGER</h2>")
    log.info(f"Sent. Message ID: {msg_id}")