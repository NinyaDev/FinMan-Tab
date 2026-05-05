"""
 Testing the just retrieved accounts and checking their items. Linked under each Plaid item.
 
 Run after linking banks. Re-run if you add a new bank or detect missing accounts.
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from plaid.api import plaid_api
from plaid.api_client import ApiClient
from plaid.configuration import Configuration
from plaid.model.accounts_get_request import AccountsGetRequest

logging.basicConfig(level=logging.INFO,
                    format = '%(asctime)s - %(levelname)s - %(message)s',
                    datefmt = '%Y-%m-%d %H:%M:%S',)
log = logging.getLogger(__name__)

load_dotenv()

PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID')
PLAID_PRODUCTION_SECRET = os.getenv('PLAID_PRODUCTION_SECRET')

if not PLAID_CLIENT_ID or not PLAID_PRODUCTION_SECRET:
    log.error("PLAID_CLIENT_ID and PLAID_PRODUCTION_SECRET must be set in .env file")
    raise SystemExit(1)

configuration = Configuration(
    host = "https://production.plaid.com",
    api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_PRODUCTION_SECRET,},)
client = plaid_api.PlaidApi(ApiClient(configuration))

TOKENS_FILE = Path('access_tokens.json')
ACCOUNTS_FILE = Path('accounts.json')

def fetch_accounts_for_item(nickname, access_token):
    # Call Plaid /accounts/get for one Item and returns a list of accounts.
    request = AccountsGetRequest(access_token=access_token)
    response = client.accounts_get(request)

    accounts = []
    
    for acc in response.accounts:
        accounts.append({
            "account_id": acc.account_id,
            "name": acc.name, 
            "official_name": acc.official_name,
            "type": str(acc.type),
            "subtype": str(acc.subtype),
            "mask": acc.mask,
        })
    return accounts

def main():
    if not TOKENS_FILE.exists() or TOKENS_FILE.stat().st_size == 0:
        log.error(f"{TOKENS_FILE} not found or empty. Please link a bank first.")
        raise SystemExit(1)
    
    tokens = json.loads(TOKENS_FILE.read_text())
    log.info(f"Found {len(tokens)} access tokens: {list(tokens.keys())}")
    print(list(tokens.keys()))
    
    all_accounts = {}
    
    for nickname, token_data in tokens.items():
        access_token = token_data
        log.info(f"Fetching accounts for {nickname}...")
        try:
            accounts = fetch_accounts_for_item(nickname, access_token)
            all_accounts[nickname] = accounts
            log.info(f"Found {len(accounts)} accounts for {nickname}.")
            for acc in accounts:
                print(f"  - {acc['name']} ({acc['type']}/{acc['subtype']}, mask: {acc['mask']})")
                print(f"    Account ID: {acc['account_id']}")
        except Exception as e:
            log.error(f"Error fetching accounts for {nickname}: {e}")
    ACCOUNTS_FILE.write_text(json.dumps(all_accounts, indent=2))
    log.info(f"All accounts saved to {ACCOUNTS_FILE}")

if __name__ == "__main__":
    main()