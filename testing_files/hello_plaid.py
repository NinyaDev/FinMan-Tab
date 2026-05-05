"""
Testing plaid in order to check that it works. SANITY CHECK
 Create a fake bank item. Exchange public token for access token.
 Fetch transcations and print.
"""

import os
import time
from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.api_client import ApiClient
from plaid.configuration import Configuration
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

load_dotenv()

PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID')
PLAID_SANDBOX_SECRET = os.getenv('PLAID_SANDBOX_SECRET')

# Sandbox = fake banks/transactions.
# We point at sandbox for testing

configuration = Configuration(
    host="https://sandbox.plaid.com",
    api_key={
        'clientId': PLAID_CLIENT_ID,
        'secret': PLAID_SANDBOX_SECRET,
    },)

api_client = ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

def main():
    # Create a sandbox public token for a fake bank item
    pt_request = SandboxPublicTokenCreateRequest(
        institution_id="ins_109508",
        initial_products=[Products('transactions')],)
    
    pt_response = client.sandbox_public_token_create(pt_request)
    public_token = pt_response['public_token']
    
    # Exchange the public token for an access token
    exch_request = ItemPublicTokenExchangeRequest(public_token=public_token)
    exch_response = client.item_public_token_exchange(exch_request)
    access_token = exch_response['access_token']
    print(f"Access token: {access_token[:25]}...")
    
    # Sync transactions for the item
    for attempt in range(10):      
        sync_request = TransactionsSyncRequest(access_token=access_token)
        sync_response = client.transactions_sync(sync_request)
        if sync_response['added']:
            break
        print("No transactions yet, retrying...")
        time.sleep(2)
    else:
        print("No transactions found after 10 attempts.")
        return
    
    print(f"\nFound {len(sync_response.added)} transactions:")
    for tx in sync_response.added[:10]:
        print(f"- {tx.name}: ${tx.amount} on {tx.date}")
        
if __name__ == "__main__":
    main()