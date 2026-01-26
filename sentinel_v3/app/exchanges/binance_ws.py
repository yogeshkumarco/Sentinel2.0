
import json
import logging
import threading
import time
import os
import websocket
from urllib.parse import urlparse
from typing import Dict, Optional, Callable, List

logger = logging.getLogger(__name__)

class BinanceFuturesWSClient:
    """
    Binance Futures WebSocket Client.
    Stream live candle data for real-time updates without REST polling.
    """
    
    WS_BASE_URL = "wss://fstream.binance.com/ws"
    
    def __init__(self, symbol: str, interval: str, callback: Callable[[dict], None]):
        self.symbol = self._normalize_symbol(symbol)
        self.interval = interval
        self.callback = callback
        
        self.ws: Optional[websocket.WebSocketApp] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.reconnect_delay = 5
        
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to Binance format (e.g. XBTUSDTM -> BTCUSDT)"""
        symbol = symbol.upper()
        if symbol.endswith("USDTM"):
            symbol = symbol.replace("USDTM", "USDT")
        if symbol.startswith("XBT"):
            symbol = symbol.replace("XBT", "BTC")
        return symbol.lower() # WebSocket stream names are lowercase

    def start(self):
        """Start the WebSocket in a separate non-blocking thread"""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._run_forever, daemon=True)
        self.thread.start()
        logger.info(f"Binance WS Started for {self.symbol} {self.interval}")

    def stop(self):
        """Stop the WebSocket cleanly"""
        self.running = False
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("Binance WS Stopped")

    def _run_forever(self):
        """Main loop handling connection and auto-reconnect"""
        stream_name = f"{self.symbol.lower()}@kline_{self.interval}"
        url = f"{self.WS_BASE_URL}/{stream_name}"
        
        # Check for Proxy in Env
        proxy_host = None
        proxy_port = None
        proxy_auth = None
        
        proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if proxy_url:
            try:
                p = urlparse(proxy_url)
                proxy_host = p.hostname
                proxy_port = p.port
                if p.username and p.password:
                    proxy_auth = (p.username, p.password)
                logger.info(f"Using Proxy: {proxy_host}:{proxy_port}")
            except Exception as e:
                logger.error(f"Failed to parse proxy: {e}")
        
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.run_forever(
                    ping_interval=60,
                    http_proxy_host=proxy_host,
                    http_proxy_port=proxy_port,
                    http_proxy_auth=proxy_auth,
                    sslopt={"cert_reqs": 0} # ssl.CERT_NONE
                )
            except Exception as e:
                logger.error(f"WS Connection Error: {e}")
            
            if self.running:
                logger.warning(f"WS Disconnected. Reconnecting in {self.reconnect_delay}s...")
                time.sleep(self.reconnect_delay)

    def _on_message(self, ws, message):
        """Handle incoming candle message"""
        try:
            data = json.loads(message)
            # Structure: 
            # { "e": "kline", "E": 123456789, "s": "BTCUSDT", "k": { ... } }
            
            if data.get('e') == 'kline':
                k = data['k']
                
                # Parse candle
                candle = {
                    'time': int(k['t']),
                    'open': float(k['o']),
                    'high': float(k['h']),
                    'low': float(k['l']),
                    'close': float(k['c']),
                    'volume': float(k['v']),
                    'closed': k['x'] # True if candle is closed
                }
                
                # Emit to callback
                self.callback(candle)
                
        except Exception as e:
            logger.error(f"WS JSON Parse Error: {e}")

    def _on_error(self, ws, error):
        if self.running: # Only log if we expect to be running
            logger.error(f"WS Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        if self.running:
            logger.info("WS Connection Closed")
