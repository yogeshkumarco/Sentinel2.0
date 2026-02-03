
from typing import List, Dict, Optional
import pandas as pd
import logging
import requests
import time
import hmac
import hashlib
from urllib.parse import urlencode
from app.exchanges.abstract import ExchangeProvider, Position
from app.core.config import settings
from app.engine.paper_engine import PaperTradingEngine
from app.exchanges.binance_ws import BinanceFuturesWSClient

logger = logging.getLogger(__name__)

class BinanceAdapter(ExchangeProvider):
    """
    Binance Futures Adapter.
    Supports:
    - Public Data (Hybrid: REST Snapshot + WebSocket Stream)
    - Paper Trading (Simulated Execution)
    """
    
    BASE_URL = "https://fapi.binance.com"
    
    @property
    def is_paper(self) -> bool:
        """Check if running in paper mode (dynamic check)"""
        return not (self.api_key and self.api_secret) or settings.mode.value == 'dry_run'

    def __init__(self):
        self.api_key = settings.binance.api_key
        self.api_secret = settings.binance.api_secret
        self.paper = PaperTradingEngine(exchange_name="binance", initial_balance=10000.0)
        
        # Data Caches
        self.ws_clients: Dict[str, BinanceFuturesWSClient] = {}
        self.market_cache: Dict[str, List[Dict]] = {} # symbol -> list of candle dicts
        
        # Setup Session with Retries and Headers
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json"
        })
        self.session.verify = False 
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

        if self.is_paper:
            logger.info("Binance Adapter: Operating in PAPER MODE (Virtual $10,000)")
        else:
            logger.info("Binance Adapter: Operating in LIVE MODE")

    def connect(self) -> bool:
        return True

    def _sign_request(self, params: Dict) -> Dict:
        """Generate HMAC-SHA256 signature for Binance authenticated requests"""
        params['timestamp'] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params

    def _signed_request(self, method: str, endpoint: str, params: Dict = None) -> Dict:
        """Make a signed request to Binance Futures API"""
        if params is None:
            params = {}
        
        signed_params = self._sign_request(params)
        headers = {'X-MBX-APIKEY': self.api_key}
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            if method == 'GET':
                res = self.session.get(url, params=signed_params, headers=headers, timeout=10)
            elif method == 'POST':
                res = self.session.post(url, params=signed_params, headers=headers, timeout=10)
            elif method == 'DELETE':
                res = self.session.delete(url, params=signed_params, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            res.raise_for_status()
            return res.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Binance API Error: {e} - {e.response.text if e.response else 'No response'}")
            return {}
        except Exception as e:
            logger.error(f"Binance Request Failed: {e}")
            return {}

    def normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to Binance format (e.g. XBTUSDTM -> BTCUSDT)"""
        symbol = symbol.upper()
        
        # KuCoin → Binance futures mapping
        if symbol.endswith("USDTM"):
            symbol = symbol.replace("USDTM", "USDT")
            
        # Special mappings for Binance Futures
        special_map = {
            "XBTUSDT": "BTCUSDT",      # Bitcoin
            "PEPEUSDT": "1000PEPEUSDT", # PEPE uses 1000x multiplier
            "SHIBUSDT": "1000SHIBUSDT", # SHIB uses 1000x multiplier
            "FLOKIUSDT": "1000FLOKIUSDT", # FLOKI uses 1000x multiplier
        }
        
        if symbol in special_map:
            return special_map[symbol]
            
        return symbol

    def start_ws(self, symbol: str, interval: str):
        """Start WS for a symbol if not already running"""
        # Ensure we have a cache entry
        if symbol not in self.market_cache:
            self.market_cache[symbol] = []

        if symbol in self.ws_clients:
            return

        # Define Callback
        def on_update(candle):
            self._on_candle_update(symbol, candle)

        # Start Client
        client = BinanceFuturesWSClient(symbol, interval, on_update)
        client.start()
        self.ws_clients[symbol] = client

    def _on_candle_update(self, symbol: str, candle: dict):
        """Handle WS Update: Update Cache & Paper Price"""
        cache = self.market_cache.get(symbol, [])
        
        # Update Paper Price (Real-time PnL)
        if self.is_paper:
            self.paper.update_mark_prices({symbol: candle['close']})

        # Update Cache (Logic: Update last candle if same time, else append)
        if not cache:
            cache.append(candle)
        else:
            last = cache[-1]
            if candle['time'] == last['time']:
                # Update in place (this corresponds to live candle changing)
                cache[-1] = candle
            else:
                # New candle started (previous closed)
                # Only if previous one is actually closed? No, Binance sends 'x' flag when closed.
                # But here we stream all updates. If 't' is new, it's a new candle.
                cache.append(candle)
                
        # Limit cache size (keep last 500 to match typical REST limits)
        if len(cache) > 500:
            self.market_cache[symbol] = cache[-500:]
        else:
            self.market_cache[symbol] = cache

    def get_market_structure(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """Fetch market data (Hybrid: REST Cache + WS Updates)"""
        # Normalize Symbol first
        symbol = self.normalize_symbol(symbol)

        # Map timeframe
        tf_map = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '4h': '4h', '1d': '1d'}
        interval = tf_map.get(timeframe, '1m')

        # 1. Start WS if needed (Lazy Load)
        if settings.trading.use_websockets:
            self.start_ws(symbol, interval)

        # 2. Check Cache
        if not self.market_cache.get(symbol):
            # Cache empty: Fetch Snapshot via REST
            try:
                url = f"{self.BASE_URL}/fapi/v1/klines"
                params = {
                    'symbol': symbol,
                    'interval': interval,
                    'limit': limit
                }
                res = self.session.get(url, params=params, timeout=5)
                res.raise_for_status()
                data = res.json()
                
                # Convert REST format to Candle Dict format
                # REST: [time, open, high, low, close, volume, ...]
                snapshot = []
                for k in data:
                    snapshot.append({
                        'time': int(k[0]),
                        'open': float(k[1]),
                        'high': float(k[2]),
                        'low': float(k[3]),
                        'close': float(k[4]),
                        'volume': float(k[5]),
                        'closed': True # Historical candles are closed
                    })
                
                self.market_cache[symbol] = snapshot
                
                # Update Mark Price from snapshot
                if snapshot:
                    self.paper.update_mark_prices({symbol: snapshot[-1]['close']})

            except Exception as e:
                logger.error(f"Snapshot Failed: {e}")
                return pd.DataFrame()

        # 3. Return DataFrame from Cache
        data = self.market_cache.get(symbol, [])
        if not data:
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        return df[['time', 'open', 'high', 'low', 'close', 'volume']].tail(limit)

        # Map timeframe
        tf_map = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '4h': '4h', '1d': '1d'}
        interval = tf_map.get(timeframe, '1m')
        
        try:
            url = f"{self.BASE_URL}/fapi/v1/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            # Use session
            res = self.session.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            
            # [time, open, high, low, close, vol, ...]
            df = pd.DataFrame(data, columns=[
                'time', 'open', 'high', 'low', 'close', 'volume', 
                'close_time', 'quote_vol', 'trades', 'buy_base_vol', 'buy_quote_vol', 'ignore'
            ])
            
            # Clean
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].astype(float)
            df['time'] = df['time'].astype('int64')
            
            # Update Paper Engine's internal pricing for PnL
            latest_price = df['close'].iloc[-1]
            self.paper.update_mark_prices({symbol: latest_price})
            
            return df[['time', 'open', 'high', 'low', 'close', 'volume']]
            
        except Exception as e:
            logger.error(f"Binance Data Error: {e}")
            return pd.DataFrame()

    def get_open_positions(self) -> List[Position]:
        if self.is_paper:
            return self.paper.get_positions()
        else:
            try:
                # Fetch Live Positions (Signed Request Required)
                data = self._signed_request('GET', '/fapi/v2/positionRisk')
                if not data:
                    return []
                
                positions = []
                for p in data:
                    amt = float(p.get('positionAmt', 0))
                    if amt == 0:
                        continue
                        
                    # Extract Data
                    symbol = p['symbol']
                    side = "LONG" if amt > 0 else "SHORT"
                    entry = float(p.get('entryPrice', 0))
                    mark = float(p.get('markPrice', 0))
                    lev = int(p.get('leverage', 1))
                    unrealized = float(p.get('unRealizedProfit', 0))
                    
                    # Calculate ROE% roughly if not provided
                    # PnL / Margin. Margin = (Entry * Size) / Lev
                    # This is approximate
                    margin = (abs(amt) * entry) / lev
                    roe = (unrealized / margin) * 100 if margin > 0 else 0
                    
                    positions.append(Position(
                        symbol=symbol,
                        side=side,
                        size=abs(amt),
                        entry_price=entry,
                        current_price=mark,
                        unrealized_pnl=unrealized,
                        pnl_pct=roe,
                        leverage=lev,
                        raw=p
                    ))
                return positions
            except Exception as e:
                logger.error(f"Binance Live Positions Failed: {e}")
                return []

    def get_balance(self, currency: str = 'USDT') -> float:
        if self.is_paper:
            return self.paper.get_balance()
        else:
            try:
                # Fetch Future Account Balance (Signed Request Required)
                data = self._signed_request('GET', '/fapi/v2/balance')
                if not data:
                    return 0.0
                
                # Find USDT balance
                for asset in data:
                    if asset['asset'] == currency:
                        return float(asset.get('availableBalance', asset.get('balance', 0)))
                return 0.0
            except Exception as e:
                logger.error(f"Binance Live Balance Failed: {e}")
                return 0.0

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, leverage: int, price: Optional[float] = None, reduce_only: bool = False) -> Dict:
        # Normalize symbol
        symbol = self.normalize_symbol(symbol)
        
        # Get Price if Market
        exec_price = price
        if not exec_price:
            exec_price = self.get_current_price(symbol)
            
        if self.is_paper:
            return self.paper.place_order(symbol, side, quantity, leverage, exec_price)
        else:
            # LIVE MODE: Execute on Binance Futures
            try:
                # 1. Set Leverage first
                lev_params = {'symbol': symbol, 'leverage': leverage}
                lev_res = self._signed_request('POST', '/fapi/v1/leverage', lev_params)
                if not lev_res:
                    logger.error(f"Failed to set leverage for {symbol}")
                    # Continue anyway, might already be set
                
                # 2. Place Market Order
                order_params = {
                    'symbol': symbol,
                    'side': side.upper(),  # BUY or SELL
                    'type': 'MARKET',
                    'quantity': quantity,
                }
                if reduce_only:
                    order_params['reduceOnly'] = 'true'
                
                order_res = self._signed_request('POST', '/fapi/v1/order', order_params)
                
                if order_res and order_res.get('orderId'):
                    logger.info(f"✅ Binance Order Placed: {side} {quantity} {symbol} @ Market (Lev: {leverage}x)")
                    return order_res
                else:
                    logger.error(f"Binance Order Failed: {order_res}")
                    return {}
                    
            except Exception as e:
                logger.error(f"Binance Order Error: {e}")
                return {}

    def close_position(self, symbol: str, size: Optional[float] = None) -> Dict:
        # Normalize symbol
        symbol = self.normalize_symbol(symbol)
        exec_price = self.get_current_price(symbol)
        
        if self.is_paper:
            return self.paper.close_position(symbol, exec_price)
        else:
            # LIVE MODE: Close position on Binance Futures
            try:
                # 1. Get current position to determine side and size
                positions = self.get_open_positions()
                target_pos = None
                for p in positions:
                    if self.normalize_symbol(p.symbol) == symbol:
                        target_pos = p
                        break
                
                if not target_pos:
                    logger.warning(f"No open position found for {symbol}")
                    return {}
                
                # 2. Close by placing opposite order
                close_side = 'SELL' if target_pos.side == 'LONG' else 'BUY'
                close_qty = size if size else target_pos.size
                
                order_params = {
                    'symbol': symbol,
                    'side': close_side,
                    'type': 'MARKET',
                    'quantity': close_qty,
                    'reduceOnly': 'true',
                }
                
                order_res = self._signed_request('POST', '/fapi/v1/order', order_params)
                
                if order_res and order_res.get('orderId'):
                    logger.info(f"✅ Binance Position Closed: {close_side} {close_qty} {symbol} @ Market")
                    return order_res
                else:
                    logger.error(f"Binance Close Failed: {order_res}")
                    return {}
                    
            except Exception as e:
                logger.error(f"Binance Close Error: {e}")
                return {}
    
    def get_current_price(self, symbol: str) -> float:
        symbol = self.normalize_symbol(symbol)
        
        # Check Cache First (Fastest)
        cache = self.market_cache.get(symbol)
        if cache:
             return cache[-1]['close']

        # Fallback to REST if no cache (and no WS running)
        try:
            res = self.session.get(f"{self.BASE_URL}/fapi/v1/ticker/price", params={'symbol': symbol}, timeout=5)
            res.raise_for_status()
            data = res.json()
            return float(data['price'])
        except Exception as e:
            logger.error(f"Price Check Failed: {e}")
            return 0.0

    def stop(self):
        """Cleanup Websockets"""
        logger.info("Stopping Binance Adapter...")
        for sym, client in self.ws_clients.items():
            client.stop()
        self.ws_clients.clear()

    def get_top_symbols(self, limit: int = 20) -> List[Dict]:
        """
        Get top symbols by volume
        Returns: [{'symbol': str, 'volume': float, 'price': float}, ...]
        """
        try:
            # Fetch 24hr ticker
            res = self.session.get(f"{self.BASE_URL}/fapi/v1/ticker/24hr", timeout=10)
            res.raise_for_status()
            data = res.json()
            
            results = []
            for t in data:
                if t['symbol'].endswith('USDT'):
                    results.append({
                        'symbol': t['symbol'],
                        'volume': float(t.get('quoteVolume', 0)), # Use quote volume (USDT)
                        'price': float(t.get('lastPrice', 0))
                    })
            
            # Sort by volume desc
            results.sort(key=lambda x: x['volume'], reverse=True)
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Error fetching top symbols: {e}")
            return []
