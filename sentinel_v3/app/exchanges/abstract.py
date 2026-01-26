from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass
import pandas as pd

@dataclass
class Position:
    symbol: str
    side: str  # 'LONG' or 'SHORT'
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    pnl_pct: float
    leverage: int
    raw: Optional[Dict] = None # Store original exchange data if needed

class ExchangeProvider(ABC):
    """
    Abstract Interface for Trading Exchanges.
    Any new exchange must implement these methods.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Authenticate and establish connection"""
        pass

    @abstractmethod
    def get_market_structure(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """
        Fetch OHLCV data and formatting it into a standard DataFrame.
        Must return columns: [timestamp, open, high, low, close, volume]
        """
        pass

    @abstractmethod
    def get_open_positions(self) -> List[Position]:
        """Return list of currently open positions"""
        pass

    @abstractmethod
    def get_balance(self, currency: str = 'USDT') -> float:
        """Get available wallet balance"""
        pass

    @abstractmethod
    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, leverage: int, price: Optional[float] = None, reduce_only: bool = False) -> Dict:
        """
        Place an order (Market or Limit).
        side: 'buy' or 'sell'
        """
        pass

    @abstractmethod
    def close_position(self, symbol: str, size: Optional[float] = None) -> Dict:
        """
        Close a position completely or partially.
        If size is None, close entire position.
        """
        pass
    
    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Get the latest mark price for a symbol"""
        pass
