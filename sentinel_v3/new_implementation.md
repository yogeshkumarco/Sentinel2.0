# Master Prompt: Rebuild Sentinel V3 Backend
 
**Instruction to AI:**
Act as a Senior Python Quantitative Developer. Your task is to rebuild the **Backend** for the "Sentinel V3" crypto trading bot. A React frontend already exists and expects a specific API contract. You must build a robust, production-grade FastAPI backend that supports **Multi-Exchange Trading (Binance + KuCoin)** with strictly separated **Paper** and **Live** modes.
 
---
 
## 1. Architecture Overview
 
-   **Framework**: FastAPI (Python 3.11+)
-   **Entry Point**: Single `run_bot.py` that starts the API server and Bot Manager.
-   **Architecture**:
    -   **Bot Manager**: Singleton service that manages independent instances of `TradingBot` for each exchange.
    -   **Exchange Adapters**: Abstract base class with implementations for `BinanceAdapter` and `KucoinAdapter`.
    -   **Engine**:
        -   **Sniper Strategy**: Logic for detecting Harmonic Patterns on 15m timeframe.
        -   **Paper Engine**: Simulates order execution using local `state.json` files when in Dry-Run.
 
---
 
## 2. API Contract (Critical for Frontend Compatibility)
 
The frontend expects the following endpoints at `http://localhost:8000/api`.
 
### Global Control
-   `GET /api/status`: Returns `{ "binance": {...}, "kucoin": {...} }` (Status of both bots).
-   `GET /api/settings`: Returns current global settings (MUST include `mode` field: "dry_run" or "live").
-   `POST /api/settings`: Accepts `{ "mode": "live" | "dry_run", "allocation": int }`. Updates global state.
-   `GET /api/sentiment`: Returns `{ "regime": "NEUTRAL", "confidence": 0.5 }`.
-   `GET /api/pnl/daily`: Returns list of daily P&L objects (or empty list `[]`).
-   `GET /api/trades`: Returns list of historical trades (or empty list `[]`).
 
### Exchange-Specific Control ({exchange} = "binance" or "kucoin")
-   `POST /api/{exchange}/start`: Starts the specific bot instance.
-   `POST /api/{exchange}/stop`: Stops the specific bot instance.
-   `GET /api/{exchange}/status`: Returns status: `{ "running": bool, "pid": int }`.
-   `GET /api/{exchange}/balance`: Returns wallet balance (Real if Live, Simulated if Paper).
-   `GET /api/{exchange}/positions`: Returns list of open positions:
    ```json
    [{ "symbol": "XRPUSDTM", "side": "LONG", "size": 100, "entry_price": 1.0, "current_price": 1.1, "pnl_pct": 10.0, "leverage": 5 }]
    ```
-   `POST /api/{exchange}/positions/{symbol}/close`: Closes the specific position by symbol.
 
---
 
## 3. "Sniper" Strategy Logic
 
The bot must implement a **Harmonic Pattern Scanner** ("Sniper Mode").
 
-   **Timeframe**: 15 Minute (15m) primarily.
-   **Strategy**:
    1.  **Scan**: Fetch recent candles (OHLCV).
    2.  **Detect Patterns**: Identify 5-point patterns (X, A, B, C, D) based on Fibonacci ratios.
    3.  **Patterns to Detect**:
        -   **Gartley** (B=0.618, D=0.786)
        -   **Butterfly** (B=0.786, D=1.27/1.618)
        -   **Bat** (B=0.382/0.5, D=0.886)
        -   **Crab** (D=1.618)
        -   **Shark** & **Cypher**
    4.  **Entry Logic**: Enter at Point D.
    5.  **Exits**:
        -   **Take Profit**: Targets at Point C and Point A (Fib levels).
        -   **Stop Loss**: Beyond Point X (or tight stop below D).
 
---
 
## 4. Paper vs. Live Logic
 
### Paper Mode (Dry-Run)
-   **Default State**: System starts in `DRY_RUN` by default.
-   **Storage**: Use `data/{exchange}/state.json` to persist simulated balance (start $10,000) and positions.
-   **Execution**: When "placing order", simply update `state.json` and log the trade.
-   **Price Updates**: Mock P&L updates based on live market prices.
 
### Live Mode
-   **Activation**: Enabled only when `settings.mode` is set to "LIVE" via API.
-   **Requirement**: User MUST restart the bot instance (Stop -> Start) for mode change to take effect.
-   **Safety**:
    -   Verify API Keys (`.env`) before starting.
    -   Use `ccxt` or `requests` to interact with real exchange APIs.
    -   **CRITICAL**: `get_balance` and `close_position` must work against the real exchange.
    -   **CRITICAL**: `get_balance` and `close_position` must work against the real exchange.
 
---
 
## 5. Configuration & Constants
 
Use a `config.py` with specific settings:
-   **Coin Tiers**:
    -   Tier 1 (BTC, ETH): 15x max leverage.
    -   Tier 2 (SOL, XRP, ADA): 8x max leverage.
    -   Tier 3 (DOGE, PEPE): 3x max leverage.
-   **Risk**: Max 2% risk per trade.
-   **Watchlist**: `['XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM', 'XRPUSDTM', 'DOGEUSDTM', 'ADAUSDTM']`.
 
---
 
## 6. Project Structure
 
Rebuild the files in this structure:
 
```
sentinel_v3/
├── run_bot.py              # Entry point
├── .env                    # Keys
├── app/
│   ├── api/
│   │   └── endpoints.py    # FastAPI routes
│   ├── engine/
│   │   ├── services.py     # BotManager
│   │   ├── bot.py          # Main TradingBot loop
│   │   └── paper_engine.py # Mock execution
│   ├── exchanges/
│   │   ├── binance_adapter.py
│   │   └── kucoin_adapter.py
│   ├── models/
│   │   └── harmonics.py    # Pattern detection logic
│   └── core/
│       └── config.py       # Configuration
└── data/                   # JSON state files
```
 
**Next Step for AI:** Start by creating the project structure and the `run_bot.py` entry point.
 
 