import urllib.request
import os

print("--- Environment Variables ---")
print(f"HTTP_PROXY: {os.environ.get('HTTP_PROXY')}")
print(f"HTTPS_PROXY: {os.environ.get('HTTPS_PROXY')}")

print("\n--- System Proxy detection ---")
proxies = urllib.request.getproxies()
if proxies:
    print("Detected Proxies:")
    for proto, url in proxies.items():
        print(f"{proto}: {url}")
else:
    print("No system proxies detected by Python.")
