"""
Pattern Execution Layer - Sentinel Trading System

This module provides a PatternEngine that sits BETWEEN Permission and Entry.
It has VETO power - can block trades if patterns are incomplete or unconfirmed.

RESPONSIBILITIES:
- Pattern completion detection
- Entry timing (avoid early trades)
- Structural Stop Loss placement
- Measured Take Profit projection

DOES NOT DECIDE:
- Market direction
- Whether market is tradable
- Position sizing

PATTERN FAMILIES:
1. CONTINUATION - Bull/Bear flags, ABC, Channel corrections
2. BREAKOUT_RETEST - Range/Compression retests, S/R flips
3. FAILED_MOVE - Failed breakouts, Liquidity sweeps (RUN_MODE only)
"""

import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("SentinelBot")


@dataclass
class SwingPoint:
    """Represents a swing high or low"""
    index: int
    price: float
    is_high: bool  # True = swing high, False = swing low
    timestamp: datetime = None


@dataclass
class PatternResult:
    """Result of pattern analysis"""
    family: str = "NONE"  # CONTINUATION, BREAKOUT_RETEST, FAILED_MOVE, REACTION, NONE
    variant: str = "NONE"  # Specific pattern name
    is_complete: bool = False
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0
    proof_of_move: bool = False
    direction: str = "NONE"  # LONG or SHORT
    # CHANGE 5: Pattern scoring replaces VETO
    # Score affects position sizing: 0.7+ = 100%, 0.5-0.7 = 70%, 0.3-0.5 = 50%, <0.3 = 30%
    score: float = 0.5  # Pattern quality score (0.0 - 1.0)
    log_data: Dict = field(default_factory=dict)
    
    def to_log_dict(self) -> Dict:
        """Convert to logging format"""
        return {
            "timestamp": datetime.now().isoformat(),
            "family": self.family,
            "variant": self.variant,
            "is_complete": self.is_complete,
            "proof_of_move": self.proof_of_move,
            "direction": self.direction,
            "entry": round(self.entry_price, 6),
            "stop": round(self.stop_loss, 6),
            "target": round(self.take_profit, 6),
            "confidence": round(self.confidence, 2),
            **self.log_data
        }


class PatternEngine:
    """
    Pattern Execution Layer with VETO power.
    
    Detects pattern completion and provides structural entry/exit levels.
    Requires Proof-of-Move before allowing execution.
    """
    
    def __init__(self):
        self.swing_lookback = 5  # Candles on each side for swing detection
        self.min_impulse_pct = 3.0  # Minimum % move to qualify as impulse
        self.min_correction_ratio = 0.25  # Correction must retrace at least 25%
        self.max_correction_ratio = 0.75  # Correction must NOT retrace more than 75%
    
    # ============================================
    # MAIN ENTRY POINT
    # ============================================
    
    def analyze(self, df, direction_bias: str, htf_trend: str = None, run_mode: bool = False) -> PatternResult:
        """
        Analyze price structure for tradeable patterns.
        
        CHANGE 5: No longer uses VETO. Instead, assigns scores.
        Score affects position sizing, NOT permission.
        
        Args:
            df: DataFrame with OHLCV data (15m timeframe)
            direction_bias: "LONG" or "SHORT" from decision engine
            htf_trend: "BULL" or "BEAR" from HTF analysis
            run_mode: Whether RUN_MODE is active
            
        Returns:
            PatternResult with entry/stop/target levels, score, and completion status
        """
        result = PatternResult(direction=direction_bias)
        
        if df is None or len(df) < 50:
            # CHANGE 5: Return fallback levels instead of empty result
            return self._get_fallback_levels(df, direction_bias) if df is not None else result
        
        try:
            # Use closed candles only
            df_closed = df.iloc[:-1].copy()
            
            # Find swing points
            swings = self._find_swings(df_closed)
            
            # Calculate ATR for context
            atr = self._calculate_atr(df_closed)
            
            # CHANGE 5: Check all pattern families but DON'T block
            # Just assign scores and return best match
            
            best_result = result
            
            # FAMILY 1: CONTINUATION (Primary)
            cont_result = self._check_continuation_patterns(df_closed, swings, direction_bias, atr)
            if cont_result.is_complete:
                cont_result.proof_of_move = self._check_proof_of_move(df_closed, cont_result.direction, atr)
                cont_result.score = self._calculate_pattern_score(cont_result)
                if cont_result.score > best_result.score:
                    best_result = cont_result
            
            # FAMILY 2: BREAKOUT_RETEST
            retest_result = self._check_breakout_retest_patterns(df_closed, swings, direction_bias, atr)
            if retest_result.is_complete:
                retest_result.proof_of_move = self._check_proof_of_move(df_closed, retest_result.direction, atr)
                retest_result.score = self._calculate_pattern_score(retest_result)
                if retest_result.score > best_result.score:
                    best_result = retest_result
            
            # FAMILY 3: FAILED_MOVE (RUN_MODE only)
            if run_mode:
                failed_result = self._check_failed_move_patterns(df_closed, swings, direction_bias, atr)
                if failed_result.is_complete:
                    failed_result.proof_of_move = self._check_proof_of_move(df_closed, failed_result.direction, atr)
                    failed_result.score = self._calculate_pattern_score(failed_result)
                    if failed_result.score > best_result.score:
                        best_result = failed_result
            
            # FAMILY 4: REACTION PATTERNS (NEW - Always allowed)
            reaction_result = self._check_reaction_patterns(df_closed, swings, direction_bias, atr)
            if reaction_result.is_complete:
                reaction_result.proof_of_move = self._check_proof_of_move(df_closed, reaction_result.direction, atr)
                reaction_result.score = self._calculate_pattern_score(reaction_result)
                if reaction_result.score > best_result.score:
                    best_result = reaction_result
            
            # FAMILY 5: INSTITUTIONAL PATTERNS (Order Block, FVG, Liquidity Sweep)
            institutional_result = self._check_institutional_patterns(df_closed, swings, direction_bias, atr)
            if institutional_result.is_complete:
                institutional_result.proof_of_move = self._check_proof_of_move(df_closed, institutional_result.direction, atr)
                institutional_result.score = self._calculate_pattern_score(institutional_result)
                if institutional_result.score > best_result.score:
                    best_result = institutional_result
            
            # FAMILY 6: CLASSIC PATTERNS (Engulfing, Double Bottom/Top, Flags)
            classic_result = self._check_classic_patterns(df_closed, swings, direction_bias, atr)
            if classic_result.is_complete:
                classic_result.proof_of_move = self._check_proof_of_move(df_closed, classic_result.direction, atr)
                classic_result.score = self._calculate_pattern_score(classic_result)
                if classic_result.score > best_result.score:
                    best_result = classic_result
            
            # CHANGE 5: If no pattern found, return FALLBACK levels with low score
            if best_result.family == "NONE":
                fallback = self._get_fallback_levels(df_closed, direction_bias)
                fallback.score = 0.30  # Low score = small position
                return fallback
            
            return best_result
            
        except Exception as e:
            logger.warning(f"PatternEngine error: {e}")
            # Return fallback instead of empty
            return self._get_fallback_levels(df, direction_bias)
    
    # ============================================
    # SWING DETECTION
    # ============================================
    
    def _find_swings(self, df, lookback: int = None) -> List[SwingPoint]:
        """
        Find swing highs and lows in price structure.
        A swing high has lower highs on both sides.
        A swing low has higher lows on both sides.
        """
        if lookback is None:
            lookback = self.swing_lookback
            
        swings = []
        highs = df['high'].values
        lows = df['low'].values
        
        for i in range(lookback, len(df) - lookback):
            # Check for swing high
            is_swing_high = True
            for j in range(1, lookback + 1):
                if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                    is_swing_high = False
                    break
            
            if is_swing_high:
                swings.append(SwingPoint(
                    index=i,
                    price=highs[i],
                    is_high=True,
                    timestamp=df.index[i] if hasattr(df.index[i], 'isoformat') else None
                ))
            
            # Check for swing low
            is_swing_low = True
            for j in range(1, lookback + 1):
                if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                    is_swing_low = False
                    break
            
            if is_swing_low:
                swings.append(SwingPoint(
                    index=i,
                    price=lows[i],
                    is_high=False,
                    timestamp=df.index[i] if hasattr(df.index[i], 'isoformat') else None
                ))
        
        # Sort by index
        swings.sort(key=lambda x: x.index)
        return swings
    
    def _is_corrective(self, swings: List[SwingPoint]) -> bool:
        """
        Check if structure is corrective (overlapping swings).
        Corrective = Later swing violates earlier swing's territory.
        """
        if len(swings) < 3:
            return False
        
        # Check for overlapping highs/lows
        highs = [s.price for s in swings if s.is_high]
        lows = [s.price for s in swings if not s.is_high]
        
        if len(highs) < 2 or len(lows) < 2:
            return False
        
        # Corrective if range is narrowing or swings overlap
        high_range = max(highs) - min(highs)
        low_range = max(lows) - min(lows)
        total_range = max(highs) - min(lows)
        
        if total_range == 0:
            return False
        
        # Corrective structures have the swings bunched together
        overlap_ratio = (high_range + low_range) / (2 * total_range)
        return overlap_ratio < 0.6  # More overlap = more corrective
    
    def _is_impulsive(self, df, start_idx: int, end_idx: int) -> Tuple[bool, float]:
        """
        Check if move between indices is impulsive.
        Impulsive = Strong directional move with minimal overlap.
        
        Returns:
            (is_impulsive, move_pct)
        """
        if end_idx <= start_idx or end_idx >= len(df):
            return False, 0.0
        
        segment = df.iloc[start_idx:end_idx + 1]
        
        move_high = segment['high'].max()
        move_low = segment['low'].min()
        move_pct = (move_high - move_low) / move_low * 100
        
        # Check for clean trend (minimal body overlap)
        bodies = abs(segment['close'] - segment['open'])
        avg_body = bodies.mean()
        
        # Count how many candles moved in trend direction
        if segment['close'].iloc[-1] > segment['close'].iloc[0]:
            trend_candles = sum(segment['close'] > segment['open'])
        else:
            trend_candles = sum(segment['close'] < segment['open'])
        
        trend_ratio = trend_candles / len(segment)
        
        is_impulsive = move_pct >= self.min_impulse_pct and trend_ratio >= 0.6
        return is_impulsive, move_pct
    
    # ============================================
    # FAMILY 1: CONTINUATION PATTERNS
    # ============================================
    
    def _check_continuation_patterns(self, df, swings: List[SwingPoint], 
                                      direction: str, atr: float) -> PatternResult:
        """
        Detect continuation patterns after correction.
        
        Variants:
        - BULL_FLAG / BEAR_FLAG
        - ABC_CORRECTION
        - RISING_CHANNEL_CORRECTION / FALLING_CHANNEL_CORRECTION
        - COMPLEX_PULLBACK
        """
        result = PatternResult(direction=direction, family="CONTINUATION")
        
        if len(swings) < 4:
            return result
        
        # Get recent swings for pattern detection
        recent_swings = swings[-8:] if len(swings) >= 8 else swings
        
        # Find prior impulse
        impulse_found = False
        impulse_high = 0
        impulse_low = 0
        impulse_end_idx = 0
        
        # Look for impulse before correction
        for i in range(max(0, len(df) - 60), len(df) - 15):
            is_imp, move_pct = self._is_impulsive(df, i, i + 10)
            if is_imp:
                impulse_found = True
                impulse_high = df['high'].iloc[i:i+10].max()
                impulse_low = df['low'].iloc[i:i+10].min()
                impulse_end_idx = i + 10
                break
        
        if not impulse_found:
            result.variant = "NO_PRIOR_IMPULSE"
            return result
        
        # Check if recent structure is corrective
        correction_swings = [s for s in recent_swings if s.index > impulse_end_idx]
        
        if len(correction_swings) < 2:
            result.variant = "CORRECTION_FORMING"
            return result
        
        is_corrective = self._is_corrective(correction_swings)
        
        if not is_corrective:
            result.variant = "NOT_CORRECTIVE"
            return result
        
        # Measure correction depth
        correction_highs = [s.price for s in correction_swings if s.is_high]
        correction_lows = [s.price for s in correction_swings if not s.is_high]
        
        if not correction_highs or not correction_lows:
            return result
        
        impulse_range = impulse_high - impulse_low
        
        if direction == "LONG":
            # Looking for bullish continuation after bearish correction
            correction_low = min(correction_lows)
            retrace_pct = (impulse_high - correction_low) / impulse_range
            
            if self.min_correction_ratio <= retrace_pct <= self.max_correction_ratio:
                # Check for correction structure
                if len(correction_swings) == 3:
                    result.variant = "ABC_CORRECTION"
                elif len(correction_swings) >= 4:
                    result.variant = "COMPLEX_PULLBACK"
                else:
                    result.variant = "BULL_FLAG"
                
                # Check for breakout (pattern completion)
                current_price = df['close'].iloc[-1]
                correction_high = max(correction_highs)
                
                if current_price > correction_high:
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = correction_low - (atr * 0.5)  # Below structure
                    result.take_profit = current_price + impulse_range  # Measured move
                    result.confidence = 0.70 + (0.10 if retrace_pct > 0.38 else 0)
                    
        elif direction == "SHORT":
            # Looking for bearish continuation after bullish correction
            correction_high = max(correction_highs)
            retrace_pct = (correction_high - impulse_low) / impulse_range
            
            if self.min_correction_ratio <= retrace_pct <= self.max_correction_ratio:
                if len(correction_swings) == 3:
                    result.variant = "ABC_CORRECTION"
                elif len(correction_swings) >= 4:
                    result.variant = "COMPLEX_PULLBACK"
                else:
                    result.variant = "BEAR_FLAG"
                
                # Check for breakdown (pattern completion)
                current_price = df['close'].iloc[-1]
                correction_low = min(correction_lows)
                
                if current_price < correction_low:
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = correction_high + (atr * 0.5)  # Above structure
                    result.take_profit = current_price - impulse_range  # Measured move
                    result.confidence = 0.70 + (0.10 if retrace_pct > 0.38 else 0)
        
        result.log_data = {
            "impulse_range": round(impulse_range, 6),
            "correction_swings": len(correction_swings),
            "is_corrective": is_corrective
        }
        
        return result
    
    # ============================================
    # FAMILY 2: BREAKOUT RETEST PATTERNS
    # ============================================
    
    def _check_breakout_retest_patterns(self, df, swings: List[SwingPoint],
                                        direction: str, atr: float) -> PatternResult:
        """
        Detect breakout-retest-continuation patterns.
        
        Variants:
        - RANGE_RETEST
        - COMPRESSION_RETEST
        - SUPPORT_FLIP / RESISTANCE_FLIP
        """
        result = PatternResult(direction=direction, family="BREAKOUT_RETEST")
        
        if len(df) < 40:
            return result
        
        # Find prior range (candles 50-20 ago)
        range_start = max(0, len(df) - 50)
        range_end = len(df) - 15
        
        if range_end <= range_start:
            return result
        
        range_df = df.iloc[range_start:range_end]
        range_high = range_df['high'].max()
        range_low = range_df['low'].min()
        range_size = range_high - range_low
        
        if range_size == 0:
            return result
        
        # Check for compression (smaller range)
        first_half = range_df.iloc[:len(range_df)//2]
        second_half = range_df.iloc[len(range_df)//2:]
        
        first_range = first_half['high'].max() - first_half['low'].min()
        second_range = second_half['high'].max() - second_half['low'].min()
        
        is_compression = second_range < first_range * 0.7
        
        # Get recent price action
        recent_df = df.iloc[-15:]
        current_price = df['close'].iloc[-1]
        
        if direction == "LONG":
            # Look for: Breakout above range -> Retest -> Hold
            
            # Check if we had a breakout
            breakout_candles = sum(recent_df['close'] > range_high)
            
            if breakout_candles >= 2:
                # Check for retest back to range high
                retest_low = recent_df['low'].min()
                
                # Retest should touch the old resistance (now support)
                if retest_low <= range_high * 1.005:  # Within 0.5%
                    # Check if retest held (current price above range)
                    if current_price > range_high:
                        result.variant = "COMPRESSION_RETEST" if is_compression else "RANGE_RETEST"
                        result.is_complete = True
                        result.entry_price = current_price
                        result.stop_loss = retest_low - (atr * 0.5)
                        result.take_profit = current_price + range_size  # Range projection
                        result.confidence = 0.75
                    else:
                        result.variant = "SUPPORT_FLIP"  # Waiting for hold
                        
        elif direction == "SHORT":
            # Look for: Breakdown below range -> Retest -> Fail
            
            breakout_candles = sum(recent_df['close'] < range_low)
            
            if breakout_candles >= 2:
                retest_high = recent_df['high'].max()
                
                if retest_high >= range_low * 0.995:  # Within 0.5%
                    if current_price < range_low:
                        result.variant = "COMPRESSION_RETEST" if is_compression else "RANGE_RETEST"
                        result.is_complete = True
                        result.entry_price = current_price
                        result.stop_loss = retest_high + (atr * 0.5)
                        result.take_profit = current_price - range_size
                        result.confidence = 0.75
                    else:
                        result.variant = "RESISTANCE_FLIP"
        
        result.log_data = {
            "range_size": round(range_size, 6),
            "is_compression": is_compression
        }
        
        return result
    
    # ============================================
    # FAMILY 3: FAILED MOVE PATTERNS (RUN_MODE ONLY)
    # ============================================
    
    def _check_failed_move_patterns(self, df, swings: List[SwingPoint],
                                    direction: str, atr: float) -> PatternResult:
        """
        Detect failed moves and rejections.
        ONLY allowed when RUN_MODE is active.
        
        Variants:
        - FAILED_BREAKOUT
        - FAILED_CONTINUATION
        - LIQUIDITY_SWEEP
        - TREND_EXHAUSTION
        """
        result = PatternResult(direction=direction, family="FAILED_MOVE")
        
        if len(df) < 30 or len(swings) < 3:
            return result
        
        current_price = df['close'].iloc[-1]
        latest = df.iloc[-1]
        
        # Calculate wick sizes
        candle_range = latest['high'] - latest['low']
        if candle_range == 0:
            return result
        
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        lower_wick = min(latest['open'], latest['close']) - latest['low']
        
        upper_wick_pct = upper_wick / candle_range
        lower_wick_pct = lower_wick / candle_range
        
        # Find recent extreme
        recent_high = df['high'].iloc[-20:].max()
        recent_low = df['low'].iloc[-20:].min()
        
        if direction == "LONG":
            # Look for: Sweep below recent low + Strong rejection
            
            swept_low = latest['low'] < recent_low
            strong_rejection = lower_wick_pct >= 0.50  # Wick > 50% of candle
            bullish_close = latest['close'] > latest['open']
            
            if swept_low and strong_rejection and bullish_close:
                # Volatility check
                avg_range = (df['high'] - df['low']).iloc[-10:].mean()
                vol_expansion = candle_range > avg_range * 1.3
                
                if vol_expansion:
                    result.variant = "LIQUIDITY_SWEEP"
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = latest['low'] - (atr * 0.3)
                    result.take_profit = recent_high  # Target prior high
                    result.confidence = 0.65  # Lower confidence for counter-trend
                    
        elif direction == "SHORT":
            # Look for: Sweep above recent high + Strong rejection
            
            swept_high = latest['high'] > recent_high
            strong_rejection = upper_wick_pct >= 0.50
            bearish_close = latest['close'] < latest['open']
            
            if swept_high and strong_rejection and bearish_close:
                avg_range = (df['high'] - df['low']).iloc[-10:].mean()
                vol_expansion = candle_range > avg_range * 1.3
                
                if vol_expansion:
                    result.variant = "LIQUIDITY_SWEEP"
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = latest['high'] + (atr * 0.3)
                    result.take_profit = recent_low
                    result.confidence = 0.65
        
        result.log_data = {
            "upper_wick_pct": round(upper_wick_pct, 2),
            "lower_wick_pct": round(lower_wick_pct, 2),
            "recent_high": round(recent_high, 6),
            "recent_low": round(recent_low, 6)
        }
        
        return result
    
    # ============================================
    # PROOF-OF-MOVE GATE (MANDATORY)
    # ============================================
    
    def _check_proof_of_move(self, df, direction: str, atr: float) -> bool:
        """
        Before ANY entry, require at least ONE proof:
        1. Strong displacement candle (> 1.5x ATR body)
        2. Break-and-hold beyond structure
        3. Volatility expansion vs recent bars
        4. Acceptance (2-3 candles holding direction)
        
        Returns:
            True if at least one proof condition is met
        """
        if len(df) < 5:
            return False
        
        recent = df.iloc[-5:]
        latest = df.iloc[-1]
        
        # 1. DISPLACEMENT CANDLE
        body_size = abs(latest['close'] - latest['open'])
        displacement_threshold = atr * 1.5
        has_displacement = body_size >= displacement_threshold
        
        if direction == "LONG":
            displacement_correct_direction = latest['close'] > latest['open']
        else:
            displacement_correct_direction = latest['close'] < latest['open']
        
        displacement_proof = has_displacement and displacement_correct_direction
        
        # 2. VOLATILITY EXPANSION
        current_range = latest['high'] - latest['low']
        avg_range = (df['high'] - df['low']).iloc[-10:-1].mean()
        vol_expansion = current_range >= avg_range * 1.3
        
        # 3. ACCEPTANCE (2-3 candles holding)
        if direction == "LONG":
            acceptance_candles = sum(recent['close'] > recent['open'])
            price_trending_up = recent['close'].iloc[-1] > recent['close'].iloc[0]
        else:
            acceptance_candles = sum(recent['close'] < recent['open'])
            price_trending_up = recent['close'].iloc[-1] < recent['close'].iloc[0]
        
        acceptance_proof = acceptance_candles >= 3 and price_trending_up
        
        # 4. BREAK-AND-HOLD (price holding beyond prior swing)
        prior_highs = df['high'].iloc[-15:-5]
        prior_lows = df['low'].iloc[-15:-5]
        
        if direction == "LONG":
            prior_high = prior_highs.max()
            break_hold = latest['close'] > prior_high and recent['low'].iloc[-1] > prior_high * 0.998
        else:
            prior_low = prior_lows.min()
            break_hold = latest['close'] < prior_low and recent['high'].iloc[-1] < prior_low * 1.002
        
        # ANY ONE proof is sufficient
        proof = displacement_proof or vol_expansion or acceptance_proof or break_hold
        
        if proof:
            proof_types = []
            if displacement_proof: proof_types.append("DISPLACEMENT")
            if vol_expansion: proof_types.append("VOL_EXPANSION")
            if acceptance_proof: proof_types.append("ACCEPTANCE")
            if break_hold: proof_types.append("BREAK_HOLD")
            logger.debug(f"✅ PROOF_OF_MOVE: {', '.join(proof_types)}")
        
        return proof
    
    # ============================================
    # UTILITIES
    # ============================================
    
    def _calculate_atr(self, df, period: int = 14) -> float:
        """Calculate Average True Range"""
        if len(df) < period + 1:
            return (df['high'] - df['low']).mean()
        
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift(1))
        low_close = abs(df['low'] - df['close'].shift(1))
        
        tr = np.maximum(high_low, np.maximum(high_close, low_close))
        atr = tr.rolling(window=period).mean().iloc[-1]
        
        return atr if not np.isnan(atr) else (df['high'] - df['low']).mean()
    
    # ============================================
    # CHANGE 5: PATTERN SCORING (Replaces VETO)
    # ============================================
    
    def _calculate_pattern_score(self, pattern: PatternResult) -> float:
        """
        Calculate pattern quality score (0.0 - 1.0).
        
        Score tiers:
        - 0.70+ = Full position size (100%)
        - 0.50-0.70 = Reduced size (70%)
        - 0.30-0.50 = Small size (50%)
        - <0.30 = Minimum size (30%)
        """
        score = 0.50  # Base score
        
        # Family bonus
        family_scores = {
            "CONTINUATION": 0.20,
            "BREAKOUT_RETEST": 0.15,
            "REACTION": 0.10,
            "FAILED_MOVE": 0.05,
            "INSTITUTIONAL": 0.20,  # High priority - Order Blocks, FVG, Liquidity
            "CLASSIC": 0.15  # Engulfing, Double Bottom/Top, Flags
        }
        score += family_scores.get(pattern.family, 0.0)
        
        # Completion bonus
        if pattern.is_complete:
            score += 0.15
        
        # Proof-of-move bonus
        if pattern.proof_of_move:
            score += 0.10
        
        # Confidence bonus
        score += pattern.confidence * 0.10
        
        # Cap at 1.0
        return min(1.0, score)
    
    def _get_fallback_levels(self, df, direction_bias: str) -> PatternResult:
        """
        Provide structural SL/TP when no named pattern is found.
        Uses simple swing high/low for levels.
        """
        result = PatternResult(
            family="FALLBACK",
            variant="SWING_STRUCTURE",
            direction=direction_bias,
            is_complete=True,
            score=0.30  # Low score = small position
        )
        
        if df is None or len(df) < 20:
            return result
        
        try:
            current_price = df['close'].iloc[-1]
            atr = self._calculate_atr(df)
            
            # Find recent swings for levels
            recent_high = df['high'].iloc[-20:].max()
            recent_low = df['low'].iloc[-20:].min()
            
            if direction_bias == "LONG":
                result.entry_price = current_price
                result.stop_loss = recent_low - (atr * 0.5)
                result.take_profit = recent_high + (atr * 2)
            else:
                result.entry_price = current_price
                result.stop_loss = recent_high + (atr * 0.5)
                result.take_profit = recent_low - (atr * 2)
            
            result.proof_of_move = True  # Fallback always allows entry
            
        except Exception as e:
            logger.warning(f"Fallback levels error: {e}")
        
        return result
    
    # ============================================
    # FAMILY 4: REACTION PATTERNS (NEW)
    # V_REVERSAL, FAILED_BREAKDOWN, CAPITULATION_WICK
    # ============================================
    
    def _check_reaction_patterns(self, df, swings: List[SwingPoint],
                                  direction: str, atr: float) -> PatternResult:
        """
        Detect reaction/reversal patterns.
        These trade AGAINST recent impulse moves.
        
        Variants:
        - V_REVERSAL: Liquidity flush + immediate reclaim
        - FAILED_BREAKDOWN: Break below support, reclaim above
        - CAPITULATION_WICK: Long wick rejection, body against direction
        """
        result = PatternResult(direction=direction, family="REACTION")
        
        if len(df) < 20:
            return result
        
        current_price = df['close'].iloc[-1]
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        
        # Calculate wick metrics
        candle_range = latest['high'] - latest['low']
        if candle_range == 0:
            return result
        
        body_size = abs(latest['close'] - latest['open'])
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        lower_wick = min(latest['open'], latest['close']) - latest['low']
        
        upper_wick_pct = upper_wick / candle_range
        lower_wick_pct = lower_wick / candle_range
        
        # Find recent extremes
        recent_high = df['high'].iloc[-20:].max()
        recent_low = df['low'].iloc[-20:].min()
        prior_high = df['high'].iloc[-10:-1].max()
        prior_low = df['low'].iloc[-10:-1].min()
        
        if direction == "LONG":
            # V_REVERSAL: Swept low, closed bullish above prior low
            swept_low = latest['low'] < prior_low
            bullish_close = latest['close'] > latest['open']
            reclaimed = latest['close'] > prior_low
            
            if swept_low and bullish_close and reclaimed:
                result.variant = "V_REVERSAL"
                result.is_complete = True
                result.entry_price = current_price
                result.stop_loss = latest['low'] - (atr * 0.3)
                result.take_profit = prior_high
                result.confidence = 0.65
                return result
            
            # FAILED_BREAKDOWN: Closed below support, next candle reclaims
            prev_closed_below = prev['close'] < prior_low
            current_reclaimed = latest['close'] > prior_low
            
            if prev_closed_below and current_reclaimed and bullish_close:
                result.variant = "FAILED_BREAKDOWN"
                result.is_complete = True
                result.entry_price = current_price
                result.stop_loss = min(latest['low'], prev['low']) - (atr * 0.3)
                result.take_profit = prior_high
                result.confidence = 0.60
                return result
            
            # CAPITULATION_WICK: Lower wick > 70% of candle, bullish close
            if lower_wick_pct >= 0.70 and bullish_close:
                result.variant = "CAPITULATION_WICK"
                result.is_complete = True
                result.entry_price = current_price
                result.stop_loss = latest['low'] - (atr * 0.2)
                result.take_profit = latest['high'] + (2 * body_size)
                result.confidence = 0.55
                return result
                
        elif direction == "SHORT":
            # V_REVERSAL: Swept high, closed bearish below prior high
            swept_high = latest['high'] > prior_high
            bearish_close = latest['close'] < latest['open']
            reclaimed = latest['close'] < prior_high
            
            if swept_high and bearish_close and reclaimed:
                result.variant = "V_REVERSAL"
                result.is_complete = True
                result.entry_price = current_price
                result.stop_loss = latest['high'] + (atr * 0.3)
                result.take_profit = prior_low
                result.confidence = 0.65
                return result
            
            # FAILED_BREAKDOWN: Closed above resistance, next candle rejects
            prev_closed_above = prev['close'] > prior_high
            current_reclaimed = latest['close'] < prior_high
            
            if prev_closed_above and current_reclaimed and bearish_close:
                result.variant = "FAILED_BREAKDOWN"
                result.is_complete = True
                result.entry_price = current_price
                result.stop_loss = max(latest['high'], prev['high']) + (atr * 0.3)
                result.take_profit = prior_low
                result.confidence = 0.60
                return result
            
            # CAPITULATION_WICK: Upper wick > 70% of candle, bearish close
            if upper_wick_pct >= 0.70 and bearish_close:
                result.variant = "CAPITULATION_WICK"
                result.is_complete = True
                result.entry_price = current_price
                result.stop_loss = latest['high'] + (atr * 0.2)
                result.take_profit = latest['low'] - (2 * body_size)
                result.confidence = 0.55
                return result
        
        return result

    # ============================================
    # FAMILY 5: INSTITUTIONAL PATTERNS
    # Order Blocks, Fair Value Gaps, Liquidity Sweeps
    # ============================================
    
    def _check_institutional_patterns(self, df, swings: List[SwingPoint],
                                      direction: str, atr: float) -> PatternResult:
        """
        Detect institutional trading patterns.
        
        Variants:
        - ORDER_BLOCK: Last bearish candle before bullish move (for LONG) or vice versa
        - FAIR_VALUE_GAP: Price imbalance between candles
        - LIQUIDITY_SWEEP: Stop hunt followed by reversal
        """
        result = PatternResult(family="INSTITUTIONAL", direction=direction)
        
        if df is None or len(df) < 30:
            return result
        
        try:
            latest = df.iloc[-1]
            current_price = df['close'].iloc[-1]
            
            # ============================================
            # 1. PROPULSION BLOCK (Strong Momentum)
            # Single massive candle (body > 2x ATR) - entry on retracement
            # ============================================
            for i in range(-15, -3):
                candle = df.iloc[i]
                body_size = abs(candle['close'] - candle['open'])
                is_huge = body_size > (atr * 2.0)
                
                if is_huge:
                    if direction == "LONG" and candle['close'] > candle['open']:
                        entry_level = candle['open'] + (body_size * 0.5)
                        if current_price <= entry_level * 1.005 and current_price >= candle['low']:
                            result.variant = "PROPULSION_BLOCK"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = candle['low'] - (atr * 0.2)
                            result.take_profit = current_price + (atr * 4)
                            result.confidence = 0.75
                            return result
                    elif direction == "SHORT" and candle['close'] < candle['open']:
                        entry_level = candle['open'] - (body_size * 0.5)
                        if current_price >= entry_level * 0.995 and current_price <= candle['high']:
                            result.variant = "PROPULSION_BLOCK"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = candle['high'] + (atr * 0.2)
                            result.take_profit = current_price - (atr * 4)
                            result.confidence = 0.75
                            return result

            # ============================================
            # 2. DECISIVE BLOCK (Engulfing + Displacement)
            # Engulfing candle that initiated a strong move
            # ============================================
            for i in range(-10, -2):
                prev = df.iloc[i-1]
                curr = df.iloc[i]
                
                if direction == "LONG":
                    is_engulfing = curr['close'] > prev['high'] and curr['open'] < prev['low']
                    is_strong = (curr['close'] - curr['open']) > atr
                    if is_engulfing and is_strong:
                        if current_price <= curr['close'] and current_price >= curr['open']:
                            result.variant = "DECISIVE_OB"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = curr['low'] - (atr * 0.2)
                            result.take_profit = curr['high'] + (atr * 3)
                            result.confidence = 0.72
                            return result
                elif direction == "SHORT":
                    is_engulfing = curr['close'] < prev['low'] and curr['open'] > prev['high']
                    is_strong = (curr['open'] - curr['close']) > atr
                    if is_engulfing and is_strong:
                        if current_price >= curr['close'] and current_price <= curr['open']:
                            result.variant = "DECISIVE_OB"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = curr['high'] + (atr * 0.2)
                            result.take_profit = curr['low'] - (atr * 3)
                            result.confidence = 0.72
                            return result

            # ============================================
            # 3. RELAXED ORDER BLOCK (2/3 candles, 0.5% move)
            # Same concept as classic OB but easier to trigger
            # ============================================
            for i in range(-12, -3):
                candle = df.iloc[i]
                next_candles = df.iloc[i+1:i+4]
                
                if len(next_candles) < 2:
                    continue
                
                if direction == "LONG":
                    is_bearish = candle['close'] < candle['open']
                    bullish_count = sum(1 for _, c in next_candles.iterrows() if c['close'] > c['open'])
                    move_pct = (next_candles['high'].max() - candle['low']) / candle['low'] * 100
                    
                    if is_bearish and bullish_count >= 1 and move_pct > 0.5:
                        ob_high = candle['high']
                        ob_low = candle['low']
                        
                        if ob_low <= current_price <= ob_high * 1.01:
                            result.variant = "ORDER_BLOCK"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = ob_low - (atr * 0.2)
                            result.take_profit = ob_high + (atr * 3)
                            result.confidence = 0.68
                            result.sl_zone = (ob_low, ob_low - atr * 0.2)
                            return result
                            
                elif direction == "SHORT":
                    is_bullish = candle['close'] > candle['open']
                    bearish_count = sum(1 for _, c in next_candles.iterrows() if c['close'] < c['open'])
                    move_pct = (candle['high'] - next_candles['low'].min()) / candle['high'] * 100
                    
                    if is_bullish and bearish_count >= 1 and move_pct > 0.5:
                        ob_high = candle['high']
                        ob_low = candle['low']
                        
                        if ob_low * 0.99 <= current_price <= ob_high:
                            result.variant = "ORDER_BLOCK"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = ob_high + (atr * 0.2)
                            result.take_profit = ob_low - (atr * 3)
                            result.confidence = 0.68
                            result.sl_zone = (ob_high, ob_high + atr * 0.2)
                            return result

            # ============================================
            # 4. BREAKER BLOCK (Failed Level Flip)
            # Old support becomes resistance (or vice versa)
            # ============================================
            for i in range(-20, -5):
                candle = df.iloc[i]
                window = df.iloc[max(0, i-2):i+3]
                
                if direction == "LONG":
                    is_swing_low = candle['low'] == window['low'].min()
                    if is_swing_low:
                        broke_below = df.iloc[i+1:-1]['close'].min() < candle['low']
                        now_above = current_price >= candle['low'] * 0.995 and current_price <= candle['low'] * 1.005
                        if broke_below and now_above:
                            result.variant = "BREAKER_BLOCK"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = candle['low'] - (atr * 0.5)
                            result.take_profit = current_price + (atr * 2.5)
                            result.confidence = 0.65
                            return result
                
                elif direction == "SHORT":
                    is_swing_high = candle['high'] == window['high'].max()
                    if is_swing_high:
                        broke_above = df.iloc[i+1:-1]['close'].max() > candle['high']
                        now_below = current_price <= candle['high'] * 1.005 and current_price >= candle['high'] * 0.995
                        if broke_above and now_below:
                            result.variant = "BREAKER_BLOCK"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = candle['high'] + (atr * 0.5)
                            result.take_profit = current_price - (atr * 2.5)
                            result.confidence = 0.65
                            return result

            # ============================================
            # 5. MITIGATION BLOCK (Old Unfilled OB)
            # OB from 20-50 candles ago that was never retested
            # ============================================
            for i in range(-50, -20):
                if abs(i) >= len(df):
                    continue
                candle = df.iloc[i]
                next_idx = min(i + 3, -1)
                next_candles = df.iloc[i+1:next_idx+1]
                
                if len(next_candles) < 2:
                    continue
                
                if direction == "LONG":
                    is_bearish = candle['close'] < candle['open']
                    move_up = next_candles['close'].max() > candle['high']
                    
                    if is_bearish and move_up:
                        ob_high = candle['high']
                        ob_low = candle['low']
                        # Check price never came back to OB between then and now
                        between = df.iloc[next_idx+1:-1]
                        if len(between) > 0:
                            never_retested = between['low'].min() > ob_high
                            now_at_ob = ob_low <= current_price <= ob_high * 1.01
                            if never_retested and now_at_ob:
                                result.variant = "MITIGATION_BLOCK"
                                result.is_complete = True
                                result.entry_price = current_price
                                result.stop_loss = ob_low - (atr * 0.3)
                                result.take_profit = ob_high + (atr * 4)
                                result.confidence = 0.62
                                return result
                
                elif direction == "SHORT":
                    is_bullish = candle['close'] > candle['open']
                    move_down = next_candles['close'].min() < candle['low']
                    
                    if is_bullish and move_down:
                        ob_high = candle['high']
                        ob_low = candle['low']
                        between = df.iloc[next_idx+1:-1]
                        if len(between) > 0:
                            never_retested = between['high'].max() < ob_low
                            now_at_ob = ob_low * 0.99 <= current_price <= ob_high
                            if never_retested and now_at_ob:
                                result.variant = "MITIGATION_BLOCK"
                                result.is_complete = True
                                result.entry_price = current_price
                                result.stop_loss = ob_high + (atr * 0.3)
                                result.take_profit = ob_low - (atr * 4)
                                result.confidence = 0.62
                                return result
            
            # ============================================
            # FAIR VALUE GAP (FVG) DETECTION
            # Gap between candle 1's low and candle 3's high (for bullish FVG)
            # ============================================
            
            for i in range(-15, -3):
                c1 = df.iloc[i]      # First candle
                c2 = df.iloc[i+1]    # Middle candle (the big move)
                c3 = df.iloc[i+2]    # Third candle
                
                if direction == "LONG":
                    # Bullish FVG: Gap between c1's high and c3's low
                    fvg_top = c3['low']
                    fvg_bottom = c1['high']
                    gap_exists = fvg_top > fvg_bottom
                    gap_size = (fvg_top - fvg_bottom) / fvg_bottom * 100
                    
                    if gap_exists and gap_size > 0.3:
                        # Check if price is near or inside FVG
                        if fvg_bottom <= current_price <= fvg_top * 1.01:
                            result.variant = "FAIR_VALUE_GAP"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = fvg_bottom - (atr * 0.5)
                            result.take_profit = fvg_top + (atr * 3)
                            result.confidence = 0.65
                            return result
                            
                elif direction == "SHORT":
                    # Bearish FVG: Gap between c1's low and c3's high
                    fvg_top = c1['low']
                    fvg_bottom = c3['high']
                    gap_exists = fvg_top > fvg_bottom
                    gap_size = (fvg_top - fvg_bottom) / fvg_bottom * 100
                    
                    if gap_exists and gap_size > 0.3:
                        if fvg_bottom * 0.99 <= current_price <= fvg_top:
                            result.variant = "FAIR_VALUE_GAP"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = fvg_top + (atr * 0.5)
                            result.take_profit = fvg_bottom - (atr * 3)
                            result.confidence = 0.65
                            return result
            
            # ============================================
            # LIQUIDITY SWEEP DETECTION
            # Price sweeps beyond recent high/low then reverses
            # ============================================
            
            recent_high = df['high'].iloc[-20:-3].max()
            recent_low = df['low'].iloc[-20:-3].min()
            
            if direction == "LONG":
                # Swept low and closed back above
                swept_low = latest['low'] < recent_low
                closed_above = latest['close'] > recent_low
                bullish_candle = latest['close'] > latest['open']
                
                if swept_low and closed_above and bullish_candle:
                    result.variant = "LIQUIDITY_SWEEP"
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = latest['low'] - (atr * 0.2)
                    result.take_profit = recent_high
                    result.confidence = 0.68
                    return result
                    
            elif direction == "SHORT":
                # Swept high and closed back below
                swept_high = latest['high'] > recent_high
                closed_below = latest['close'] < recent_high
                bearish_candle = latest['close'] < latest['open']
                
                if swept_high and closed_below and bearish_candle:
                    result.variant = "LIQUIDITY_SWEEP"
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = latest['high'] + (atr * 0.2)
                    result.take_profit = recent_low
                    result.confidence = 0.68
                    return result
            
        except Exception as e:
            logger.warning(f"Institutional pattern detection error: {e}")
        
        return result
    
    # ============================================
    # FAMILY 6: CLASSIC PATTERNS
    # Double Bottom/Top, Flags, Engulfing
    # ============================================
    
    def _check_classic_patterns(self, df, swings: List[SwingPoint],
                                direction: str, atr: float) -> PatternResult:
        """
        Detect classic technical patterns.
        
        Variants:
        - DOUBLE_BOTTOM: Two equal lows with neckline break
        - DOUBLE_TOP: Two equal highs with neckline break
        - BULL_FLAG: Tight consolidation after upward impulse
        - BEAR_FLAG: Tight consolidation after downward impulse
        - BULLISH_ENGULFING: Large bullish candle engulfs prior bearish
        - BEARISH_ENGULFING: Large bearish candle engulfs prior bullish
        """
        result = PatternResult(family="CLASSIC", direction=direction)
        
        if df is None or len(df) < 30:
            return result
        
        try:
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            current_price = df['close'].iloc[-1]
            
            # ============================================
            # ENGULFING PATTERN DETECTION (Quick, high priority)
            # ============================================
            
            if direction == "LONG":
                # Bullish Engulfing: Current bullish candle fully engulfs prior bearish
                prior_bearish = prev['close'] < prev['open']
                current_bullish = latest['close'] > latest['open']
                engulfs_body = (latest['open'] < prev['close']) and (latest['close'] > prev['open'])
                engulfs_full = (latest['low'] <= prev['low']) and (latest['high'] >= prev['high'])
                
                if prior_bearish and current_bullish and engulfs_body:
                    result.variant = "BULLISH_ENGULFING"
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = latest['low'] - (atr * 0.3)
                    result.take_profit = latest['high'] + (atr * 2)
                    result.confidence = 0.62 if engulfs_full else 0.55
                    return result
                    
            elif direction == "SHORT":
                # Bearish Engulfing
                prior_bullish = prev['close'] > prev['open']
                current_bearish = latest['close'] < latest['open']
                engulfs_body = (latest['open'] > prev['close']) and (latest['close'] < prev['open'])
                engulfs_full = (latest['low'] <= prev['low']) and (latest['high'] >= prev['high'])
                
                if prior_bullish and current_bearish and engulfs_body:
                    result.variant = "BEARISH_ENGULFING"
                    result.is_complete = True
                    result.entry_price = current_price
                    result.stop_loss = latest['high'] + (atr * 0.3)
                    result.take_profit = latest['low'] - (atr * 2)
                    result.confidence = 0.62 if engulfs_full else 0.55
                    return result
            
            # ============================================
            # DOUBLE BOTTOM / DOUBLE TOP DETECTION
            # ============================================
            
            # Find swing lows for double bottom
            swing_lows = [s for s in swings if not s.is_high]
            swing_highs = [s for s in swings if s.is_high]
            
            if direction == "LONG" and len(swing_lows) >= 2:
                # Look for two roughly equal lows
                for i in range(len(swing_lows) - 1):
                    low1 = swing_lows[i]
                    low2 = swing_lows[i + 1]
                    
                    # Check if lows are similar (within 1%)
                    low_diff_pct = abs(low1.price - low2.price) / low1.price * 100
                    
                    if low_diff_pct < 1.5:
                        # Find neckline (high between the two lows)
                        between_idx = [s for s in swing_highs 
                                      if s.index > low1.index and s.index < low2.index]
                        if between_idx:
                            neckline = max(between_idx, key=lambda x: x.price).price
                            
                            # Check if price broke above neckline
                            if current_price > neckline:
                                result.variant = "DOUBLE_BOTTOM"
                                result.is_complete = True
                                result.entry_price = current_price
                                result.stop_loss = min(low1.price, low2.price) - (atr * 0.3)
                                result.take_profit = neckline + (neckline - min(low1.price, low2.price))
                                result.confidence = 0.70
                                return result
                                
            elif direction == "SHORT" and len(swing_highs) >= 2:
                # Look for two roughly equal highs
                for i in range(len(swing_highs) - 1):
                    high1 = swing_highs[i]
                    high2 = swing_highs[i + 1]
                    
                    high_diff_pct = abs(high1.price - high2.price) / high1.price * 100
                    
                    if high_diff_pct < 1.5:
                        between_idx = [s for s in swing_lows 
                                      if s.index > high1.index and s.index < high2.index]
                        if between_idx:
                            neckline = min(between_idx, key=lambda x: x.price).price
                            
                            if current_price < neckline:
                                result.variant = "DOUBLE_TOP"
                                result.is_complete = True
                                result.entry_price = current_price
                                result.stop_loss = max(high1.price, high2.price) + (atr * 0.3)
                                result.take_profit = neckline - (max(high1.price, high2.price) - neckline)
                                result.confidence = 0.70
                                return result
            
            # ============================================
            # FLAG PATTERN DETECTION
            # ============================================
            
            # Check for recent impulse followed by tight consolidation
            if len(df) >= 20:
                impulse_window = df.iloc[-20:-10]
                flag_window = df.iloc[-10:]
                
                impulse_range = impulse_window['high'].max() - impulse_window['low'].min()
                flag_range = flag_window['high'].max() - flag_window['low'].min()
                
                impulse_pct = impulse_range / impulse_window['low'].min() * 100
                flag_compression = flag_range / impulse_range
                
                if direction == "LONG":
                    # Bull Flag: Strong up move followed by tight consolidation
                    impulse_up = impulse_window['close'].iloc[-1] > impulse_window['close'].iloc[0]
                    
                    if impulse_pct > 3.0 and flag_compression < 0.4 and impulse_up:
                        # Flag is tight, check for breakout
                        flag_high = flag_window['high'].max()
                        if current_price > flag_high:
                            result.variant = "BULL_FLAG"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = flag_window['low'].min() - (atr * 0.3)
                            result.take_profit = flag_high + impulse_range
                            result.confidence = 0.68
                            return result
                            
                elif direction == "SHORT":
                    # Bear Flag
                    impulse_down = impulse_window['close'].iloc[-1] < impulse_window['close'].iloc[0]
                    
                    if impulse_pct > 3.0 and flag_compression < 0.4 and impulse_down:
                        flag_low = flag_window['low'].min()
                        if current_price < flag_low:
                            result.variant = "BEAR_FLAG"
                            result.is_complete = True
                            result.entry_price = current_price
                            result.stop_loss = flag_window['high'].max() + (atr * 0.3)
                            result.take_profit = flag_low - impulse_range
                            result.confidence = 0.68
                            return result
            
        except Exception as e:
            logger.warning(f"Classic pattern detection error: {e}")
        
        return result
