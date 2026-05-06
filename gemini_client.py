"""
Gemini Client for Transaction Description
Takes Raw Plaid merchant string plus context (amount, account, time of the day) and returns a clean
Spanglish? Or Spanish (Your choice) description of the transaction, to be used in the UI.

I am using my personal descriptions for my output to stay consistent.
"""

import logging
import os
import yaml
import time
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors

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
MAX_RETRIES = 4
RETRIES_BASE_DELAY = 2  # seconds

def clean_description(merchant: str, amount: float, account: str, date: str) -> str:
    # Clean a raw merchant string to the user's voice using Gemini.
    # Each call is fresh so context (amount, account, date) influences the result.
    examples = "\n".join([f"- Raw: {raw}\n  Cleaned: {cleaned}" for raw, cleaned in FEWSHOT_EXAMPLES])
    prompt = PROMPT_TEMPLATE.format(examples=examples, merchant=merchant, amount=amount, account=account, date=date)

    for attempt in range(MAX_RETRIES):
        try:
            response = _client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt)
            cleaned = response.text.strip().strip('"').strip("'")  # Remove any extra whitespace or quotes
            return cleaned
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRIES_BASE_DELAY * (2 ** attempt)  # Exponential backoff
                log.warning(f"Gemini API error: {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
                continue
            #Last attempt failed
            log.exception(f"Gemini failed after {MAX_RETRIES} attempts for '{merchant}'")
            return merchant
        except Exception:
            log.exception(f"Non-Gemini error for '{merchant}'")
            return merchant
    return merchant # Fallback
    
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