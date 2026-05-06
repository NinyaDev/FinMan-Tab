"""
Verification for Sheets, making sure it works.
Authenticates with combined Gmail + Sheet Scopes.
"""

import os
import os.path
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

# Combined Scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/spreadsheets'
]

CREDENTIALS_FILE = 'credentials.json' # downloaded from Google Cloud Console
TOKEN_FILE = 'token.json'
TEST_SHEETS_ID = os.getenv("TEST_SHEETS_ID")

def get_credentials():                                                                                                                                     
    """Get valid OAuth credentials, prompting via browser only if needed."""                                                                               
    creds = None                                                                                                                                           
    if os.path.exists(TOKEN_FILE):                            
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)                                                                                  
                                            
    if not creds or not creds.valid:              
        if creds and creds.expired and creds.refresh_token:                                                                                                
            creds.refresh(Request())                          
        else:                                                                                                                                              
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:                                                                                                                   
            f.write(creds.to_json())              
    return creds

def main():
    if not TEST_SHEETS_ID:
        print("Missing TEST_SHEETS_ID in .env")
        return
    try:
        creds = get_credentials()
        service = build('sheets', 'v4', credentials=creds)
        
        result = service.spreadsheets().get(spreadsheetId=TEST_SHEETS_ID).execute()
        
        title = result["properties"]["title"]
        sheets = result["sheets"]
        
        print(f"Connected to {title}")
        print(f"Found {len(sheets)} tabs:")
        
        for sheet in sheets:
            props = sheet["properties"]
            tab_name = props["title"]
            tab_id = props["sheetId"]
            print(f"- {tab_name} (ID: {tab_id})")
    except HttpError as error:
        print(f"An error occurred: {error}")

if __name__ == "__main__":
    main()