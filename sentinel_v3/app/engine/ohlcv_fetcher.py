
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict
import logging
from app.core.config import settings, Timeframe
from app.exchanges.kucoin_adapter import KuCoinAdapter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OHLCVFetcher:
    """
    Fetch OHLCV candlestick data.
    Sentinel 3.0: Uses ExchangeAdapters instead of direct clients.
    """
    
    def __init__(self):
        # We can support multiple exchanges here.
        # For now, simplistic implementation to support MarketScanner.
        self.kucoin = KuCoinAdapter()
    
    def fetch(
        self,
        symbol: str,
        timeframe: Timeframe,
        exchange: str = "kucoin",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Unified fetch method
        """
        if exchange.lower() == "kucoin":
            return self.kucoin.get_market_structure(symbol, timeframe.value, limit)
        else:
            logger.warning(f"Exchange {exchange} not fully supported in Fetcher yet. Returning empty.")
            return pd.DataFrame()

    def fetch_multi_timeframe(
        self,
        symbol: str,
        timeframes: List[Timeframe],
        exchange: str = "kucoin",
        limit: int = 500
    ) -> Dict[Timeframe, pd.DataFrame]:
        """
        Fetch multiple timeframes for a symbol
        """
        result = {}
        for tf in timeframes:
            try:
                result[tf] = self.fetch(symbol, tf, exchange, limit)
            except Exception as e:
                logger.error(f"Failed to fetch {symbol} {tf.value}: {e}")
        return result
