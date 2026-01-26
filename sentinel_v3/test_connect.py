import requests
import socket
import sys

def test_url(url):
    print(f"\n--- Testing {url} ---")
    
    # 1. DNS Resolution
    hostname = url.replace("https://", "").replace("wss://", "").split("/")[0]
    try:
        ip = socket.gethostbyname(hostname)
        print(f"DNS Resolution: {hostname} -> {ip}")
    except Exception as e:
        print(f"DNS Resolution FAILED: {e}")
        return

    # 2. HTTP Request
    try:
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # Bypass SSL verification for testing
        response = requests.get(f"{url}/fapi/v1/time", headers=headers, timeout=5, verify=False)
        
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        print(f"HTTP Status: {response.status_code}")
        print(f"Response: {response.text[:100]}")
    except Exception as e:
        print(f"HTTP Request FAILED: {e}")

if __name__ == "__main__":
    test_url("https://fapi.binance.com")
    test_url("https://fapi.binance.me")
