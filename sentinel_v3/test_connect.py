import requests
import time
import hmac
import hashlib
import base64
import sys
import os

# Add parent directory to path to import app.core.config
sys.path.append(os.getcwd())

from app.core.config import settings
from dotenv import load_dotenv
import os

# Force reload from .env to be sure
load_dotenv(override=True)

print(f"--- AUTH & PRIVATE ENDPOINT CHECK ---")

API_KEY = os.getenv("KUCOIN_API_KEY") or settings.kucoin.api_key
API_SECRET = os.getenv("KUCOIN_API_SECRET") or settings.kucoin.api_secret
PASSPHRASE = os.getenv("KUCOIN_PASSPHRASE") or settings.kucoin_passphrase

print(f"Loaded from Env/Config:")
print(f"API Key:      '{API_KEY[:4]}...{API_KEY[-4:]}' (Len: {len(API_KEY)})")
print(f"API Secret:   '{API_SECRET[:4]}...{API_SECRET[-4:]}' (Len: {len(API_SECRET)})")
print(f"Passphrase:   '{PASSPHRASE}' (Len: {len(PASSPHRASE)})")

if len(API_KEY.strip()) != len(API_KEY):
    print(f"⚠️ WARNING: API Key has leading/trailing whitespace!")
if len(API_SECRET.strip()) != len(API_SECRET):
    print(f"⚠️ WARNING: API Secret has leading/trailing whitespace!")
if len(PASSPHRASE.strip()) != len(PASSPHRASE):
    print(f"⚠️ WARNING: Passphrase has leading/trailing whitespace!")

from urllib.parse import urlencode

# Endpoint: GET /api/v1/account-overview (Futures)
ENDPOINT = "/api/v1/account-overview"
URL = f"https://api-futures.kucoin.com{ENDPOINT}"
PARAMS = {'currency': 'USDT'}

timestamp = str(int(time.time() * 1000))

# 1. Sign
# KuCoin requires params to be sorted and separated by ?
query_string = urlencode(sorted(PARAMS.items()))
str_to_sign = f"{timestamp}GET{ENDPOINT}?{query_string}"

signature = base64.b64encode(
    hmac.new(
        API_SECRET.encode('utf-8'),
        str_to_sign.encode('utf-8'),
        hashlib.sha256
    ).digest()
).decode('utf-8')

# 2. Passphrase
passphrase_sign = base64.b64encode(
    hmac.new(
        API_SECRET.encode('utf-8'),
        PASSPHRASE.encode('utf-8'),
        hashlib.sha256
    ).digest()
).decode('utf-8')

headers = {
    'KC-API-KEY': API_KEY,
    'KC-API-SIGN': signature,
    'KC-API-TIMESTAMP': timestamp,
    'KC-API-PASSPHRASE': passphrase_sign,
    'KC-API-KEY-VERSION': '2',
    'Content-Type': 'application/json'
}

print(f"\nSending Signed Request to {ENDPOINT}...")
try:
    resp = requests.get(URL, params=PARAMS, headers=headers, timeout=10)
    
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
    
    with open("last_error.txt", "w") as f:
        f.write(f"Status: {resp.status_code}\n")
        f.write(resp.text)
    
    if resp.status_code == 200:
        print(f"✅ SUCCESS! Auth is working. Balance data received.")
    else:
        print(f"❌ FAILED. The issue is definitely Auth or Permissions.")

except Exception as e:
    print(f"❌ Connection Error: {e}")
