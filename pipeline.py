"""
    Pipeline file that acts as orchestrator connecting the LLM layer and Plaid's data retrieval.
    For each bank: 1) Fetch new transactions. 2) Run each through Gemini. 3) Print structured output to terminal.    
"""

import logging
import time
from googleapiclient.discovery import build
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from config import CONFIG
from clients.plaid_client import get_client, load_tokens, save_tokens
from clients.gemini_client import clean_description
from clients.google_auth import get_credentials
from clients.insights import maybe_send_monthly_summary
from clients.sheets_writer import (
    get_or_create_month_tab,
    find_table_in_tab,
    insert_transaction_into_table,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

log = logging.getLogger(__name__)

# Transactions to display for now
MAX_TX_PER_BANK = CONFIG["pipeline"].get("max_tx_per_bank")
START_DATE = CONFIG["pipeline"].get("start_date")
GEMINI_PACING = CONFIG["pipeline"].get("pacing_second", 0.5)  # seconds between Gemini calls to avoid rate limits

def fetch_recent_transactions(client, access_token, cursor=""):
    # Call plaid /transactions/sync for one Item
    all_added =[]
    has_more = True
    while has_more:
        request = TransactionsSyncRequest(
            access_token=access_token,
            cursor= cursor)
    
        response = client.transactions_sync(request)
        all_added.extend(response.added)
        cursor = response.next_cursor
        has_more = response.has_more
    return all_added, cursor

def route_transaction(tx, account_routing):
    # Look up routing config for a transaction
    account_id = tx.account_id
    if account_id not in account_routing:
        return None
    
    routing = account_routing[account_id]
    income_prefix = routing["income_table_prefix"]
    outflow_prefix = routing["outflow_table_prefix"]
    
    # Plaid convention: positive amount is outflow and negative is income
    if tx.amount > 0:
        table_prefix = outflow_prefix
        direction = "outflow"
    else: 
        table_prefix = income_prefix
        direction = "income"
        
    # Single table mode
    if income_prefix == outflow_prefix:
        amount = tx.amount
    else:
        amount = abs(tx.amount)
    
    return routing["bank"], table_prefix, direction, amount

def main():
    plaid_client = get_client(env="production")
    creds = get_credentials()
    sheets_service = build("sheets", "v4", credentials=creds)
    
    spreadsheet_id = CONFIG["sheet"]["spreadsheet_id"]
    account_routing = CONFIG["account_routing"]
    tokens = load_tokens()
    
    if not tokens:
        log.error("No access tokens found. Please run setup/link_banks.py first.")
        raise SystemExit(1)
    
    log.info(f"Pipeline starting - banks: {list(tokens.keys())}")
    print()
    
    total_processed = 0
    total_skipped = 0
    
    for nickname, token_data in tokens.items():
        access_token = token_data["access_token"]
        prior_cursor = token_data.get("cursor", "")
        log.info(f"Syncing {nickname} (cursor={'<empty, first run>' if not prior_cursor else 'set'})...")
        
        try:
            transactions, new_cursor = fetch_recent_transactions(plaid_client, access_token, prior_cursor)
            # Checking if there's no cursor and there is a START DATE (ONETIME)
            if not prior_cursor and START_DATE:
                before = len(transactions)
                transactions = [t for t in transactions if str(t.date) >= START_DATE]
                log.info(f"First-sync filter: kept {len(transactions)}/{before} tx >= {START_DATE}")
            
            # First within this batch
            transactions.sort(key=lambda t: t.date, reverse=True)
            log.info(f"Fetched {len(transactions)} new transactions showing for {nickname}")
            
            tx_slice = transactions
            
            for tx in tx_slice:
                # 1. Routing
                routing = route_transaction(tx, account_routing)
                if routing is None:
                    log.info(f" Skipping unmapped account_id={tx.account_id} (vault?)")
                    total_skipped +=1
                    continue
                bank, table_prefix, direction, amount = routing
                
                #2. Description with Gemini
                tx_date = str(tx.date)
                cleaned = clean_description(merchant = tx.name, amount = tx.amount, account = nickname, date = tx_date)
                time.sleep(GEMINI_PACING)
                
                #3. Get/create month tab - uses tx.date, not today's date
                tab = get_or_create_month_tab(sheets_service, spreadsheet_id, tx_date)
                
                # 4. Find the right table
                table = find_table_in_tab(sheets_service, spreadsheet_id, tab["sheetId"], table_prefix) 
                
                # 5. Write to table
                row = insert_transaction_into_table(sheets_service, spreadsheet_id, table, description = cleaned, amount = amount)
                
                log.info(
                    f" DONE {tx_date}{direction:7s} ${amount:>9.2f}"
                    f" -> {tab['title']}/{table['name']} row{row}"
                )
                total_processed +=1
        
            # Save cursor only after the bank's full tx loops succeeds.
            # If anything raises, keep old cursor.
            tokens[nickname]["cursor"] = new_cursor
            save_tokens(tokens)
            log.info(f"Saved cursor for {nickname}")
            
        except Exception:
            log.exception(f"Error processing {nickname}")
        print()
        
    log.info(f"Pipeline done. wrote: {total_processed} tx."
             f" skipped {total_skipped} across {len(tokens)} banks.")

    # Monthly summary attempt - non-critical; failures must not crash the pipeline.
    # The orchestrator self-recovers by retrying on the next daily run.
    try:
        maybe_send_monthly_summary(creds)
    except Exception:
        log.exception("Monthly summary failed (pipeline continues normally)")


if __name__ == "__main__":
    main()