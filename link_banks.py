"""
One time Plaid Link UI to connect to real banks.
This is for running the Oauth for each of the banks we want to link and this should an access token we store to call
the program later. Once per bank    
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from plaid.api import plaid_api                                                                                                             
from plaid.api_client import ApiClient                 
from plaid.configuration import Configuration
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest                                              
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

logging.basicConfig(level=logging.INFO,
                     format = '%(asctime)s - %(levelname)s - %(message)s',
                     datefmt = '%Y-%m-%d %H:%M:%S',)
log = logging.getLogger(__name__)

load_dotenv()

PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID')
PLAID_PRODUCTION_SECRET = os.getenv('PLAID_PRODUCTION_SECRET')

if not PLAID_CLIENT_ID or not PLAID_PRODUCTION_SECRET:
    log.error("PLAID_CLIENT_ID and PLAID_PRODUCTION_SECRET must be set in .env")
    raise SystemExit(1)

# Production = real banks, real tokens
configuration = Configuration(
    host="https://production.plaid.com",
    api_key={
        'clientId': PLAID_CLIENT_ID,
        'secret': PLAID_PRODUCTION_SECRET,
    },)
client = plaid_api.PlaidApi(ApiClient(configuration))

TOKENS_FILE = Path('access_tokens.json')

app = Flask(__name__)

@app.route("/")
def home():
    # Page that hosts Plaid Link
    return HTML_PAGE

@app.route("/api/create_link_token", methods=["POST"])
def create_link_token():
    # Generate a one-time link token for Plaid Link
    try:
        link_request = LinkTokenCreateRequest(
            products = [Products('transactions')],
            client_name = "Finance Manager",
            country_codes = [CountryCode('US')],
            language = 'en',
            user = LinkTokenCreateRequestUser(client_user_id="finance-manager-user"),)
        
        response = client.link_token_create(link_request)
        log.info("Link token created successfully")
        return jsonify({"link_token": response['link_token']})
    except Exception:
        log.exception("Error creating link token")
        return jsonify({"error": "Failed to create link token"}), 500
    
@app.route("/api/exchange_public_token", methods=["POST"])
def exchange_public_token():
    # Exchange the public token and nickame for a persistent access token
    try:
        body = request.get_json()
        public_token = body['public_token']
        nickname = body["nickname"]
        log.info(f"Received public token for {nickname}")
        
        exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
        exchange_response = client.item_public_token_exchange(exchange_request)
        access_token = exchange_response['access_token']
        item_id = exchange_response.item_id
        
        # Load existing tokens, add new one and save
        tokens = {}
        if TOKENS_FILE.exists() and TOKENS_FILE.stat().st_size > 0:
            tokens = json.loads(TOKENS_FILE.read_text())
        tokens[nickname] = {"access_token": access_token, "item_id": item_id}
        TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
        log.info(f"Access token for {nickname} saved successfully to {TOKENS_FILE}")
        
        return jsonify({"status": "ok", "nickname": nickname})
    except Exception:
        log.exception("Error exchanging public token")
        return jsonify({"error": "Failed to exchange public token"}), 500
HTML_PAGE = """<!DOCTYPE html>                                                                                                              
  <html>                  
  <head>                                                                                                                                      
    <title>Link a Bank - Finance Manager</title>                                                                                              
    <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
    <style>                                                                                                                                   
      body { font-family: -apple-system, sans-serif; padding: 40px; max-width: 500px; margin: auto; }
      button { padding: 12px 24px; font-size: 16px; cursor: pointer; }
      input { padding: 8px; font-size: 16px; width: 100%; margin: 12px 0; box-sizing: border-box; }
      .status { margin-top: 24px; padding: 12px; border-radius: 8px; }                                                                        
      .ok { background: #e8f5e9; color: #2e7d32; }           
      .err { background: #ffebee; color: #c62828; }                                                                                           
    </style>                                                 
  </head>                                                                                                                                     
  <body>                                                     
    <h1>Link a Bank Account</h1>                                                                                                              
    <p>Type a nickname (e.g. <code>discover</code>, <code>sofi_checking</code>, <code>onepay</code>),
       then click Link. After linking, refresh and link the next bank.</p>                                                                    
                                                    
    <input id="nickname" placeholder="bank nickname" autocomplete="off" />                                                                    
    <button id="link-btn">Link</button>                         
    <div id="status"></div>                                                                                                                   
                                                                
    <script>                                 
      const statusEl = document.getElementById("status");                                                                                     
      const setStatus = (msg, cls) => statusEl.innerHTML = `<div class="status ${cls}">${msg}</div>`;
                                                                                                                                              
      document.getElementById("link-btn").onclick = async () => {
        const nickname = document.getElementById("nickname").value.trim();                                                                    
        if (!nickname) { setStatus("Please enter a nickname", "err"); return; }
                                                                                                                                              
        // 1. Get a link_token from our backend
        let link_token;                                                                                                                       
        try {                                                
          const r = await fetch("/api/create_link_token", { method: "POST" });                                                                
          const data = await r.json();
          if (data.error) throw new Error(data.error);                                                                                        
          link_token = data.link_token;             
        } catch (e) {                                                                                                                         
          setStatus(`Failed to create link token: ${e.message}`, "err");
          return;                                                                                                                             
        }                                           
                                                             
        // 2. Open Plaid Link                                                                                                                 
        const handler = Plaid.create({
          token: link_token,                                                                                                                  
          onSuccess: async (public_token, metadata) => {
            // 3. Send public_token + nickname back to our backend                                                                            
            try {                                   
              const r = await fetch("/api/exchange_public_token", {                                                                           
                method: "POST",                                 
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ public_token, nickname }),
              });                                            
              const data = await r.json();                                                                                                    
              if (data.error) throw new Error(data.error);
              setStatus(`✅ Linked <b>${nickname}</b> successfully. Refresh to link another bank.`, "ok");                                    
            } catch (e) {                
              setStatus(`Exchange failed: ${e.message}`, "err");                                                                              
            }                                       
          },                                                                                                                                  
          onExit: (err, metadata) => {                          
            if (err) setStatus(`Plaid Link error: ${err.display_message || err.error_message}`, "err");
          },   
        });
        handler.open();                                                                                                                       
      };
    </script>                                                                                                                                 
  </body>                                
  </html>                 
  """

if __name__ == "__main__":
    log.info("Starting Flask server on http://localhost:8000")
    log.info("Open that URL in your browser to link a bank account")
    app.run(host = "127.0.0.1", port = 8000, debug = False)