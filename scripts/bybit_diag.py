import hmac
import hashlib
import time
import requests
import yaml
import os

# Load config
try:
    with open("config/settings.yaml", "r") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"Error loading config: {e}")
    exit(1)

api_key = config['bybit']['api_key']
api_secret = config['bybit']['api_secret']
testnet = config['bybit']['testnet']

# Bybit Endpoints
base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
endpoint = "/v5/account/wallet-balance"
params = "accountType=UNIFIED&coin=USDT"

def get_signature(api_key, api_secret, timestamp, recv_window, params):
    param_str = str(timestamp) + api_key + str(recv_window) + params
    hash = hmac.new(bytes(api_secret, "utf-8"), param_str.encode("utf-8"), hashlib.sha256)
    return hash.hexdigest()

def test_auth(url, key, secret):
    timestamp = int(time.time() * 1000)
    recv_window = 15000
    signature = get_signature(key, secret, timestamp, recv_window, params)
    
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(recv_window),
        "Content-Type": "application/json"
    }
    
    try:
        r = requests.get(url + endpoint + "?" + params, headers=headers)
        return r.status_code, r.json(), timestamp
    except Exception as e:
        return 0, str(e), timestamp

print(f"--- Bybit Diagnostic ---")
print(f"Using Environment: {'Testnet' if testnet else 'Mainnet'}")
print(f"API Key: {api_key[:5]}...{api_key[-3:]}")

# Check Clock
try:
    server_time_resp = requests.get("https://api.bybit.com/v5/market/time").json()
    server_time_ms = int(server_time_resp['result']['timeNow']) * 1000
    local_time_ms = int(time.time() * 1000)
    drift = local_time_ms - server_time_ms
    print(f"Local Time:  {local_time_ms}")
    print(f"Server Time: {server_time_ms}")
    print(f"System Drift: {drift}ms")
except:
    print("Could not fetch server time.")

# Test Current Config
status, resp, local_ts = test_auth(base_url, api_key, api_secret)
print(f"\nResult (Current Config):")
print(f"HTTP Status: {status}")
print(f"Response: {resp}")

if status == 401:
    print("\n--- Diagnostic Analysis ---")
    print("X 401 Unauthorized detected.")
    
    # Try alternate environment just in case
    alt_url = "https://api.bybit.com" if testnet else "https://api-testnet.bybit.com"
    alt_env = "Mainnet" if testnet else "Testnet"
    print(f"Testing against {alt_env} for verification...")
    s2, r2, _ = test_auth(alt_url, api_key, api_secret)
    print(f"HTTP Status (alt): {s2}")
    if s2 == 200:
        print(f"!!! KEY MISMATCH FOUND: These keys belong to {alt_env}, but config is set to {'Testnet' if testnet else 'Mainnet'}. !!!")
    else:
        print("Keys failed in both environments. Likely causes:")
        print("1. Keys have incorrect permissions (need Wallet/Spot enabled).")
        print("2. Keys are tied to a different IP.")
        print("3. Keys are for a Sub-account but used at Sub-account level incorrectly.")
        print("4. Typo in Key or Secret.")
