"""
 Shared Plaid client factory for token account loaders.
 Centralized "library" for Plaid API interactions, to be used by various account loaders.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.api_client import ApiClient
from plaid.configuration import Configuration

load_dotenv()

log = logging.getLogger(__name__)

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_PRODUCTION_SECRET = os.getenv("PLAID_PRODUCTION_SECRET")
PLAID_SANDBOX_SECRET = os.getenv("PLAID_SANDBOX_SECRET")

TOKENS_FILE = Path("access_tokens.json")
ACCOUNTS_FILE = Path("accounts.json")

def get_client(env: str="production"):
    # Return a Plaid client configured for the given environment
    # Defaults to production, but can be set to "sandbox" for testing.
    if env == "production":
        host = "https://production.plaid.com"
        secret = PLAID_PRODUCTION_SECRET
    elif env == "sandbox":
        host = "https://sandbox.plaid.com"
        secret = PLAID_SANDBOX_SECRET
    else:
        raise ValueError(f"Invalid environment: {env}. Must be 'production' or 'sandbox'.")
    
    if not PLAID_CLIENT_ID or not secret:
        log.error(f"Missing PLAID_CLIENT_ID or {env} secret in .env")
        raise SystemExit(1)
    
    config = Configuration(
        host = host,
        api_key = {
            "clientId": PLAID_CLIENT_ID, "secret": secret}
    )
    return plaid_api.PlaidApi(ApiClient(config))

# JSON file loaders

def load_tokens() -> dict:
    # Load access tokens from JSON file
    if not TOKENS_FILE.exists() or TOKENS_FILE.stat().st_size == 0:
        log.error(f"{TOKENS_FILE} not found or empty.")
        return {}
    return json.loads(TOKENS_FILE.read_text())

def save_tokens(tokens: dict) -> None:
    # Persist tokens dict (overwrite file)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
    

def load_accounts() -> dict:
    # Load accounts.json into a dict
    if not ACCOUNTS_FILE.exists() or ACCOUNTS_FILE.stat().st_size == 0:
        log.error(f"{ACCOUNTS_FILE} not found or empty.")
        return {}
    return json.loads(ACCOUNTS_FILE.read_text())