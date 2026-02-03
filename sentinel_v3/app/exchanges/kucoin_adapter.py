
import requests
import time
import logging
import hmac
import hashlib
import base64
import json
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime
import pytz
from urllib.parse import urlencode

from app.exchanges.abstract import ExchangeProvider, Position


from app.core.config import settings
from app.exchanges.kucoin_ws import KuCoinFuturesWSClient
from app.engine.paper_engine import PaperTradingEngine

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')

class KuCoinAdapter(ExchangeProvider):
    """
    KuCoin Futures Adapter implementation of ExchangeProvider.
    Ports logic from legacy kucoin_client.py
    """
    
    BASE_URL = "https://api-futures.kucoin.com"

    @property
    def is_paper(self) -> bool:
        """Check if running in paper mode (dynamic check)"""
        return not (self.api_key and self.api_secret and self.passphrase) or settings.mode.value == 'dry_run'

    def __init__(self):
        self.api_key = settings.kucoin.api_key
        self.api_secret = settings.kucoin.api_secret
        self.passphrase = settings.kucoin_passphrase
        self.timeout = 10
        self.authenticated = False
        
        # Paper Trading Engine (separate from Binance)
        self.paper = PaperTradingEngine(exchange_name="kucoin", initial_balance=10000.0)
        
        # Websocket support
        self.ws_clients: Dict[str, KuCoinFuturesWSClient] = {}
        self.market_cache: Dict[str, List[Dict]] = {}  # symbol -> list of candles
        
        if self.is_paper:
            logger.info("KuCoin Adapter: Operating in PAPER MODE (Virtual $10,000)")
        else:
            logger.info("KuCoin Adapter: Operating in LIVE MODE")
        
        self.connect()

    def connect(self) -> bool:
        """Verify API keys check out"""
        self.authenticated = bool(self.api_key and self.api_secret and self.passphrase)
        if self.authenticated:
            # Optional: Test connection with a private call or just set flag
            logger.info("KuCoin Adapter: Credentials present.")
        else:
            logger.warning("KuCoin Adapter: No credentials. Public mode only.")
            
        # Initialize contract specs
        self.contract_specs = {}
        self.fetch_contracts()
        return self.authenticated

    def fetch_contracts(self):
        """Fetch active contracts to get multipliers/lot sizes"""
        try:
            res = self._request('GET', '/api/v1/contracts/active')
            if res:
                for contract in res:
                    symbol = contract['symbol']
                    # lotSize is typically the 'multiplier' for futures
                    # For KuCoin: 'multiplier' is the contract value per lot
                    # Example: XBTUSDTM multiplier = 0.001
                    self.contract_specs[symbol] = {
                        'multiplier': float(contract.get('multiplier', 1)),
                        'lotSize': float(contract.get('lotSize', 1)) 
                        # Note: KuCoin lotSize might be labeled differently, 
                        # usually 'multiplier' is what we need to convert Qty -> Lots.
                        # Lots = Qty / Multiplier
                    }
                logger.info(f"Loaded {len(self.contract_specs)} contract specs.")
        except Exception as e:
            logger.error(f"Failed to fetch contracts: {e}")

    def _sign(self, timestamp: str, method: str, endpoint: str, body: str = "") -> Dict[str, str]:
        """Generate authentication headers"""
        str_to_sign = timestamp + method + endpoint + body
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode('utf-8'),
                str_to_sign.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')
        
        passphrase = base64.b64encode(
            hmac.new(
                self.api_secret.encode('utf-8'),
                self.passphrase.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')
        
        return {
            'KC-API-KEY': self.api_key,
            'KC-API-SIGN': signature,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': passphrase,
            'KC-API-KEY-VERSION': '2',
            'Content-Type': 'application/json'
        }

    def _request(self, method: str, endpoint: str, params: Dict = None, body: Dict = None, private: bool = False) -> Any:
        """Make HTTP request to KuCoin"""
        url = f"{self.BASE_URL}{endpoint}"
        headers = {}
        
        if private:
            if not self.authenticated:
                raise Exception("Private endpoint requires API credentials")
            timestamp = str(int(time.time() * 1000))
            body_str = json.dumps(body) if body else ""
            
            # For GET requests, include query params in the signature
            sign_endpoint = endpoint
            query_string = ""
            
            if method == 'GET' and params:
                # CRITICAL: Sort params alphabetically for signature matching
                # KuCoin requires params to be sorted
                sorted_params = sorted(params.items())
                query_string = urlencode(sorted_params)
                sign_endpoint = f"{endpoint}?{query_string}"
            
            headers = self._sign(timestamp, method, sign_endpoint, body_str)
        
        try:
            if method == 'GET':
                # Pass the exact encoded query string if private to ensure match
                if private and params:
                    url = f"{url}?{query_string}"
                    response = requests.get(url, headers=headers, timeout=self.timeout)
                else:
                    response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            elif method == 'POST':
                response = requests.post(url, json=body, headers=headers, timeout=self.timeout)
            elif method == 'DELETE':
                response = requests.delete(url, params=params, headers=headers, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
                
            response.raise_for_status()
            data = response.json()
            
            if data['code'] != '200000':
                raise Exception(f"KuCoin API error: {data['code']} - {data.get('msg')}")
                
            return data['data']
            
        except Exception as e:
            logger.error(f"KuCoin Request failed: {url} - {e}")
            raise

    def start_ws(self, symbol: str, interval: str):
        """Start KuCoin WS for a symbol if not already running"""
        if symbol not in self.market_cache:
            self.market_cache[symbol] = []
        
        if symbol in self.ws_clients:
            return
        
        # Define callback
        def on_update(candle):
            self._on_candle_update(symbol, candle)
        
        # Start client
        client = KuCoinFuturesWSClient(symbol, interval, on_update)
        client.start()
        self.ws_clients[symbol] = client
        logger.info(f"KuCoin WS started for {symbol} {interval}")
    
    def _on_candle_update(self, symbol: str, candle: dict):
        """Handle WS candle update"""
        cache = self.market_cache.get(symbol, [])
        
        # Update paper price for real-time P&L
        if self.is_paper:
            self.paper.update_mark_prices({symbol: candle['close']})
        
        # Update cache
        if not cache:
            cache.append(candle)
        else:
            last = cache[-1]
            if candle['time'] == last['time']:
                cache[-1] = candle  # Update existing candle
            else:
                cache.append(candle)  # New candle
        
        # Limit cache size
        if len(cache) > 500:
            self.market_cache[symbol] = cache[-500:]
        else:
            self.market_cache[symbol] = cache

    def get_market_structure(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """
        Fetch OHLCV klines (Hybrid: REST snapshot + WS updates)
        """
        tf_map = {'1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}
        granularity = tf_map.get(timeframe, 1)
        
        # Start websocket if enabled
        if settings.trading.use_websockets:
            self.start_ws(symbol, timeframe)
        
        # ALWAYS fetch fresh data from API for accurate analysis
        # Previously cached data was stale and caused incorrect decisions
        try:
            raw_klines = self._request('GET', '/api/v1/kline/query', 
                                     params={'symbol': symbol, 'granularity': granularity})
        except Exception as e:
            logger.error(f"Failed to fetch klines for {symbol}: {e}")
            return pd.DataFrame()
        
        if not raw_klines:
            return pd.DataFrame()
        
        # Convert to cache format
        snapshot = []
        for k in raw_klines:
            snapshot.append({
                'time': int(k[0]),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]) if len(k) > 5 else 0,
                'closed': True
            })
        
        self.market_cache[symbol] = snapshot
        
        # Update paper price from snapshot
        # NOTE: KuCoin returns newest candle FIRST, so [0] is most recent
        if snapshot and self.is_paper:
            self.paper.update_mark_prices({symbol: snapshot[0]['close']})
        
        # Return from cache
        data = self.market_cache.get(symbol, [])
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
        # Ensure numeric types
        cols = ['open', 'high', 'low', 'close', 'volume']
        df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
        
        # Sort by time ASCENDING (Oldest -> Newest)
        df = df.sort_values('time').reset_index(drop=True)
        
        return df[['time', 'open', 'high', 'low', 'close', 'volume']].tail(limit)

    def get_open_positions(self) -> List[Position]:
        """Fetch active positions"""
        if self.is_paper:
            return self.paper.get_positions()
        
        if not self.authenticated:
            return []
            
        try:
            raw_positions = self._request('GET', '/api/v1/positions', private=True)
            positions = []
            
            for pos in raw_positions or []:
                if pos.get('currentQty', 0) == 0:
                    continue
                    
                # Extract details
                qty = pos['currentQty']
                side = 'LONG' if qty > 0 else 'SHORT'
                entry = float(pos.get('avgEntryPrice', 0))
                mark = float(pos.get('markPrice', 0))
                leverage = int(pos.get('realLeverage', 5))
                pnl = float(pos.get('unrealisedPnl', 0))
                roe = float(pos.get('unrealisedRoePcnt', 0)) * 100
                
                positions.append(Position(
                    symbol=pos['symbol'],
                    side=side,
                    size=abs(qty),
                    entry_price=entry,
                    current_price=mark,
                    unrealized_pnl=pnl,
                    pnl_pct=roe,
                    leverage=leverage,
                    raw=pos
                ))
            return positions
            
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_balance(self, currency: str = 'USDT') -> float:
        """Get Available Balance"""
        if self.is_paper:
            return self.paper.get_balance()
        
        # Live trading
        if not self.authenticated:
            return 0.0
        try:
            # KuCoin Futures Account Overview
            res = self._request('GET', f'/api/v1/account-overview?currency={currency}', private=True)
            return float(res.get('availableBalance', 0))
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0.0

    def _set_margin_mode(self, symbol: str, mode: str):
        """
        Set margin mode (ISOLATED/CROSS) via V2 Endpoint.
        """
        try:
            body = {
                "symbol": symbol,
                "marginMode": mode.upper()
            }
            # Correct V2 Endpoint
            self._request('POST', '/api/v2/position/changeMarginMode', body=body, private=True)
        except Exception as e:
            # If already in that mode, it might return error, which is fine.
            # But if it fails for other reasons, we log it.
            logger.warning(f"Set Margin Mode to {mode} result: {e}")

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, leverage: int, price: Optional[float] = None, reduce_only: bool = False) -> Dict:
        """Place Order"""
        # Get price for paper trading
        exec_price = price if price else self.get_current_price(symbol)
        
        if self.is_paper:
            return self.paper.place_order(symbol, side, quantity, leverage, exec_price)
        
        # LIVE TRADING
        # 1. Enforce ISOLATED Mode (User Requirement)
        if not reduce_only:
            self._set_margin_mode(symbol, 'ISOLATED')
        
        # Convert LONG/SHORT to buy/sell for KuCoin API
        s_upper = side.upper()
        if s_upper == 'LONG':
            kucoin_side = 'buy'
        elif s_upper == 'SHORT':
            kucoin_side = 'sell'
        else:
            kucoin_side = side.lower()
            
        # Format Quantity using Contract Multiplier
        # Reference: https://www.kucoin.com/futures/contract/detail
        multiplier = 1.0
        if symbol in self.contract_specs:
            multiplier = self.contract_specs[symbol]['multiplier']
        elif 'XBT' in symbol or 'BTC' in symbol:
            multiplier = 0.001  # 1 lot = 0.001 BTC
        elif 'ETH' in symbol:
            multiplier = 0.01   # 1 lot = 0.01 ETH
        elif 'SOL' in symbol:
            multiplier = 0.1    # 1 lot = 0.1 SOL
        elif 'LTC' in symbol:
            multiplier = 0.1    # 1 lot = 0.1 LTC
        elif 'LINK' in symbol:
            multiplier = 1.0    # 1 lot = 1 LINK
        elif 'SUI' in symbol:
            multiplier = 1.0    # 1 lot = 1 SUI
        elif 'ADA' in symbol:
            multiplier = 10.0   # 1 lot = 10 ADA
        elif 'XRP' in symbol:
            multiplier = 10.0   # 1 lot = 10 XRP
        elif 'DOGE' in symbol:
            multiplier = 100.0  # 1 lot = 100 DOGE
        elif 'PEPE' in symbol:
            multiplier = 1000000.0  # 1 lot = 1M PEPE (meme coin)
        elif 'ZEC' in symbol:
            multiplier = 0.1    # 1 lot = 0.1 ZEC
            
        try:
            lots = int(quantity / multiplier)
            if lots < 1:
                logger.warning(f"Quantity {quantity} too small for {symbol}. Setting to 1.")
                lots = 1
        except Exception:
            lots = int(quantity)

        body = {
            'clientOid': str(int(time.time() * 1000)),
            'symbol': symbol,
            'side': kucoin_side,
            'type': order_type.lower(),
            'leverage': str(leverage),
            'size': lots,
            'reduceOnly': reduce_only,
            'marginMode': 'ISOLATED' # Explicitly request ISOLATED
        }
        
        if order_type.lower() == 'limit' and price:
            body['price'] = str(price)
            
        # Place Order
        return self._request('POST', '/api/v1/orders', body=body, private=True)

    def close_position(self, symbol: str, size: Optional[float] = None) -> Dict:
        """Close position using KuCoin's closeOrder parameter"""
        exec_price = self.get_current_price(symbol)
        
        if exec_price <= 0:
            logger.error(f"Cannot close {symbol}: Invalid price {exec_price}")
            return {}
        
        if self.is_paper:
            return self.paper.close_position(symbol, exec_price)
        
        # Live trading - Use closeOrder=True (KuCoin handles side/size automatically)
        # This is the recommended approach per KuCoin Futures API docs
        try:
            body = {
                'clientOid': str(int(time.time() * 1000)),
                'symbol': symbol,
                'type': 'market',
                'closeOrder': True  # <-- KuCoin API closes entire position automatically
            }
            
            logger.info(f"📤 KuCoin Close Order: {symbol} (closeOrder=True)")
            result = self._request('POST', '/api/v1/orders', body=body, private=True)
            
            if result and result.get('orderId'):
                logger.info(f"✅ KuCoin Position Closed: {symbol} - Order ID: {result['orderId']}")
            return result
            
        except Exception as e:
            logger.error(f"KuCoin Close Position Error: {e}")
            return {}



    def get_current_price(self, symbol: str) -> float:
        """Get latest price (cache-first for low latency)"""
        # Check cache first (fastest)
        # IMPORTANT: KuCoin returns candles NEWEST FIRST, so cache[0] is the latest!
        cache = self.market_cache.get(symbol)
        if cache:
            price = cache[0]['close']  # First element = newest candle
            return price
            
        try:
            # Fallback to API Ticker
            ticker = self._request('GET', f'/api/v1/ticker?symbol={symbol}')
            price = float(ticker['price'])
            logger.info(f"💲 Price({symbol}) from API: {price}")
            return price
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return 0.0
    
    def stop(self):
        """Cleanup websockets"""
        logger.info("Stopping KuCoin Adapter...")
        for sym, client in self.ws_clients.items():
            client.stop()
        self.ws_clients.clear()

    def get_top_symbols(self, limit: int = 20) -> List[Dict]:
        """
        Get top symbols by volume
        Returns: [{'symbol': str, 'volume': float, 'price': float}, ...]
        """
        try:
            # Fetch all tickers
            tickers = self._request('GET', '/api/v1/contracts/active')
            
            results = []
            for t in tickers:
                if t['symbol'].endswith('USDTM'): # Only USDT-M futures
                     results.append({
                         'symbol': t['symbol'],
                         'volume': float(t.get('turnoverOf24h', 0)), # Use turnover (USDT volume)
                         'price': float(t.get('lastTradePrice', 0))
                     })
            
            # Sort by volume desc
            results.sort(key=lambda x: x['volume'], reverse=True)
            return results[:limit]
        except Exception as e:
            logger.error(f"Error fetching top symbols: {e}")
            return []

    def get_trade_history(self, limit: int = 50) -> List[Dict]:
        """
        Get recent trade history with order info.
        Paper: Returns internal list.
        Live: Fetches CLOSED ORDERS from KuCoin /api/v1/recentDoneOrders
        """
        if self.is_paper:
            return self.paper.trade_history[-limit:]
        
        # Live Implementation - Use Recent Done Orders
        # Live Implementation - Use Recent Fills (Better than Orders)
        try:
            # Endpoint: GET /api/v1/recentFills
            # Returns recent executions including price/size
            res = self._request('GET', '/api/v1/recentFills', private=True)
            
            if not res:
                return []
                
            # Response is a list of fill objects
            items = res if isinstance(res, list) else res.get('items', []) if isinstance(res, dict) else []
            
            trades = []
            for fill in items:
                # Fill fields: symbol, side, price, size, value, fee, tradeId, etc.
                price = float(fill.get('price', 0))
                size = float(fill.get('size', 0))
                fee = float(fill.get('fee', 0))
                
                trades.append({
                    "symbol": fill.get('symbol', ''),
                    "side": fill.get('side', 'unknown').upper(),
                    "size": size,
                    "entry_price": price,
                    # For fills, entry is the execution price. 
                    # We can't easily know if it was an "open" or "close" without more context,
                    # but accurate price is better than $0.00
                    "exit_price": 0, 
                    "pnl": 0,  # Still hard to calc per-fill PnL without position tracking
                    "roe_pct": 0,
                    "leverage": 0, # Fills don't always have leverage info
                    "timestamp": fill.get('createdAt', ''),
                    "type": "FILL"
                })
            return trades
        except Exception as e:
            logger.error(f"Error fetching recent fills: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching recent orders: {e}")
            return []

