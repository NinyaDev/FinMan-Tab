"""
    Pipeline file that acts as orchestrator connecting the LLM layer and Plaid's data retrieval.
    For each bank: 1) Fetch new transactions. 2) Run each through Gemini. 3) Print structured output to terminal.    
"""

import logging
import time
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from plaid_client import get_client, load_tokens
from gemini_client import clean_description

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

log = logging.getLogger(__name__)

# Transactions to display for now
MAX_TX_PER_BANK = 5
GEMINI_PACING = 0.5  # seconds between Gemini calls to avoid rate limits

def fetch_recent_transactions(client, access_token):
    # Call plaid /transactions/sync for one Item
    request = TransactionsSyncRequest(access_token=access_token)
    response = client.transactions_sync(request)
    return response.added

def main():
    plaid_client = get_client(env="production")
    tokens = load_tokens()
    
    if not tokens:
        log.error("No access tokens found. Please run testing_files/link_banks.py first.")
        raise SystemExit(1)
    
    log.info(f"Pipeline starting - banks: {list(tokens.keys())}")
    print()
    
    total_processed = 0
    
    for nickname, token_data in tokens.items():
        access_token = token_data["access_token"]
        log.info(f"Fetching transactions for {nickname}...")
        
        try:
            transactions = fetch_recent_transactions(plaid_client, access_token)
            log.info(f"Fetched {len(transactions)} transactions showing first {MAX_TX_PER_BANK}...")
            
            for tx in transactions[:MAX_TX_PER_BANK]:
                cleaned = clean_description(merchant = tx.name, amount = tx.amount, account = nickname, date = str(tx.date))
                
                # Plaid convention: positive amount = outflow, negative is income
                direction = "OUT" if tx.amount > 0 else "IN"
                print(f" [{nickname:10s}] {tx.date} {direction}"
                      f" ${abs(tx.amount):8.2f}"
                      f" {tx.name[:40]:40s} → {cleaned}")
                total_processed += 1
                time.sleep(GEMINI_PACING)
        except Exception:
            log.exception(f"Error processing transactions for {nickname}")
        print()
        
    log.info(f"Pipeline done. Total transactions processed: {total_processed} for {len(tokens)} banks.")

if __name__ == "__main__":
    main()