# Decision Engine - Generates structured trading decisions

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
import json
import logging


import sys
# sys.path.append('..')
from app.core.config import (
    settings as config, MarketState, DirectionBias, Recommendation,
    VolatilityLabel
)
# Note: regime_classifier is in the same folder, so relative import .regime_classifier works,
# but let's be safe with absolute or relative to package.
from app.models.Model_Sentinel.regime_classifier import RegimeClassifier
from app.models.Model_Sentinel.harmonic_patterns import HarmonicDetector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class TradingDecision:
    """Structured trading decision output"""
    market_state: str
    direction_bias: str
    setup_quality: float
    confidence: float
    recommendation: str
    reasoning: str
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class DecisionEngine:
    """
    Generate trading decisions based on market analysis
    
    Decision Logic:
    1. Classify market regime
    2. Score setup quality
    3. Determine direction bias
    4. Calculate confidence
    5. Generate recommendation
    """
    
    def __init__(self):
        self.regime_classifier = RegimeClassifier()
        self.harmonic_detector = HarmonicDetector(error_tolerance=0.10)
        
        # Thresholds from config
        self.min_quality = config.model.min_setup_quality
        self.min_confidence_allow = config.model.min_confidence_allow
        self.min_confidence_micro = config.model.min_confidence_micro
    
    # [NEW] Updated signature to support Symbol-based Leverage
    def calculate_dynamic_leverage(self, symbol: str, volatility_score: float, quality: float, confidence: float) -> int:
        """
        Calculate safe leverage based on Symbol Tier, Volatility, and Setup Quality.
        
        Formula:
            Base_Lev = Max_Tier_Lev
            Adj_Lev = Base_Lev * (Confidence_Scalar / Volatility_Scalar)
        """
        # 1. Get Coin Tier & Max Leverage
        risk_cfg = config.risk
        # Default to Tier 3 (Lowest risk/leverage) if unknown
        tier = risk_cfg.coin_tiers.get(symbol, 3) 
        max_lev = risk_cfg.max_leverage_tier.get(tier, 5)
        
        # 2. Adjust for Volatility (Higher Vol = Lower Lev)
        # VolScore 1.0 = Normal. 2.0 = High.
        # We want to reduce leverage aggressively as vol increases.
        vol_dampener = max(1.0, volatility_score) 
        
        # 3. Adjust for Quality/Confidence
        # High confidence (0.9) stays near max. Low confidence (0.5) cuts it in half.
        conf_multiplier = confidence 
        
        safe_lev = (max_lev * conf_multiplier) / vol_dampener
        
        # 4. Hard Limits & Rounding
        final_lev = int(safe_lev)
        return max(1, min(final_lev, max_lev))

    def analyze(self, df: pd.DataFrame, symbol: str = "UNKNOWN") -> TradingDecision:
        """
        Analyze market data and generate decision
        
        Args:
            df: DataFrame with OHLCV and computed features
            symbol: Symbol name for leverage calculation
        """
        if len(df) < 10:
            return TradingDecision(
                market_state=MarketState.RANGE.value,
                direction_bias=DirectionBias.NONE.value,
                setup_quality=0.0,
                confidence=0.0,
                recommendation=Recommendation.NO_TRADE.value,
                reasoning="Insufficient data for analysis"
            )
        
        latest = df.iloc[-1]
        
        # [NEW] Velocity Check (Anti-Waterfall)
        # If RSI drops > 15 points in 3 bars, it's a crash. Don't Long.
        rsi_slope = latest.get('rsi_slope', 0)
        
        # 1. Classify regime
        df_classified = self.regime_classifier.classify(df)
        regime = df_classified.iloc[-1].get('regime', MarketState.RANGE.value)
        
        # 2. Score setup quality
        setup_quality = self._calculate_setup_quality(df_classified)
        
        # 3. Determine direction
        direction = self._determine_direction(df_classified, regime)
        
        # [HARMONIC OVERRIDE] 
        # Check for Strong Harmonic Patterns (Reversal Signals)
        # These can OVERRIDE the trend bias or NONE bias.
        try:
            harmonics = self.harmonic_detector.detect(df_classified)
            if harmonics:
                best_pattern = max(harmonics, key=lambda p: p.confidence)
                if best_pattern.confidence >= 0.7:
                    logger.info(f"🎯 STRONG HARMONIC: {best_pattern.name} ({best_pattern.confidence:.2f}) -> Forcing Reversal")
                    
                    # Force Direction
                    direction = DirectionBias.LONG.value if best_pattern.bullish else DirectionBias.SHORT.value
                    
                    # Boost Quality (Pattern is the Setup)
                    setup_quality = max(setup_quality + 0.3, 0.65) # Ensure it triggers trade
                    setup_quality = min(setup_quality, 1.0)
                    
                    # Force Regime annotation
                    regime = "HARMONIC_REVERSAL"
        except Exception as e:
            logger.warning(f"Harmonic check failed: {e}")

        # [NEW] Directional Velocity Filter
        if direction == DirectionBias.LONG.value and rsi_slope < -10:
            # Only block if NOT a harmonic reversal (Harmonics fade the drop)
            if regime != "HARMONIC_REVERSAL":
                direction = DirectionBias.NONE.value
                setup_quality = 0.0
                # Force No Trade
                return TradingDecision(
                    market_state=regime, direction_bias="NONE", setup_quality=0, confidence=0,
                    recommendation="NO_TRADE", reasoning=f"Falling Knife (RSI Slope {rsi_slope:.1f})"
                )
        elif direction == DirectionBias.SHORT.value and rsi_slope > 10:
            if regime != "HARMONIC_REVERSAL":
                direction = DirectionBias.NONE.value
                setup_quality = 0.0
                return TradingDecision(
                     market_state=regime, direction_bias="NONE", setup_quality=0, confidence=0,
                    recommendation="NO_TRADE", reasoning=f"Rocketing Up (RSI Slope {rsi_slope:.1f})"
                )

        # 4. Calculate confidence
        confidence = self._calculate_confidence(df_classified, regime, setup_quality)
        
        # 5. Generate recommendation
        recommendation, reasoning = self._generate_recommendation(
            regime, direction, setup_quality, confidence, df_classified
        )
        
        # SCALP MODE INTERCEPT
        if getattr(config.trading, 'strategy_mode', 'sniper') == 'scalp':
            return self.analyze_scalp(df, regime)

        return TradingDecision(
            market_state=regime,
            direction_bias=direction,
            setup_quality=round(setup_quality, 3),
            confidence=round(confidence, 3),
            recommendation=recommendation,
            reasoning=reasoning
        )

    def analyze_scalp(self, df: pd.DataFrame, regime: str) -> TradingDecision:
        """
        Special Logic for Scalping (M1/M5)
        Simplified Rules:
        - Trend Direction (EMA9 vs EMA21)
        - Volume Confirmation
        """
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 1. Spread Check (If avail)
        spread_pct = latest.get('ob_spread_pct', 0)
        if spread_pct > config.trading.max_spread_pct:
            return TradingDecision(
                market_state=regime, direction_bias="NONE", setup_quality=0, confidence=0,
                recommendation="NO_TRADE", reasoning=f"Spread too high: {spread_pct}%"
            )

        # 2. Trend Direction (Simple EMA Check)
        direction = "NONE"
        quality = 0.0
        reason = []
        
        ema_9 = latest.get('ema_9', latest['close'])
        ema_21 = latest.get('ema_21', latest['close'])
        
        # Volume Check
        vol_ok = (latest.get('vol_increasing_3', 0) == 1) or (latest.get('volume_ratio', 0) > 1.2)
        
        # Trend following: EMA determines direction
        if ema_9 > ema_21:
            direction = "LONG"
            quality = 0.6
            reason.append("Uptrend (EMA9 > EMA21)")
            
            # Bonus for rejection wick
            if latest.get('rejection_bottom', 0):
                quality = 0.8
                reason.append("+ Bullish Rejection")
                
        elif ema_9 < ema_21:
            direction = "SHORT"
            quality = 0.6
            reason.append("Downtrend (EMA9 < EMA21)")
            
            # Bonus for rejection wick
            if latest.get('rejection_top', 0):
                quality = 0.8
                reason.append("+ Bearish Rejection")
        
        # Volume boost
        if vol_ok and quality > 0:
            quality = min(quality + 0.1, 1.0)
            reason.append("+ Volume OK")

        # 3. Construct Decision
        rec = "NO_TRADE"
        if direction != "NONE" and quality >= 0.5:
            rec = "ALLOW_TRADE"
        
        return TradingDecision(
            market_state="SCALP",
            direction_bias=direction,
            setup_quality=quality,
            confidence=0.9 if vol_ok else 0.6,
            recommendation=rec,
            reasoning=" | ".join(reason) if reason else "No trend"
        )

    def check_reversal(self, df: pd.DataFrame, current_side: str) -> Tuple[bool, str]:
        """
        Active Monitoring v1.1: Multi-Factor Reversal Check
        Requires 2 out of 3 risk factors:
        1. Strong Rejection Wick (>60% of candle)
        2. Momentum Failure (Stalled highs/lows)
        3. High Volume (>1.3x avg)
        """
        if len(df) < 6:
            return False, ""
            
        latest = df.iloc[-1]
        
        # Factor 1: Rejection Wick
        has_rejection = self._detect_rejection(latest, current_side)
        
        # Factor 2: Momentum Failure
        has_momentum_fail = self._detect_momentum_failure(df, current_side)
        
        # Factor 3: Volume Spike
        # Avg volume of last 5 candles (excluding current)
        avg_vol = df.iloc[-6:-1]['volume'].mean()
        has_volume = latest['volume'] > (avg_vol * 1.3)
        
        # Scoring
        score = 0
        reasons = []
        
        if has_rejection:
            score += 1
            reasons.append("Rejection Wick")
        if has_momentum_fail:
            score += 1
            reasons.append("Momentum Stalled")
        if has_volume:
            score += 1
            reasons.append("High Volume")
        
        # DEBUG LOG (Simulated for dry run visibility if needed, or rely on caller)
        # print(f"DEBUG REVERSAL: Wick={has_rejection} Mom={has_momentum_fail} Vol={has_volume} Score={score}")
            
        # Decision Rule: Need at least 2 factors to pull the trigger
        if score >= 2:
            return True, f"Reversal Signal (Score {score}/3): {' + '.join(reasons)}"
            
        return False, ""

    def _detect_rejection(self, row: pd.Series, side: str) -> bool:
        """Check for long wick opposing the position (>60% of range)"""
        if row['high'] == row['low']:
            return False
            
        body_size = abs(row['close'] - row['open'])
        total_range = row['high'] - row['low']
        
        if side == 'LONG':
            # Fear: Bull trap (Long UPPER wick)
            upper_wick = row['high'] - max(row['open'], row['close'])
            ratio = upper_wick / total_range
            return ratio > 0.6
            
        elif side == 'SHORT':
            # Fear: Bear trap (Long LOWER wick)
            lower_wick = min(row['open'], row['close']) - row['low']
            ratio = lower_wick / total_range
            return ratio > 0.6
            
        return False

    def _detect_momentum_failure(self, df: pd.DataFrame, side: str) -> bool:
        """Check if price momentum is stalling"""
        # Analyze last 3 closed candles
        if len(df) < 5: return False
        
        recent = df.iloc[-4:-1] # Last 3 fully closed candles
        current = df.iloc[-1]    # Current live candle
        
        if side == 'LONG':
            # 1. Failure to make Higher Highs (Stalling Top)
            # Check if highs are not increasing or barely increasing
            highs = recent['high'].values
            is_stalled = highs[-1] <= highs[-2]
            
            # 2. Bearish Engulfing / Strong Red Candle appearing now?
            is_dumping = current['close'] < current['open'] and current['close'] < recent.iloc[-1]['low']
            
            return is_stalled or is_dumping

        elif side == 'SHORT':
            # 1. Failure to make Lower Lows (Stalling Bottom)
            lows = recent['low'].values
            is_stalled = lows[-1] >= lows[-2]
            
            # 2. Bullish Engulfing / Strong Green Candle appearing now?
            is_pumping = current['close'] > current['open'] and current['close'] > recent.iloc[-1]['high']
            
            return is_stalled or is_pumping
                
        return False
    
    def _calculate_setup_quality(self, df: pd.DataFrame) -> float:
        """
        Calculate setup quality score (0.0 to 1.0)
        
        High quality indicators:
        - Clear trend structure
        - Low noise / clean price action
        - Volume confirmation
        - Momentum alignment
        """
        scores = []
        latest = df.iloc[-1]
        
        # Trend clarity (0-1)
        trend_score = abs(latest.get('trend_score', 0))
        trend_clarity = min(trend_score * 2, 1.0)  # Scale up
        scores.append(trend_clarity)
        
        # Price structure (body/wick ratio)
        body_ratio = latest.get('body_range_ratio', 0.5)
        scores.append(body_ratio)
        
        # Volume confirmation
        volume_ratio = latest.get('volume_ratio', 1.0)
        volume_score = min(volume_ratio / 2, 1.0) if volume_ratio > 1 else volume_ratio
        scores.append(volume_score)
        
        # Momentum alignment (RSI in trend zone)
        rsi = latest.get('rsi', 50)
        if rsi > 55 and latest.get('trend_score', 0) > 0:  # Bullish alignment
            momentum_score = 0.8
        elif rsi < 45 and latest.get('trend_score', 0) < 0:  # Bearish alignment
            momentum_score = 0.8
        elif 40 < rsi < 60:  # Neutral
            momentum_score = 0.5
        else:
            momentum_score = 0.3  # Divergence
        scores.append(momentum_score)
        
        # EMA alignment
        ema_slope = latest.get('ema_9_slope', 0)
        trend = latest.get('trend_score', 0)
        if (ema_slope > 0 and trend > 0) or (ema_slope < 0 and trend < 0):
            ema_score = 0.8
        else:
            ema_score = 0.4
        scores.append(ema_score)
        
        # Average all scores
        quality = np.mean(scores)
        
        # [SNIPER] Add Pattern Score
        patterns = self._detect_patterns(df)
        quality += patterns['score']
        
        # [HARMONIC] Add Harmonic Pattern Bonus
        harmonic_boost = 0.0 # Initialize harmonic_boost
        try:
            harmonics = self.harmonic_detector.detect(df)
            if harmonics:
                best_pattern = max(harmonics, key=lambda p: p.confidence)
                logger.info(f"🎯 HARMONIC: {best_pattern.name} ({'Bull' if best_pattern.bullish else 'Bear'}) {best_pattern.confidence:.2f}")
                
                # CRITICAL UPDATE: Prioritize Pattern over Trend
                # If pattern confidence is high, switch bias to match pattern
                if best_pattern.confidence >= 0.7:
                    # 'direction' and 'regime' are not in scope here. Assuming this logic is meant for the `analyze` method.
                    # For _calculate_setup_quality, we only adjust the quality score.
                    harmonic_boost = 0.3  # Huge boost
                    
                    # Ensure quality meets minimum threshold if logic was sound
                    # We start from base 0.5 for a strong pattern
                    quality = max(quality, 0.5) 
                else:
                    harmonic_boost = best_pattern.confidence * 0.15
        except Exception as e:
            pass  # Silently continue if harmonic detection fails
        
        # 4. Final Quality Score (moved outside try-except for clarity and to apply boost)
        quality += harmonic_boost
        quality = min(quality, 1.0)
        
        # Penalty for extreme volatility
        vol_score = latest.get('volatility_score', 1.0)
        if vol_score > 2.0:
            quality *= 0.5
        elif vol_score > 1.5:
            quality *= 0.7
        
        return float(np.clip(quality, 0, 1))
    
    # Old method replaced by new signature above
    # def calculate_dynamic_leverage(self, volatility_score: float, quality: float) -> int:
    #    ...

    def _detect_patterns(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Detect price patterns for Sniper Mode
        - Engulfing Candles
        - Rejection Wicks (Pinbars)
        """
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        patterns = {
            'bullish_engulfing': False,
            'bearish_engulfing': False,
            'bullish_pinbar': False,
            'bearish_pinbar': False,
            'score': 0.0
        }
        
        # 1. Engulfing
        # Bullish: Prev was RED, Current GREEN, Current Body > Prev Body, Current Open < Prev Open, Current Close > Prev Close
        body_len = abs(latest['close'] - latest['open'])
        prev_body_len = abs(prev['close'] - prev['open'])
        
        if (prev['close'] < prev['open']) and (latest['close'] > latest['open']):
            if body_len > prev_body_len and latest['close'] > prev['open'] and latest['open'] < prev['close']:
                 patterns['bullish_engulfing'] = True
                 patterns['score'] += 0.4

        if (prev['close'] > prev['open']) and (latest['close'] < latest['open']):
            if body_len > prev_body_len and latest['close'] < prev['open'] and latest['open'] > prev['close']:
                 patterns['bearish_engulfing'] = True
                 patterns['score'] += 0.4
                 
        # 2. Rejection Wicks (Pinbars) - Uses already computed features
        # 'rejection_bottom' means lower wick is > 40% of range
        if latest.get('rejection_bottom', 0):
             patterns['bullish_pinbar'] = True
             patterns['score'] += 0.3
             
        if latest.get('rejection_top', 0):
             patterns['bearish_pinbar'] = True
             patterns['score'] += 0.3
             
        return patterns

    def _check_open_interest(self, price_change: float, oi_change: float) -> str:
        """
         Analyze Open Interest (OI) vs Price Action
         
         Rules:
         - Price UP + OI UP = Strong Bullish (New money coming in)
         - Price DOWN + OI UP = Strong Bearish (New shorts coming in)
         - Price UP + OI DOWN = Weak Bullish (Short covering, not buying)
         - Price DOWN + OI DOWN = Weak Bearish (Longs giving up)
        """
        if abs(oi_change) < 0.5:
            return "NEUTRAL"
            
        if price_change > 0:
            if oi_change > 0:
                return "BULLISH_STRONG"
            else:
                return "BULLISH_WEAK"
        else:
            if oi_change > 0:
                return "BEARISH_STRONG"
            else:
                return "BEARISH_WEAK"
    
    def _determine_direction(
        self,
        df: pd.DataFrame,
        regime: str
    ) -> str:
        """
        Determine directional bias
        
        Rules (loosened for more signals):
        - TREND_BULL / MOMENTUM (up) → LONG
        - TREND_BEAR / MOMENTUM (down) → SHORT
        - BREAKOUT → follow direction
        - RANGE / VOLATILE → use trend_score if strong enough
        """
        latest = df.iloc[-1]
        
        # Get indicators
        trend_score = latest.get('trend_score', 0)
        ema_slope = latest.get('ema_9_slope', 0)
        return_5 = latest.get('return_5', 0)
        
        # [STRICT FILTER] 1. EMA Slope Gate (Anti-Knife)
        # If EMA9 is pointing DOWN (< -0.05), FORBID Longs.
        # If EMA9 is pointing UP (> 0.05), FORBID Shorts.
        gate_long = ema_slope > -0.05
        gate_short = ema_slope < 0.05
        
        # [STRICT FILTER] 2. Candle Color Gate
        # Longs: GREEN candle *OR* Strong Lower Wick (Hammer/Pinbar)
        # Shorts: RED candle *OR* Strong Upper Wick (Shooting Star)
        # Note: rejection_bottom/top are booleans computed earlier or in price_features
        has_lower_wick = latest.get('rejection_bottom', False)
        has_upper_wick = latest.get('rejection_top', False)
        
        is_green = (latest['close'] > latest['open']) or has_lower_wick
        is_red = (latest['close'] < latest['open']) or has_upper_wick
        
        # Clear trend regimes
        if regime == MarketState.TREND_BULL.value:
            if gate_long and is_green: return DirectionBias.LONG.value
        elif regime == MarketState.TREND_BEAR.value:
            if gate_short and is_red: return DirectionBias.SHORT.value
        
        # Breakout direction
        elif regime == MarketState.BREAKOUT.value:
            if return_5 > 0 and gate_long and is_green:
                return DirectionBias.LONG.value
            elif return_5 < 0 and gate_short and is_red:
                return DirectionBias.SHORT.value
        
        # Momentum direction
        elif regime == MarketState.MOMENTUM.value:
            if trend_score > 0 and gate_long and is_green:
                return DirectionBias.LONG.value
            elif trend_score < 0 and gate_short and is_red:
                return DirectionBias.SHORT.value
        
        # RANGE or VOLATILE: Use trend_score to determine direction
        if abs(trend_score) > 0.05:
            if trend_score > 0 and gate_long and is_green:
                return DirectionBias.LONG.value
            elif trend_score < 0 and gate_short and is_red:
                return DirectionBias.SHORT.value
        
        # Fallback: Use EMA slope if trend is flat
        if abs(ema_slope) > 0.01:
            if ema_slope > 0 and gate_long and is_green:
                return DirectionBias.LONG.value
            elif ema_slope < 0 and gate_short and is_red:
                return DirectionBias.SHORT.value
        
        # Last resort: Use recent return
        if abs(return_5) > 0.3:
            if return_5 > 0 and gate_long and is_green:
                return DirectionBias.LONG.value
            elif return_5 < 0 and gate_short and is_red:
                return DirectionBias.SHORT.value
        
        # Bear Market Hail Mary kept, but gated
        latest_close = latest['close']
        ema_21 = latest.get('ema_21', latest_close)
        if latest_close < ema_21 * 0.999: 
             if gate_short and is_red: return DirectionBias.SHORT.value
        
        return DirectionBias.NONE.value
    
    def _calculate_confidence(
        self,
        df: pd.DataFrame,
        regime: str,
        setup_quality: float
    ) -> float:
        """
        Calculate confidence score (0.0 to 1.0)
        
        Confidence reflects certainty in the analysis.
        Reduced by:
        - High volatility
        - Unclear structure
        - Conflicting signals
        """
        latest = df.iloc[-1]
        
        # Base confidence from setup quality
        confidence = setup_quality
        
        # Regime confidence multipliers
        regime_confidence = {
            MarketState.TREND_BULL.value: 0.9,
            MarketState.TREND_BEAR.value: 0.9,
            MarketState.MOMENTUM.value: 0.85,
            MarketState.BREAKOUT.value: 0.8,
            MarketState.RANGE.value: 0.6,
            MarketState.VOLATILE.value: 0.4
        }
        confidence *= regime_confidence.get(regime, 0.5)
        
        # Volatility adjustment
        vol_label = latest.get('volatility_label', VolatilityLabel.NORMAL.value)
        if vol_label == VolatilityLabel.EXTREME.value:
            confidence *= 0.4
        elif vol_label == VolatilityLabel.HIGH.value:
            confidence *= 0.7
        elif vol_label == VolatilityLabel.LOW.value:
            confidence *= 1.1  # Slight boost for calm markets
        
        # Structure clarity boost
        trend_score = abs(latest.get('trend_score', 0))
        if trend_score > 0.5:
            confidence *= 1.1
        
        return float(np.clip(confidence, 0, 1))
    
    def _generate_recommendation(
        self,
        regime: str,
        direction: str,
        setup_quality: float,
        confidence: float,
        df: pd.DataFrame
    ) -> Tuple[str, str]:
        """
        Generate recommendation and reasoning
        
        Decision table:
        | setup_quality | confidence | Recommendation |
        |---------------|------------|----------------|
        | < 0.4         | any        | NO_TRADE       |
        | 0.4-0.5       | >= 0.6     | MICRO_TRADE    |
        | 0.4-0.5       | < 0.6      | NO_TRADE       |
        | >= 0.6        | >= 0.5     | ALLOW_TRADE    |
        | >= 0.6        | < 0.5      | MICRO_TRADE    |
        """
        latest = df.iloc[-1]
        reasons = []
        
        # No direction = no trade
        if direction == DirectionBias.NONE.value:
            recommendation = Recommendation.NO_TRADE.value
            reasons.append("No clear directional bias")
        
        # Low quality = no trade (using config threshold: 0.35)
        elif setup_quality < self.min_quality:
            recommendation = Recommendation.NO_TRADE.value
            reasons.append(f"Setup quality too low ({setup_quality:.2f})")
        
        # Medium quality (0.35 - 0.5): Allow with decent confidence
        elif setup_quality < 0.5:
            if confidence >= 0.4:  # Lowered from 0.6
                recommendation = Recommendation.MICRO_TRADE.value
                reasons.append(f"Decent setup ({setup_quality:.2f})")
            else:
                recommendation = Recommendation.NO_TRADE.value
                reasons.append(f"Marginal setup with low confidence ({confidence:.2f})")
        
        # Good quality (>= 0.5): Full trade
        else:
            if confidence >= 0.4:  # Lowered from 0.5
                recommendation = Recommendation.ALLOW_TRADE.value
                reasons.append(f"Good setup ({setup_quality:.2f}) with confidence ({confidence:.2f})")
            else:
                recommendation = Recommendation.MICRO_TRADE.value
                reasons.append(f"Good setup but reduced confidence ({confidence:.2f})")
        
        # Add regime context
        reasons.append(f"Regime: {regime}")
        
        # Add bias context
        if direction != DirectionBias.NONE.value:
            trend_score = latest.get('trend_score', 0)
            reasons.append(f"Trend score: {trend_score:.2f}, bias: {direction}")
        
        # Add volatility context
        vol_label = latest.get('volatility_label', 'NORMAL')
        if vol_label in ['HIGH', 'EXTREME']:
            reasons.append(f"Volatility: {vol_label} - confidence adjusted")
        
        reasoning = ". ".join(reasons)
        
        return recommendation, reasoning
    
    def get_decision_json(self, df: pd.DataFrame) -> str:
        """Get decision as JSON string"""
        decision = self.analyze(df)
        return decision.to_json()


def analyze_market(df: pd.DataFrame) -> TradingDecision:
    """Convenience function to analyze market and get decision"""
    engine = DecisionEngine()
    return engine.analyze(df)


def get_decision_json(df: pd.DataFrame) -> str:
    """Convenience function to get decision as JSON"""
    engine = DecisionEngine()
    return engine.get_decision_json(df)


# CLI for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Decision Engine test")
    parser.add_argument("--test", action="store_true", help="Run test")
    
    args = parser.parse_args()
    
    if args.test:
        np.random.seed(42)
        
        # Simulate different market conditions
        scenarios = [
            {"name": "Bullish Trend", "trend": 0.5, "vol": 0.8, "rsi": 60},
            {"name": "Bearish Trend", "trend": -0.5, "vol": 0.9, "rsi": 35},
            {"name": "Range", "trend": 0.05, "vol": 1.0, "rsi": 50},
            {"name": "High Volatility", "trend": 0.2, "vol": 1.8, "rsi": 55},
            {"name": "Breakout", "trend": 0.6, "vol": 1.2, "rsi": 65},
        ]
        
        engine = DecisionEngine()
        
        for scenario in scenarios:
            print(f"\n{'='*50}")
            print(f"Scenario: {scenario['name']}")
            print('='*50)
            
            # Create test data
            df = pd.DataFrame({
                'close': [40000],
                'return_1': [0.3 if scenario['trend'] > 0 else -0.3],
                'return_5': [1.0 if scenario['trend'] > 0 else -1.0],
                'return_15': [1.5 if scenario['trend'] > 0 else -1.5],
                'trend_score': [scenario['trend']],
                'dist_ema_9': [0.2 if scenario['trend'] > 0 else -0.2],
                'dist_ema_21': [0.3 if scenario['trend'] > 0 else -0.3],
                'ema_9_slope': [0.08 if scenario['trend'] > 0 else -0.08],
                'ema_21_slope': [0.05 if scenario['trend'] > 0 else -0.05],
                'atr_ratio': [scenario['vol']],
                'volatility_score': [scenario['vol']],
                'volatility_label': ['NORMAL' if scenario['vol'] < 1.4 else 'HIGH'],
                'rsi': [scenario['rsi']],
                'macd_diff': [10 if scenario['trend'] > 0 else -10],
                'body_range_ratio': [0.7],
                'range_position': [0.6 if scenario['trend'] > 0 else 0.4],
                'volume_ratio': [1.3]
            })
            
            decision = engine.analyze(df)
            print(decision.to_json())
