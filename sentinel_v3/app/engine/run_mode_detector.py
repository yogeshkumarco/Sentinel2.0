"""
RUN_MODE Detector - Bull/Bear Run Exploitation

This module detects conditions for RUN_MODE activation.
RUN_MODE is designed to exploit strong trending markets with:
- Position scaling (40/30/30)
- Partial profit taking (25/25/50 runner)
- Structure-based trailing stops

ACTIVATION REQUIRES ALL CONDITIONS:
1. HTF Trend (EMA slope + price position)
2. Volatility (ATR expansion after compression)
3. Acceptance (2+ candles beyond range)
4. Confidence (>= 0.80)
"""

import logging
import numpy as np
from typing import Tuple, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger("SentinelBot")


@dataclass
class RunModeConditions:
    """Container for RUN_MODE activation conditions"""
    # HTF Conditions
    htf_trend_valid: bool = False
    ema_slope_direction: str = "NONE"  # BULL, BEAR, NONE
    price_above_ema: bool = False
    clean_structure: bool = False
    
    # Volatility Conditions
    atr_expanding: bool = False
    atr_current: float = 0.0
    atr_compressed: float = 0.0
    
    # Acceptance Conditions
    range_breakout_confirmed: bool = False
    breakout_candles: int = 0
    
    # Confidence
    composite_confidence: float = 0.0
    
    def all_conditions_met(self) -> bool:
        """Returns True only if ALL conditions pass"""
        return (
            self.htf_trend_valid and
            self.atr_expanding and
            self.range_breakout_confirmed and
            self.composite_confidence >= 0.80
        )
    
    def to_dict(self) -> Dict:
        """Convert to dict for logging"""
        return {
            "htf_trend": self.htf_trend_valid,
            "ema_direction": self.ema_slope_direction,
            "atr_expanding": self.atr_expanding,
            "atr_ratio": round(self.atr_current / self.atr_compressed, 2) if self.atr_compressed > 0 else 0,
            "breakout_candles": self.breakout_candles,
            "confidence": round(self.composite_confidence, 2)
        }


class RunModeDetector:
    """
    Detects conditions for RUN_MODE activation.
    
    RUN_MODE should activate RARELY - only during confirmed trend runs.
    """
    
    def __init__(self):
        self.last_check_result: Optional[RunModeConditions] = None
        self.activation_atr: float = 0.0  # ATR at activation (for exit check)
    
    def check_activation(self, df_15m, df_1h) -> Tuple[bool, str, Dict]:
        """
        Check if RUN_MODE should activate.
        
        Args:
            df_15m: 15-minute DataFrame (primary timeframe)
            df_1h: 1-hour DataFrame (HTF context)
            
        Returns:
            Tuple of (is_active, direction, metrics)
            direction: "BULL", "BEAR", or "NONE"
        """
        conditions = RunModeConditions()
        
        try:
            # ============================================
            # 1. HTF CONDITIONS (1H)
            # ============================================
            if df_1h is not None and len(df_1h) >= 60:
                # Use closed candles only
                df_h = df_1h.iloc[:-1].copy()
                
                # EMA 20 and EMA 55 for trend
                df_h['ema_20'] = df_h['close'].ewm(span=20, adjust=False).mean()
                df_h['ema_55'] = df_h['close'].ewm(span=55, adjust=False).mean()
                
                # EMA 20 Slope (last 5 candles)
                ema_20_now = df_h['ema_20'].iloc[-1]
                ema_20_5ago = df_h['ema_20'].iloc[-6]
                ema_slope_pct = (ema_20_now - ema_20_5ago) / ema_20_5ago * 100
                
                # Current price vs EMA 55
                current_price = df_h['close'].iloc[-1]
                ema_55 = df_h['ema_55'].iloc[-1]
                
                # Determine direction
                if ema_slope_pct > 0.3 and current_price > ema_55:
                    conditions.ema_slope_direction = "BULL"
                    conditions.price_above_ema = True
                elif ema_slope_pct < -0.3 and current_price < ema_55:
                    conditions.ema_slope_direction = "BEAR"
                    conditions.price_above_ema = False
                else:
                    conditions.ema_slope_direction = "NONE"
                
                # Clean Structure Check (HH/HL for bull, LL/LH for bear)
                # Check last 10 candles for overlapping bodies (noise)
                last_10 = df_h.iloc[-10:]
                body_overlap_count = 0
                for i in range(1, len(last_10)):
                    prev_body_low = min(last_10.iloc[i-1]['open'], last_10.iloc[i-1]['close'])
                    prev_body_high = max(last_10.iloc[i-1]['open'], last_10.iloc[i-1]['close'])
                    curr_body_low = min(last_10.iloc[i]['open'], last_10.iloc[i]['close'])
                    curr_body_high = max(last_10.iloc[i]['open'], last_10.iloc[i]['close'])
                    
                    # Check if bodies overlap significantly (> 50%)
                    overlap = max(0, min(prev_body_high, curr_body_high) - max(prev_body_low, curr_body_low))
                    prev_body_size = prev_body_high - prev_body_low
                    if prev_body_size > 0 and overlap / prev_body_size > 0.5:
                        body_overlap_count += 1
                
                # Clean structure = less than 4 overlapping candles out of 10
                conditions.clean_structure = body_overlap_count < 4
                
                # HTF Trend Valid = Direction + Clean Structure
                conditions.htf_trend_valid = (
                    conditions.ema_slope_direction != "NONE" and
                    conditions.clean_structure
                )
            
            # ============================================
            # 2. VOLATILITY CONDITIONS (15m ATR)
            # ============================================
            if df_15m is not None and len(df_15m) >= 30:
                df = df_15m.iloc[:-1].copy()  # Closed candles
                
                # Calculate ATR (14-period)
                high_low = df['high'] - df['low']
                high_close = abs(df['high'] - df['close'].shift(1))
                low_close = abs(df['low'] - df['close'].shift(1))
                tr = np.maximum(high_low, np.maximum(high_close, low_close))
                atr = tr.rolling(window=14).mean()
                
                # Current ATR vs ATR from 20 candles ago (compression check)
                atr_now = atr.iloc[-1]
                atr_compressed = atr.iloc[-20:-5].min()  # Min ATR in prior period
                
                conditions.atr_current = atr_now
                conditions.atr_compressed = atr_compressed
                
                # ATR Expanding = Current > 1.3x the compressed level
                if atr_compressed > 0:
                    conditions.atr_expanding = atr_now >= (atr_compressed * 1.3)
            
            # ============================================
            # 3. ACCEPTANCE CONDITIONS (Range Breakout)
            # ============================================
            if df_15m is not None and len(df_15m) >= 50:
                df = df_15m.iloc[:-1].copy()
                
                # Find prior range (candles -50 to -20)
                range_window = df.iloc[-50:-20]
                range_high = range_window['high'].max()
                range_low = range_window['low'].min()
                
                # Check last 5 candles for breakout acceptance
                recent = df.iloc[-5:]
                
                if conditions.ema_slope_direction == "BULL":
                    # Count candles closing ABOVE range high
                    breakout_candles = sum(recent['close'] > range_high)
                    conditions.breakout_candles = breakout_candles
                    # Need 2+ candles closing above AND no wick rejection back
                    last_candle_low = recent.iloc[-1]['low']
                    no_rejection = last_candle_low >= range_high * 0.995  # Within 0.5%
                    conditions.range_breakout_confirmed = breakout_candles >= 2 and no_rejection
                    
                elif conditions.ema_slope_direction == "BEAR":
                    # Count candles closing BELOW range low
                    breakout_candles = sum(recent['close'] < range_low)
                    conditions.breakout_candles = breakout_candles
                    # Need 2+ candles closing below AND no wick rejection back
                    last_candle_high = recent.iloc[-1]['high']
                    no_rejection = last_candle_high <= range_low * 1.005  # Within 0.5%
                    conditions.range_breakout_confirmed = breakout_candles >= 2 and no_rejection
            
            # ============================================
            # 4. CONFIDENCE (Composite Score)
            # ============================================
            # Build confidence from multiple factors
            conf_score = 0.0
            
            if conditions.htf_trend_valid:
                conf_score += 0.30
            if conditions.clean_structure:
                conf_score += 0.20
            if conditions.atr_expanding:
                conf_score += 0.25
            if conditions.range_breakout_confirmed:
                conf_score += 0.25
                
            conditions.composite_confidence = conf_score
            
            # Store for reference
            self.last_check_result = conditions
            
            # Final decision
            if conditions.all_conditions_met():
                self.activation_atr = conditions.atr_current
                return True, conditions.ema_slope_direction, conditions.to_dict()
            else:
                return False, "NONE", conditions.to_dict()
                
        except Exception as e:
            logger.warning(f"RunModeDetector error: {e}")
            return False, "NONE", {}
    
    def check_exit_conditions(self, df_15m, df_1h, direction: str) -> Tuple[bool, str]:
        """
        Check if RUN_MODE should exit.
        
        Returns:
            Tuple of (should_exit, reason)
        """
        try:
            if df_1h is None or len(df_1h) < 20:
                return True, "INSUFFICIENT_DATA"
            
            df_h = df_1h.iloc[:-1].copy()
            latest = df_h.iloc[-1]
            
            # 1. HTF Candle closes against trend
            if direction == "BULL" and latest['close'] < latest['open']:
                # Bearish candle in bull run - potential reversal
                body_size = abs(latest['close'] - latest['open'])
                avg_body = abs(df_h['close'] - df_h['open']).iloc[-10:].mean()
                if body_size > avg_body * 1.2:  # Significant bearish candle
                    return True, "HTF_CANDLE_AGAINST_TREND"
                    
            elif direction == "BEAR" and latest['close'] > latest['open']:
                body_size = abs(latest['close'] - latest['open'])
                avg_body = abs(df_h['close'] - df_h['open']).iloc[-10:].mean()
                if body_size > avg_body * 1.2:
                    return True, "HTF_CANDLE_AGAINST_TREND"
            
            # 2. Structure Break (swing invalidation)
            if direction == "BULL":
                # Find recent swing low
                recent_lows = df_h['low'].iloc[-10:]
                swing_low = recent_lows.min()
                if latest['close'] < swing_low:
                    return True, "STRUCTURE_BREAK_HL_FAILED"
                    
            elif direction == "BEAR":
                recent_highs = df_h['high'].iloc[-10:]
                swing_high = recent_highs.max()
                if latest['close'] > swing_high:
                    return True, "STRUCTURE_BREAK_LH_FAILED"
            
            # 3. Volatility Collapse
            if df_15m is not None and len(df_15m) >= 20:
                df = df_15m.iloc[:-1].copy()
                high_low = df['high'] - df['low']
                high_close = abs(df['high'] - df['close'].shift(1))
                low_close = abs(df['low'] - df['close'].shift(1))
                tr = np.maximum(high_low, np.maximum(high_close, low_close))
                atr = tr.rolling(window=14).mean()
                atr_now = atr.iloc[-1]
                
                if self.activation_atr > 0 and atr_now < (self.activation_atr * 0.5):
                    return True, "VOLATILITY_COLLAPSE"
            
            # 4. Confidence decay (re-check conditions)
            is_active, _, metrics = self.check_activation(df_15m, df_1h)
            if metrics.get('confidence', 0) < 0.65:
                return True, "CONFIDENCE_DECAY"
            
            return False, ""
            
        except Exception as e:
            logger.warning(f"RunMode exit check error: {e}")
            return True, f"ERROR: {e}"
