# Market Scanner
# Finds the best trading opportunities based on volatility and trend

import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Optional
import time


import sys
# sys.path.append('..')
from app.exchanges.kucoin_adapter import KuCoinAdapter
from app.engine.ohlcv_fetcher import OHLCVFetcher
from app.engine.price_features import PriceFeatures
from app.models.Model_Sentinel.decision_engine import DecisionEngine
from app.core.config import Timeframe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketScanner:
    """
    Scans the market for high-probability setups.
    
    Filters:
    1. Volume (Liquidity)
    2. Volatility (ATR)
    3. Funding Rate (Avoid crowded trades)
    4. Trend Quality (Decision Engine Score)
    """
    
    def __init__(self):
        self.client = KuCoinAdapter()
        self.fetcher = OHLCVFetcher()
        self.decision_engine = DecisionEngine()
        
    
    def scan(self, limit: int = 20, blacklist: Dict = None) -> Optional[Dict]:
        """
        Scan top symbols and return the best opportunity
        Args:
            limit: Number of coins to check
            blacklist: Dict of {symbol: cooldown_expiry} to skip
        """
        try:
            # 1. Get Top Volume Symbols
            logger.info(f"[SCAN] Scanning top {limit} symbols...")
            tickers = self.client.get_top_symbols(limit=limit)
            
            evaluated = []
            
            for ticker in tickers:
                symbol = ticker['symbol']
                volume = ticker.get('volume', 0)  # 24h USDT volume
                
                print(f"DEBUG: Checking {symbol} (Vol: ${volume:,.0f})")

                # Skip stablecoin pairs other than USDT
                if not symbol.endswith('USDTM'):
                    print(f"  SKIP: Not USDTM")
                    continue
                
                # Filter: Cooldown / Blacklist
                if blacklist and symbol in blacklist:
                    if time.time() < blacklist[symbol]:
                        print(f"  SKIP: Cooldown Active ({int(blacklist[symbol] - time.time())}s)")
                        continue
                
                # Filter: Minimum volume for liquidity (skip very low-volume coins)
                if volume < 5_000_000:  # Minimum $5M daily volume (Increased per user request)
                    print(f"  SKIP: Volume too low")
                    continue
                    
                # 2. Analyze individual symbol
                setup = self._analyze_symbol(symbol, volume)
                if setup:
                    evaluated.append(setup)
                    time.sleep(0.3)  # Rate limit niceness
            
            # 3. Rank results
            if not evaluated:
                logger.info("  No good setups found.")
                return None
                
            # Sort by composite score: Quality (50%) + Volatility (40%) + Volume Rank (10%)
            # We want ACTIVE coins, not just liquid ones.
            max_vol = max(e['volume'] for e in evaluated) if evaluated else 1
            for e in evaluated:
                vol_score = e['volume'] / max_vol  # 0-1 normalized
                # Volatility score: Cap at 3% per candle for normalization
                volatility_score = min(e['volatility'], 3.0) / 3.0
                
                e['composite_score'] = (e['quality'] * 0.5) + (volatility_score * 0.4) + (vol_score * 0.1)
            
            ranked = sorted(
                evaluated, 
                key=lambda x: x['composite_score'], 
                reverse=True
            )
            
            best = ranked[0]
            logger.info(f"[WIN] Best Find: {best['symbol']} (Score: {best['composite_score']:.2f}, Volatility: {best['volatility']:.2f}%)")
            
            return best
            
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            return None
    
    def _analyze_symbol(self, symbol: str, volume: float = 0) -> Optional[Dict]:
        """Analyze a single symbol for setup quality"""
        try:
            # Fetch recent candles
            df = self.fetcher.fetch(symbol, Timeframe.M15, exchange='kucoin', limit=100)
            if df.empty:
                print(f"  {symbol}: Empty DF")
                return None
                
            # Compute features
            pf = PriceFeatures(df)
            df = pf.compute_all()
            
            # 1. Filter: Minimum Volatility (ATR %)
            # We want movers, not dead coins
            latest = df.iloc[-1]
            atr_pct = (latest['atr'] / latest['close']) * 100
            
            if atr_pct < 0.2:  # Lowered to 0.2% to catch moves in quiet markets
                print(f"  {symbol}: Low ATR ({atr_pct:.2f}%)")
                return None
            
            # 2. Filter: Avoid "missed bus" - check recent price momentum
            # If price moved >10% in last 4 candles (1 hour), it may have already pumped
            recent_move = ((df['close'].iloc[-1] - df['close'].iloc[-4]) / df['close'].iloc[-4]) * 100
            if abs(recent_move) > 10:  # Loosened further to 10%
                print(f"  {symbol}: Moved too much ({recent_move:.2f}%)")
                return None
                
            # 3. Get Decision
            decision = self.decision_engine.analyze(df)
            
            # 4. Filter: Must have a Direction
            if decision.direction_bias == 'NONE':
                # Log when direction is missing so we can debug
                # logger.debug(f"  {symbol}: No direction bias (Quality: {decision.setup_quality:.2f})")
                print(f"  {symbol}: No Direction (Quality: {decision.setup_quality:.2f})")
                return None
            
            # 5. Filter: Minimum Quality (very low to find opportunities)
            if decision.setup_quality < 0.5:  # Raised to 0.5 per user request (was 0.3)
                print(f"  {symbol}: Low Quality ({decision.setup_quality:.2f})")
                return None
            
            # 6. Filter: Price vs Balance check (REMOVED)
            # User confirmed they can trade ETH with leverage.
            # We will let the allocation manager handle insufficient funds if they occur.
            # if latest['close'] > 20.0:
            #      print(f"  {symbol}: Price too high (${latest['close']:.2f})")
            #      return None
            
            print(f"  [PASS] {symbol}: Quality={decision.setup_quality:.2f}, Volatility={atr_pct:.2f}%")    
            return {
                'symbol': symbol,
                'direction': decision.direction_bias,
                'quality': decision.setup_quality,
                'confidence': decision.confidence,
                'volatility': atr_pct,
                'volume': volume,
                'current_price': latest['close'],
                'regime': decision.market_state,
                'reason': decision.reasoning
            }
            
        except Exception as e:
            # limit logging for individual symbol errors
            print(f"  [ERROR] Error analyzing {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return None

# Singleton
_scanner = MarketScanner()
def get_scanner():
    return _scanner

if __name__ == "__main__":
    scan = MarketScanner()
    best = scan.scan(limit=10)
    print("\nBest Opportunity:", best)
