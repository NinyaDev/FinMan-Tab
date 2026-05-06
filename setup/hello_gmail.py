'''
Authenticates with Gmail API and Prints all GMAIL Labels. First run opens browser for consent and subsequent runs use token.json
'''

import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scopes = permissions for GMAIL API
# readonly and modify
# Request both upfront

SCOPES =['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.modify']
CREDENTIALS_FILE = 'credentials.json' # downloaded from Google Cloud Console
TOKEN_FILE = 'token.json' # stores access and refresh tokens

def get_credentials():
    # Return credentials object, prompting via browser if needed
    creds = None
    
    # If token.json exists, load credentials from it
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    # If no valid credentials, prompt user to login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            #Silent refresh without browser
            creds.refresh(Request())
        else:
            # First time open the browser
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return creds

def main():
    #List Gmail labels
    try:
        creds = get_credentials()
        
        service = build('gmail', 'v1', credentials=creds)
        # users().labels().list() returns a list of label objects
        # userId='me' refers to the authenticated user
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        
        if not labels:
            print('No labels found.')
            return
        
        print(f"Found {len(labels)} labels:")
        for label in labels:
            print(f"- {label['name']}")
    
    except HttpError as error:
        print(f'An error occurred: {error}')
        
        
if __name__ == '__main__':
    main()