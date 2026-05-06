"""
Gemini Client for Transaction Description
Takes Raw Plaid merchant string plus context (amount, account, time of the day) and returns a clean
Spanglish? Or Spanish (Your choice) description of the transaction, to be used in the UI.

I am using my personal descriptions for my output to stay consistent.
"""

import logging
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv()

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    log.error("Missing GEMINI_API_KEY in .env")
    raise SystemExit(1)

# Gemini Flash is the right balance of speed + cost. Use 2.5 if available.
_client = genai.Client(api_key=GEMINI_API_KEY)

# THIS ARE MY PERSONAL EXAMPLES
PROMPTS_FILE = Path("prompts.yaml")

def _load_prompts():
    if not PROMPTS_FILE.exists():
        log.error(f"{PROMPTS_FILE} not found.")
        raise SystemExit(1)
    with PROMPTS_FILE.open() as f:
        data = yaml.safe_load(f)
    return data["clean_description"]

_PROMPTS = _load_prompts()
PROMPT_TEMPLATE = _PROMPTS["template"]
MODEL_NAME=_PROMPTS["model"]
FEWSHOT_EXAMPLES = [(ex["raw"], ex["clean"]) for ex in _PROMPTS["examples"]]

def clean_description(merchant: str, amount: float, account: str, date: str) -> str:
    # Clean a raw merchant string to the user's voice using Gemini
    examples = "\n".join([f"- Raw: {raw}\n  Cleaned: {cleaned}" for raw, cleaned in FEWSHOT_EXAMPLES])
    prompt = PROMPT_TEMPLATE.format(examples=examples, merchant=merchant, amount=amount, account=account, date=date)
    
    try:
        response = _client.models.generate_content(
            model=MODEL_NAME, 
            contents=prompt)
        cleaned = response.text.strip().strip('"').strip("'")  # Remove any extra whitespace or quotes
        return cleaned
    except Exception:
        log.exception(f"Error generating cleaned description for merchant '{merchant}'")
        return merchant
    
if __name__ == "__main__":
    # COOL CONFIG FOR TESTING THE CLEANER IN ISOLATION
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    # Standalone test
    test_cases = [                                         
          ("CHEVRON #1234 GAS",            18.50,  "discover", "2026-05-04"),
          ("UBER 0421 SF*POOL",             8.40,  "discover", "2026-05-03"),  # late night → taxi home?                                      
          ("TRANSFER TO SAVINGS",        -100.00,  "sofi",     "2026-05-01"),
          ("LE BERNARDIN",                250.00,  "discover", "2026-05-15"),                                                                 
      ]
    
    for merchant, amount, account, date in test_cases:
        cleaned = clean_description(merchant, amount, account, date)
        print(f"Raw: {merchant}\nCleaned: {cleaned}\n")