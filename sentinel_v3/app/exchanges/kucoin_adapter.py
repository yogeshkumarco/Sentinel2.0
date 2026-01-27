
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
        
        # Check cache first
        if not self.market_cache.get(symbol):
            # Fetch REST snapshot
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
            if snapshot and self.is_paper:
                self.paper.update_mark_prices({symbol: snapshot[-1]['close']})
        
        # Return from cache
        data = self.market_cache.get(symbol, [])
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
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

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, leverage: int, price: Optional[float] = None, reduce_only: bool = False) -> Dict:
        """Place Order"""
        # Get price for paper trading
        exec_price = price if price else self.get_current_price(symbol)
        
        if self.is_paper:
            return self.paper.place_order(symbol, side, quantity, leverage, exec_price)
        
        # Set Margin Mode to ISOLATED before placing order
        self._set_margin_mode(symbol, 'ISOLATED')
        
        # Live trading (original code)
        # Convert LONG/SHORT to buy/sell for KuCoin API
        s_upper = side.upper()
        if s_upper == 'LONG':
            kucoin_side = 'buy'
        elif s_upper == 'SHORT':
            kucoin_side = 'sell'
        else:
            # Assume already 'buy' or 'sell'
            kucoin_side = side.lower()
            
        # Format Quantity using Contract Multiplier
        # KuCoin Futures requires 'size' (Integer Lots)
        # Lots = BaseAmount / Multiplier
        
        multiplier = 1.0
        if symbol in self.contract_specs:
            multiplier = self.contract_specs[symbol]['multiplier']
        elif 'XBT' in symbol or 'BTC' in symbol:
            multiplier = 0.001
        elif 'ETH' in symbol:
            multiplier = 0.01
        elif 'SOL' in symbol:
            multiplier = 0.1
            
        # Calculate Lots
        # e.g. Qty 0.022 ETH / 0.01 = 2.2 -> 2 Lots
        # If Qty < Multiplier, size becomes 0.
        
        try:
            lots = int(quantity / multiplier)
            if lots < 1:
                logger.warning(f"Quantity {quantity} too small for {symbol} (Multiplier: {multiplier}). Setting to 1.")
                lots = 1
        except Exception:
            lots = int(quantity)

        body = {
            'clientOid': str(int(time.time() * 1000)),
            'symbol': symbol,
            'side': kucoin_side,
            'type': order_type.lower(),
            'leverage': str(leverage),
            'size': lots, # Send integer Lots
            'reduceOnly': reduce_only
            # Note: marginMode removed - uses account default to avoid 330005 errors
        }
        
        if order_type.lower() == 'limit' and price:
            body['price'] = str(price)
            
        return self._request('POST', '/api/v1/orders', body=body, private=True)

    def close_position(self, symbol: str, size: Optional[float] = None) -> Dict:
        """Close position"""
        exec_price = self.get_current_price(symbol)
        
        if exec_price <= 0:
            logger.error(f"Cannot close {symbol}: Invalid price {exec_price}")
            return {}
        
        if self.is_paper:
            return self.paper.close_position(symbol, exec_price)
        
        # Live trading (original code)
        positions = self.get_open_positions()
        target = next((p for p in positions if p.symbol == symbol), None)
        
        if not target:
            logger.warning(f"No position found for {symbol} to close.")
            return {}
            
        close_qty = int(size) if size else target.size
        close_side = 'sell' if target.side == 'LONG' else 'buy'
        
        return self.place_order(
            symbol=symbol,
            side=close_side,
            order_type='market',
            quantity=close_qty,
            leverage=target.leverage,
            reduce_only=True
        )

    def _set_margin_mode(self, symbol: str, mode: str):
        """
        Set margin mode (ISOLATED/CROSS).
        KuCoin Endpoint varies - try multiple endpoints.
        """
        try:
            # Try the standard endpoint first
            body = {
                "symbol": symbol,
                "marginMode": mode.upper()
            }
            self._request('POST', '/api/v1/position/margin-mode', body=body, private=True)
        except Exception as e:
            # Endpoint may not exist or mode already set - silently continue
            # The order will fail with 330005 if truly wrong, handled in place_order
            logger.warning(f"Failed to set margin mode for {symbol}: {e}")
            pass

    def get_current_price(self, symbol: str) -> float:
        """Get latest price (cache-first for low latency)"""
        # Check cache first (fastest)
        cache = self.market_cache.get(symbol)
        if cache:
            return cache[-1]['close']
        
        # Fallback to REST
        try:
            res = self._request('GET', '/api/v1/ticker', {'symbol': symbol})
            return float(res.get('price', 0))
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
        try:
            # Endpoint: GET /api/v1/recentDoneOrders
            # Returns recently closed orders (last 24 hours, up to 1000)
            # No params required for basic usage
            
            res = self._request('GET', '/api/v1/recentDoneOrders', private=True)
            
            if not res:
                return []
                
            # Response is a list of order objects
            items = res if isinstance(res, list) else res.get('items', []) if isinstance(res, dict) else []
            
            trades = []
            for order in items:
                # Order fields: symbol, side, price, size, value, filledQty, filledValue, etc.
                # Note: Individual order doesn't have full PnL - that requires matching entry/exit
                
                trades.append({
                    "symbol": order.get('symbol', ''),
                    "side": order.get('side', 'unknown').upper(),
                    "size": float(order.get('filledSize', order.get('size', 0))),
                    "entry_price": float(order.get('price', order.get('dealValue', 0))),
                    "exit_price": 0,  # Would need to match with opposite order
                    "pnl": 0,  # Calculate separately or from position data
                    "roe_pct": 0,
                    "leverage": int(float(order.get('leverage', 1))),  # Handle '10.0' strings
                    "timestamp": order.get('updatedAt', order.get('createdAt', '')),
                    "type": "CLOSED_ORDER"
                })
            return trades
        except Exception as e:
            logger.error(f"Error fetching recent orders: {e}")
            return []

