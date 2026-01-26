
import json
import os
import time

def reset_file(exchange):
    path = f"data/{exchange}/state.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    data = {
        "balance": 10000.0,
        "positions": {},
        "trade_history": [],
        "last_updated": int(time.time())
    }
    
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
    
    print(f"✅ Reset {exchange} state to $10,000")

reset_file("binance")
reset_file("kucoin")
