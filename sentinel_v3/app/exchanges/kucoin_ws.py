"""
KuCoin Futures WebSocket Client
Implements real-time kline (candlestick) streaming for low latency
"""

import json
import logging
import threading
import time
import websocket
import requests
from typing import Dict, Optional, Callable

logger = logging.getLogger(__name__)


class KuCoinFuturesWSClient:
    """
    KuCoin Futures WebSocket Client
    Streams live candle data for real-time updates
    """
    
    # KuCoin requires getting a token first
    TOKEN_URL = "https://api-futures.kucoin.com/api/v1/bullet-public"
    
    def __init__(self, symbol: str, interval: str, callback: Callable[[dict], None]):
        self.symbol = self._normalize_symbol(symbol)
        self.interval = self._map_interval(interval)
        self.callback = callback
        
        self.ws: Optional[websocket.WebSocketApp] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.reconnect_delay = 5
        
        # KuCoin specific
        self.ws_url = None
        self.ping_interval = 18  # KuCoin recommends 18s ping
        
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to KuCoin format"""
        # KuCoin uses: XBTUSDTM (already correct)
        return symbol.upper()
    
    def _map_interval(self, interval: str) -> int:
        """Map interval string to KuCoin granularity (minutes)"""
        mapping = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "1d": 1440
        }
        return mapping.get(interval, 15)  # Default 15m
    
    def _get_ws_token(self) -> Optional[Dict]:
        """Get websocket token and endpoint from KuCoin"""
        try:
            response = requests.post(self.TOKEN_URL, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("code") == "200000":
                return data["data"]
            else:
                logger.error(f"KuCoin token request failed: {data}")
                return None
        except Exception as e:
            logger.error(f"Failed to get KuCoin WS token: {e}")
            return None
    
    def start(self):
        """Start the WebSocket in a separate thread"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_forever, daemon=True)
        self.thread.start()
        logger.info(f"KuCoin WS Started for {self.symbol} {self.interval}min")
    
    def stop(self):
        """Stop the WebSocket cleanly"""
        self.running = False
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("KuCoin WS Stopped")
    
    def _run_forever(self):
        """Main loop with auto-reconnect"""
        while self.running:
            try:
                # Get fresh token
                token_data = self._get_ws_token()
                if not token_data:
                    logger.error("Failed to get KuCoin token, retrying...")
                    time.sleep(self.reconnect_delay)
                    continue
                
                # Extract connection details
                instance = token_data["instanceServers"][0]
                endpoint = instance["endpoint"]
                token = token_data["token"]
                
                # Build WebSocket URL
                self.ws_url = f"{endpoint}?token={token}"
                self.ping_interval = instance.get("pingInterval", 18000) // 1000  # ms to seconds
                
                # Connect
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                
                self.ws.run_forever(
                    ping_interval=self.ping_interval,
                    sslopt={"cert_reqs": 0}
                )
                
            except Exception as e:
                logger.error(f"KuCoin WS Error: {e}")
            
            if self.running:
                logger.warning(f"KuCoin WS Disconnected. Reconnecting in {self.reconnect_delay}s...")
                time.sleep(self.reconnect_delay)
    
    def _on_open(self, ws):
        """Subscribe to kline channel on connection"""
        # KuCoin subscription format
        subscribe_msg = {
            "id": int(time.time() * 1000),
            "type": "subscribe",
            "topic": f"/contractMarket/limitCandle:{self.symbol}_{self.interval}min",
            "privateChannel": False,
            "response": True
        }
        
        ws.send(json.dumps(subscribe_msg))
        logger.info(f"KuCoin WS Subscribed to {self.symbol} {self.interval}min candles")
    
    def _on_message(self, ws, message):
        """Handle incoming messages"""
        try:
            data = json.loads(message)
            
            # Handle different message types
            msg_type = data.get("type")
            
            if msg_type == "welcome":
                logger.info("KuCoin WS Connected")
                return
            
            if msg_type == "pong":
                return  # Heartbeat response
            
            if msg_type == "message":
                # Candle data
                topic = data.get("topic", "")
                if "limitCandle" in topic:
                    candle_data = data.get("data", {})
                    
                    # Parse KuCoin candle format
                    # Format: [timestamp, open, high, low, close, volume]
                    candles = candle_data.get("candles", [])
                    if candles and len(candles) > 0:
                        c = candles[0]  # Most recent candle
                        
                        candle = {
                            'time': int(c[0]),  # Timestamp in ms
                            'open': float(c[1]),
                            'high': float(c[2]),
                            'low': float(c[3]),
                            'close': float(c[4]),
                            'volume': float(c[5]) if len(c) > 5 else 0,
                            'closed': True  # KuCoin sends closed candles
                        }
                        
                        self.callback(candle)
            
        except Exception as e:
            logger.error(f"KuCoin WS Message Parse Error: {e}")
    
    def _on_error(self, ws, error):
        if self.running:
            logger.error(f"KuCoin WS Error: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        if self.running:
            logger.info("KuCoin WS Connection Closed")
