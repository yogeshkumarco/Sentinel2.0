"""
Chop Detector - Market Condition Analysis

Detects whether the market is TRENDING or CHOPPY and recommends
appropriate timeframe and parameters.

CHOPPY Market Indicators:
- Low ATR ratio (current vs average)
- Low ADX (< 20 = no trend)
- Narrow Bollinger Bands

When CHOPPY:
- Switch from 15m to 5m entries
- Use 15m as HTF confirmation (instead of 1h)
- Tighter targets and stop losses
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("SentinelBot")


@dataclass
class MarketMode:
    """Result of market condition analysis."""
    mode: str  # "TRENDING" or "CHOPPY"
    confidence: float  # 0.0 - 1.0
    timeframe: str  # "15m" or "5m"
    htf_timeframe: str  # "1h" or "15m"
    target_pct: float  # Target profit %
    sl_pct: float  # Stop loss %
    atr_ratio: float  # Current ATR vs Average
    adx: float  # ADX value
    bb_width_pct: float  # Bollinger Band width %
    

class ChopDetector:
    """
    Detects choppy/ranging vs trending market conditions.
    
    Uses multiple indicators:
    1. ATR Ratio - Current ATR(14) / ATR(50)
    2. ADX - Trend strength indicator
    3. Bollinger Band Width - Volatility compression
    """
    
    def __init__(self):
        # Thresholds for chop detection
        self.atr_ratio_choppy = 0.8  # Below this = choppy
        self.atr_ratio_trending = 1.2  # Above this = trending
        self.adx_choppy = 20  # Below this = no trend
        self.adx_trending = 25  # Above this = trending
        self.bb_width_choppy = 3.0  # Below this % = compression
        self.bb_width_trending = 5.0  # Above this % = expansion
        
        # Parameters for each mode
        self.trending_params = {
            "timeframe": "15m",
            "htf_timeframe": "1h",
            "target_pct": 2.0,
            "sl_pct": 1.0
        }
        self.choppy_params = {
            "timeframe": "5m",
            "htf_timeframe": "15m",
            "target_pct": 0.8,
            "sl_pct": 0.4
        }
    
    def detect(self, df) -> MarketMode:
        """
        Analyze market conditions and return appropriate mode.
        
        Args:
            df: DataFrame with OHLCV data (15m timeframe)
            
        Returns:
            MarketMode with recommended settings
        """
        try:
            if df is None or len(df) < 60:
                # Not enough data, default to trending mode
                return self._create_mode("TRENDING", 0.5, 1.0, 25.0, 4.0)
            
            # Use closed candles only
            df_closed = df.iloc[:-1].copy() if len(df) > 1 else df.copy()
            
            # Calculate indicators
            atr_ratio = self._calculate_atr_ratio(df_closed)
            adx = self._calculate_adx(df_closed)
            bb_width_pct = self._calculate_bb_width(df_closed)
            
            # Score each indicator (0 = trending, 1 = choppy)
            atr_score = self._score_atr_ratio(atr_ratio)
            adx_score = self._score_adx(adx)
            bb_score = self._score_bb_width(bb_width_pct)
            
            # Weighted average (ADX is most reliable)
            chop_score = (atr_score * 0.3 + adx_score * 0.5 + bb_score * 0.2)
            
            # Determine mode
            if chop_score >= 0.6:
                mode = "CHOPPY"
                confidence = chop_score
            else:
                mode = "TRENDING"
                confidence = 1.0 - chop_score
            
            return self._create_mode(mode, confidence, atr_ratio, adx, bb_width_pct)
            
        except Exception as e:
            logger.warning(f"ChopDetector error: {e}")
            return self._create_mode("TRENDING", 0.5, 1.0, 25.0, 4.0)
    
    def _create_mode(self, mode: str, confidence: float, 
                     atr_ratio: float, adx: float, bb_width_pct: float) -> MarketMode:
        """Create MarketMode with appropriate parameters."""
        if mode == "CHOPPY":
            params = self.choppy_params
        else:
            params = self.trending_params
            
        return MarketMode(
            mode=mode,
            confidence=confidence,
            timeframe=params["timeframe"],
            htf_timeframe=params["htf_timeframe"],
            target_pct=params["target_pct"],
            sl_pct=params["sl_pct"],
            atr_ratio=atr_ratio,
            adx=adx,
            bb_width_pct=bb_width_pct
        )
    
    def _calculate_atr_ratio(self, df) -> float:
        """Calculate ATR(14) / ATR(50) ratio."""
        try:
            high = df['high'].values
            low = df['low'].values
            close = df['close'].values
            
            # True Range
            tr = np.maximum(
                high[1:] - low[1:],
                np.maximum(
                    np.abs(high[1:] - close[:-1]),
                    np.abs(low[1:] - close[:-1])
                )
            )
            
            # ATR 14 and 50
            if len(tr) >= 50:
                atr_14 = np.mean(tr[-14:])
                atr_50 = np.mean(tr[-50:])
                return atr_14 / atr_50 if atr_50 > 0 else 1.0
            return 1.0
        except:
            return 1.0
    
    def _calculate_adx(self, df, period: int = 14) -> float:
        """Calculate ADX (Average Directional Index)."""
        try:
            if len(df) < period * 2:
                return 25.0  # Default neutral
            
            high = df['high'].values
            low = df['low'].values
            close = df['close'].values
            
            # +DM and -DM
            plus_dm = np.zeros(len(high))
            minus_dm = np.zeros(len(high))
            
            for i in range(1, len(high)):
                up_move = high[i] - high[i-1]
                down_move = low[i-1] - low[i]
                
                if up_move > down_move and up_move > 0:
                    plus_dm[i] = up_move
                if down_move > up_move and down_move > 0:
                    minus_dm[i] = down_move
            
            # True Range
            tr = np.maximum(
                high[1:] - low[1:],
                np.maximum(
                    np.abs(high[1:] - close[:-1]),
                    np.abs(low[1:] - close[:-1])
                )
            )
            tr = np.insert(tr, 0, high[0] - low[0])
            
            # Smoothed values (simple moving average)
            atr = np.convolve(tr, np.ones(period)/period, mode='valid')
            plus_di = 100 * np.convolve(plus_dm, np.ones(period)/period, mode='valid') / (atr + 1e-10)
            minus_di = 100 * np.convolve(minus_dm, np.ones(period)/period, mode='valid') / (atr + 1e-10)
            
            # DX
            dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
            
            # ADX (smoothed DX)
            if len(dx) >= period:
                adx = np.mean(dx[-period:])
                return float(adx)
            return 25.0
        except:
            return 25.0
    
    def _calculate_bb_width(self, df, period: int = 20) -> float:
        """Calculate Bollinger Band width as % of price."""
        try:
            if len(df) < period:
                return 4.0  # Default neutral
            
            close = df['close'].values[-period:]
            sma = np.mean(close)
            std = np.std(close)
            
            upper = sma + 2 * std
            lower = sma - 2 * std
            
            width_pct = (upper - lower) / sma * 100
            return float(width_pct)
        except:
            return 4.0
    
    def _score_atr_ratio(self, atr_ratio: float) -> float:
        """Score ATR ratio (0 = trending, 1 = choppy)."""
        if atr_ratio <= self.atr_ratio_choppy:
            return 1.0
        elif atr_ratio >= self.atr_ratio_trending:
            return 0.0
        else:
            # Linear interpolation
            return 1.0 - (atr_ratio - self.atr_ratio_choppy) / (self.atr_ratio_trending - self.atr_ratio_choppy)
    
    def _score_adx(self, adx: float) -> float:
        """Score ADX (0 = trending, 1 = choppy)."""
        if adx <= self.adx_choppy:
            return 1.0
        elif adx >= self.adx_trending:
            return 0.0
        else:
            return 1.0 - (adx - self.adx_choppy) / (self.adx_trending - self.adx_choppy)
    
    def _score_bb_width(self, bb_width_pct: float) -> float:
        """Score Bollinger Band width (0 = trending, 1 = choppy)."""
        if bb_width_pct <= self.bb_width_choppy:
            return 1.0
        elif bb_width_pct >= self.bb_width_trending:
            return 0.0
        else:
            return 1.0 - (bb_width_pct - self.bb_width_choppy) / (self.bb_width_trending - self.bb_width_choppy)
