"""
Technical Pattern Detection Module

Detects trend, continuation, reversal, and breakout patterns using mathematical algorithms.
Similar to harmonic_patterns.py, this module provides comprehensive technical analysis pattern detection.
"""

from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from enum import Enum
import pandas as pd
import numpy as np
from scipy.stats import linregress
import logging

logger = logging.getLogger(__name__)


class PatternCategory(Enum):
    """Pattern category types"""
    TREND = "trend"
    CONTINUATION = "continuation"
    REVERSAL = "reversal"
    BREAKOUT = "breakout"


@dataclass
class TechnicalPattern:
    """
    Technical Pattern Detection Result
    Similar to HarmonicPattern but for classical TA patterns
    """
    name: str
    category: PatternCategory
    bullish: bool
    start_idx: int
    end_idx: int
    confidence: float
    entries: List[float]
    stops: List[float]
    targets: List[float]
    metadata: Dict = None  # Pattern-specific info (channel slope, swing ratios, etc.)


class TrendPatternDetector:
    """Detects trend and structure patterns: HH-HL, LH-LL, S/R Flips, Channels"""
    
    def __init__(self, swing_lookback: int = 30, sr_tolerance_pct: float = 0.02):
        self.swing_lookback = swing_lookback
        self.sr_tolerance_pct = sr_tolerance_pct
    
    def detect_trend_structure(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect HH-HL (bullish) and LH-LL (bearish) structures"""
        patterns = []
        if len(df) < 20:
            return patterns
        
        swings = self._get_swing_points(df)
        if len(swings) < 4:
            return patterns
        
        # Analyze last 4 swings for trend structure
        recent_swings = swings[-4:]
        
        # HH-HL Pattern (Bullish)
        if self._is_higher_highs_higher_lows(recent_swings):
            latest = df.iloc[-1]
            pattern = TechnicalPattern(
                name="Higher High - Higher Low",
                category=PatternCategory.TREND,
                bullish=True,
                start_idx=recent_swings[0]['index'],
                end_idx=recent_swings[-1]['index'],
                confidence=0.70,
                entries=[latest['close'] * 0.995],  # Slight pullback entry
                stops=[recent_swings[-2]['price'] * 0.98],  # Below last higher low
                targets=[latest['close'] * 1.03, latest['close'] * 1.05],
                metadata={'swing_count': len(recent_swings), 'structure': 'uptrend'}
            )
            patterns.append(pattern)
        
        # LH-LL Pattern (Bearish)
        elif self._is_lower_highs_lower_lows(recent_swings):
            latest = df.iloc[-1]
            pattern = TechnicalPattern(
                name="Lower High - Lower Low",
                category=PatternCategory.TREND,
                bullish=False,
                start_idx=recent_swings[0]['index'],
                end_idx=recent_swings[-1]['index'],
                confidence=0.70,
                entries=[latest['close'] * 1.005],  # Slight bounce entry
                stops=[recent_swings[-2]['price'] * 1.02],  # Above last lower high
                targets=[latest['close'] * 0.97, latest['close'] * 0.95],
                metadata={'swing_count': len(recent_swings), 'structure': 'downtrend'}
            )
            patterns.append(pattern)
        
        return patterns
    
    def detect_sr_flips(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect Support-to-Resistance and Resistance-to-Support flips"""
        patterns = []
        if len(df) < 50:
            return patterns
        
        # Find significant support/resistance levels
        sr_levels = self._find_sr_levels(df)
        
        for level in sr_levels:
            flip_pattern = self._check_level_flip(df, level)
            if flip_pattern:
                patterns.append(flip_pattern)
        
        return patterns
    
    def detect_trend_channel(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect bullish/bearish trend channels using linear regression"""
        patterns = []
        if len(df) < 30:
            return patterns
        
        swings_high = self._get_swing_highs(df)
        swings_low = self._get_swing_lows(df)
        
        if len(swings_high) < 3 or len(swings_low) < 3:
            return patterns
        
        # Bullish Channel
        bullish_channel = self._fit_channel(swings_low, swings_high, bullish=True)
        if bullish_channel and bullish_channel['r2'] > 0.85:
            latest = df.iloc[-1]
            pattern = TechnicalPattern(
                name="Bullish Trend Channel",
                category=PatternCategory.TREND,
                bullish=True,
                start_idx=swings_low[0]['index'],
                end_idx=len(df) - 1,
                confidence=min(0.60 + bullish_channel['r2'] * 0.15, 0.80),
                entries=[bullish_channel['lower_line']],
                stops=[bullish_channel['lower_line'] * 0.98],
                targets=[bullish_channel['upper_line']],
                metadata={'slope': bullish_channel['slope'], 'r2': bullish_channel['r2']}
            )
            patterns.append(pattern)
        
        # Bearish Channel
        bearish_channel = self._fit_channel(swings_high, swings_low, bullish=False)
        if bearish_channel and bearish_channel['r2'] > 0.85:
            latest = df.iloc[-1]
            pattern = TechnicalPattern(
                name="Bearish Trend Channel",
                category=PatternCategory.TREND,
                bullish=False,
                start_idx=swings_high[0]['index'],
                end_idx=len(df) - 1,
                confidence=min(0.60 + bearish_channel['r2'] * 0.15, 0.80),
                entries=[bearish_channel['upper_line']],
                stops=[bearish_channel['upper_line'] * 1.02],
                targets=[bearish_channel['lower_line']],
                metadata={'slope': bearish_channel['slope'], 'r2': bearish_channel['r2']}
            )
            patterns.append(pattern)
        
        return patterns
    
    # Helper Methods
    def _get_swing_points(self, df: pd.DataFrame) -> List[Dict]:
        """Get both swing highs and lows in chronological order"""
        highs = self._get_swing_highs(df)
        lows = self._get_swing_lows(df)
        
        # Merge and sort by index
        all_swings = highs + lows
        all_swings.sort(key=lambda x: x['index'])
        
        return all_swings[-self.swing_lookback:]
    
    def _get_swing_highs(self, df: pd.DataFrame) -> List[Dict]:
        """Identify swing high points using 5-bar fractal"""
        swings = []
        for i in range(2, len(df) - 2):
            if (df.iloc[i]['high'] > df.iloc[i-1]['high'] and 
                df.iloc[i]['high'] > df.iloc[i-2]['high'] and
                df.iloc[i]['high'] > df.iloc[i+1]['high'] and 
                df.iloc[i]['high'] > df.iloc[i+2]['high']):
                swings.append({
                    'index': i,
                    'price': df.iloc[i]['high'],
                    'type': 'high'
                })
        return swings
    
    def _get_swing_lows(self, df: pd.DataFrame) -> List[Dict]:
        """Identify swing low points using 5-bar fractal"""
        swings = []
        for i in range(2, len(df) - 2):
            if (df.iloc[i]['low'] < df.iloc[i-1]['low'] and 
                df.iloc[i]['low'] < df.iloc[i-2]['low'] and
                df.iloc[i]['low'] < df.iloc[i+1]['low'] and 
                df.iloc[i]['low'] < df.iloc[i+2]['low']):
                swings.append({
                    'index': i,
                    'price': df.iloc[i]['low'],
                    'type': 'low'
                })
        return swings
    
    def _is_higher_highs_higher_lows(self, swings: List[Dict]) -> bool:
        """Check if swings form HH-HL structure"""
        if len(swings) < 4:
            return False
        
        # Filter highs and lows
        highs = [s for s in swings if s['type'] == 'high']
        lows = [s for s in swings if s['type'] == 'low']
        
        if len(highs) < 2 or len(lows) < 2:
            return False
        
        # Check if highs are increasing and lows are increasing
        highs_increasing = all(highs[i]['price'] > highs[i-1]['price'] for i in range(1, len(highs)))
        lows_increasing = all(lows[i]['price'] > lows[i-1]['price'] for i in range(1, len(lows)))
        
        return highs_increasing and lows_increasing
    
    def _is_lower_highs_lower_lows(self, swings: List[Dict]) -> bool:
        """Check if swings form LH-LL structure"""
        if len(swings) < 4:
            return False
        
        highs = [s for s in swings if s['type'] == 'high']
        lows = [s for s in swings if s['type'] == 'low']
        
        if len(highs) < 2 or len(lows) < 2:
            return False
        
        highs_decreasing = all(highs[i]['price'] < highs[i-1]['price'] for i in range(1, len(highs)))
        lows_decreasing = all(lows[i]['price'] < lows[i-1]['price'] for i in range(1, len(lows)))
        
        return highs_decreasing and lows_decreasing
    
    def _find_sr_levels(self, df: pd.DataFrame) -> List[Dict]:
        """Find significant support/resistance levels using price density"""
        # Simple approach: Find price levels with multiple touches
        price_touches = {}
        tolerance = self.sr_tolerance_pct
        
        for i in range(len(df)):
            high = df.iloc[i]['high']
            low = df.iloc[i]['low']
            
            # Round prices to tolerance level for clustering
            high_key = round(high / (high * tolerance)) * (high * tolerance)
            low_key = round(low / (low * tolerance)) * (low * tolerance)
            
            price_touches[high_key] = price_touches.get(high_key, 0) + 1
            price_touches[low_key] = price_touches.get(low_key, 0) + 1
        
        # Filter levels with at least 3 touches
        significant_levels = [
            {'price': price, 'touches': count}
            for price, count in price_touches.items()
            if count >= 3
        ]
        
        return sorted(significant_levels, key=lambda x: x['touches'], reverse=True)[:5]
    
    def _check_level_flip(self, df: pd.DataFrame, level: Dict) -> Optional[TechnicalPattern]:
        """Check if a S/R level has flipped roles"""
        price = level['price']
        tolerance = price * self.sr_tolerance_pct
        
        # Look for touches above and below the level
        touches_above = 0
        touches_below = 0
        recent_above = False
        recent_below = False
        
        for i in range(max(0, len(df) - 50), len(df)):
            high = df.iloc[i]['high']
            low = df.iloc[i]['low']
            
            if abs(low - price) < tolerance:
                if i > len(df) - 10:
                    recent_below = True
                touches_below += 1
            
            if abs(high - price) < tolerance:
                if i > len(df) - 10:
                    recent_above = True
                touches_above += 1
        
        # Support to Resistance flip (was support, now resistance)
        if touches_below >= 2 and recent_above and df.iloc[-1]['close'] < price:
            return TechnicalPattern(
                name="Support to Resistance Flip",
                category=PatternCategory.TREND,
                bullish=False,
                start_idx=max(0, len(df) - 50),
                end_idx=len(df) - 1,
                confidence=0.65,
                entries=[price * 1.002],
                stops=[price * 1.015],
                targets=[price * 0.97, price * 0.95],
                metadata={'level': price, 'touches': touches_below + touches_above}
            )
        
        # Resistance to Support flip (was resistance, now support)
        if touches_above >= 2 and recent_below and df.iloc[-1]['close'] > price:
            return TechnicalPattern(
                name="Resistance to Support Flip",
                category=PatternCategory.TREND,
                bullish=True,
                start_idx=max(0, len(df) - 50),
                end_idx=len(df) - 1,
                confidence=0.65,
                entries=[price * 0.998],
                stops=[price * 0.985],
                targets=[price * 1.03, price * 1.05],
                metadata={'level': price, 'touches': touches_below + touches_above}
            )
        
        return None
    
    def _fit_channel(self, primary_swings: List[Dict], secondary_swings: List[Dict], bullish: bool) -> Optional[Dict]:
        """Fit a trend channel using linear regression"""
        if len(primary_swings) < 3:
            return None
        
        # Get recent swings (last 10)
        primary_swings = primary_swings[-10:]
        secondary_swings = secondary_swings[-10:]
        
        # Fit regression to primary swings
        x = np.array([s['index'] for s in primary_swings])
        y = np.array([s['price'] for s in primary_swings])
        
        slope, intercept, r_value, _, _ = linregress(x, y)
        r2 = r_value ** 2
        
        if r2 < 0.85:
            return None
        
        # Calculate current line values
        current_idx = primary_swings[-1]['index']
        primary_line = slope * current_idx + intercept
        
        # Estimate parallel line using secondary swings
        secondary_prices = [s['price'] for s in secondary_swings]
        if bullish:
            # Upper line (resistance)
            secondary_line = primary_line + (max(secondary_prices) - primary_line) * 1.1
        else:
            # Lower line (support)
            secondary_line = primary_line - (primary_line - min(secondary_prices)) * 1.1
        
        return {
            'slope': slope,
            'r2': r2,
            'lower_line': min(primary_line, secondary_line),
            'upper_line': max(primary_line, secondary_line)
        }


class ContinuationPatternDetector:
    """Detects continuation patterns: Flags, Triangles, Cup & Handle"""
    
    def __init__(self):
        self.min_flagpole_pct = 0.05  # 5% move for flagpole
        self.max_flag_duration = 20  # candles
    
    def detect_flags(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect bullish and bearish flag patterns"""
        patterns = []
        if len(df) < 25:
            return patterns
        
        # Look for strong directional move (flagpole) followed by consolidation
        for i in range(20, len(df) - 5):
            # Check for flagpole (strong move in 5-15 candles)
            flagpole_start = i - 15
            flagpole_end = i
            
            price_change = (df.iloc[flagpole_end]['close'] - df.iloc[flagpole_start]['close']) / df.iloc[flagpole_start]['close']
            
            # Bullish Flag
            if price_change > self.min_flagpole_pct:
                flag_pattern = self._check_flag_consolidation(df, flagpole_end, bullish=True)
                if flag_pattern and flag_pattern.end_idx == len(df) - 1:
                    patterns.append(flag_pattern)
            
            # Bearish Flag
            elif price_change < -self.min_flagpole_pct:
                flag_pattern = self._check_flag_consolidation(df, flagpole_end, bullish=False)
                if flag_pattern and flag_pattern.end_idx == len(df) - 1:
                    patterns.append(flag_pattern)
        
        return patterns
    
    def detect_triangles(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect ascending, descending, and symmetrical triangles"""
        patterns = []
        if len(df) < 30:
            return patterns
        
        # Get recent swings
        trend_detector = TrendPatternDetector()
        swings_high = trend_detector._get_swing_highs(df[-30:])
        swings_low = trend_detector._get_swing_lows(df[-30:])
        
        if len(swings_high) < 3 or len(swings_low) < 3:
            return patterns
        
        # Analyze trendlines
        high_slope = self._calculate_slope(swings_high[-3:])
        low_slope = self._calculate_slope(swings_low[-3:])
        
        latest = df.iloc[-1]
        
        # Ascending Triangle (Flat top, rising lows)
        if abs(high_slope) < 0.001 and low_slope > 0.002:
            resistance = np.mean([s['price'] for s in swings_high[-3:]])
            pattern = TechnicalPattern(
                name="Ascending Triangle",
                category=PatternCategory.CONTINUATION,
                bullish=True,
                start_idx=swings_low[-3]['index'],
                end_idx=len(df) - 1,
                confidence=0.72,
                entries=[resistance * 1.005],  # Breakout entry
                stops=[swings_low[-1]['price']],
                targets=[resistance * 1.03, resistance * 1.05],
                metadata={'resistance': resistance, 'pattern_type': 'ascending'}
            )
            patterns.append(pattern)
        
        # Descending Triangle (Flat bottom, falling highs)
        elif abs(low_slope) < 0.001 and high_slope < -0.002:
            support = np.mean([s['price'] for s in swings_low[-3:]])
            pattern = TechnicalPattern(
                name="Descending Triangle",
                category=PatternCategory.CONTINUATION,
                bullish=False,
                start_idx=swings_high[-3]['index'],
                end_idx=len(df) - 1,
                confidence=0.72,
                entries=[support * 0.995],  # Breakdown entry
                stops=[swings_high[-1]['price']],
                targets=[support * 0.97, support * 0.95],
                metadata={'support': support, 'pattern_type': 'descending'}
            )
            patterns.append(pattern)
        
        # Symmetrical Triangle (Converging lines)
        elif high_slope < -0.001 and low_slope > 0.001:
            apex_price = (swings_high[-1]['price'] + swings_low[-1]['price']) / 2
            pattern = TechnicalPattern(
                name="Symmetrical Triangle",
                category=PatternCategory.CONTINUATION,
                bullish=latest['close'] > apex_price,  # Direction depends on breakout
                start_idx=swings_high[-3]['index'],
                end_idx=len(df) - 1,
                confidence=0.65,
                entries=[apex_price],
                stops=[apex_price * 0.97 if latest['close'] > apex_price else apex_price * 1.03],
                targets=[apex_price * 1.04 if latest['close'] > apex_price else apex_price * 0.96],
                metadata={'apex': apex_price, 'pattern_type': 'symmetrical'}
            )
            patterns.append(pattern)
        
        return patterns
    
    def detect_cup_and_handle(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect cup and handle pattern (bullish continuation)"""
        patterns = []
        if len(df) < 50:
            return patterns
        
        # Look for U-shaped recovery (cup) followed by small consolidation (handle)
        # This is a complex pattern, simplified implementation
        
        window = df[-50:]
        high_idx = window['high'].idxmax()
        low_idx = window['low'].idxmin()
        
        # Cup should form before handle
        if low_idx > high_idx:
            return patterns
        
        # Check for rounded bottom (not V-shaped)
        # Simplified: Check if recovery is gradual
        cup_depth = (window.loc[high_idx, 'high'] - window.loc[low_idx, 'low']) / window.loc[high_idx, 'high']
        
        if 0.12 < cup_depth < 0.33:  # 12-33% depth
            # Look for handle (small retracement after recovery)
            recent = df[-15:]
            handle_low = recent['low'].min()
            handle_high = recent['high'].max()
            handle_range = (handle_high - handle_low) / handle_high
            
            if handle_range < 0.15:  # Handle consolidation
                pattern = TechnicalPattern(
                    name="Cup and Handle",
                    category=PatternCategory.CONTINUATION,
                    bullish=True,
                    start_idx=len(df) - 50,
                    end_idx=len(df) - 1,
                    confidence=0.75,
                    entries=[handle_high * 1.002],
                    stops=[handle_low],
                    targets=[handle_high * 1.05, handle_high * 1.08],
                    metadata={'cup_depth': cup_depth, 'handle_range': handle_range}
                )
                patterns.append(pattern)
        
        return patterns
    
    # Helper Methods
    def _check_flag_consolidation(self, df: pd.DataFrame, flagpole_end: int, bullish: bool) -> Optional[TechnicalPattern]:
        """Check for flag consolidation after flagpole"""
        if flagpole_end + 10 > len(df):
            return None
        
        consolidation = df.iloc[flagpole_end:min(flagpole_end + self.max_flag_duration, len(df))]
        
        if len(consolidation) < 5:
            return None
        
        # Check for counter-trend consolidation
        cons_start = consolidation.iloc[0]['close']
        cons_end = consolidation.iloc[-1]['close']
        cons_change = (cons_end - cons_start) / cons_start
        
        # Consolidation should be counter to flagpole
        if bullish and -0.05 < cons_change < 0.02:  # Slight down/sideways
            # Check volume decrease
            avg_vol_flag = consolidation['volume'].mean()
            avg_vol_pole = df.iloc[flagpole_end-10:flagpole_end]['volume'].mean()
            
            if avg_vol_flag < avg_vol_pole * 0.7:  # 30% volume reduction
                return TechnicalPattern(
                    name="Bullish Flag",
                    category=PatternCategory.CONTINUATION,
                    bullish=True,
                    start_idx=flagpole_end - 15,
                    end_idx=flagpole_end + len(consolidation) - 1,
                    confidence=0.70,
                    entries=[consolidation.iloc[-1]['high'] * 1.002],
                    stops=[consolidation.iloc[:]['low'].min()],
                    targets=[cons_end * 1.05, cons_end * 1.08],
                    metadata={'flagpole_end': flagpole_end, 'consolidation_candles': len(consolidation)}
                )
        
        elif not bullish and -0.02 < cons_change < 0.05:  # Slight up/sideways
            avg_vol_flag = consolidation['volume'].mean()
            avg_vol_pole = df.iloc[flagpole_end-10:flagpole_end]['volume'].mean()
            
            if avg_vol_flag < avg_vol_pole * 0.7:
                return TechnicalPattern(
                    name="Bearish Flag",
                    category=PatternCategory.CONTINUATION,
                    bullish=False,
                    start_idx=flagpole_end - 15,
                    end_idx=flagpole_end + len(consolidation) - 1,
                    confidence=0.70,
                    entries=[consolidation.iloc[-1]['low'] * 0.998],
                    stops=[consolidation.iloc[:]['high'].max()],
                    targets=[cons_end * 0.95, cons_end * 0.92],
                    metadata={'flagpole_end': flagpole_end, 'consolidation_candles': len(consolidation)}
                )
        
        return None
    
    def _calculate_slope(self, swings: List[Dict]) -> float:
        """Calculate slope of swing points"""
        if len(swings) < 2:
            return 0.0
        
        x = np.array([s['index'] for s in swings])
        y = np.array([s['price'] for s in swings])
        
        slope, _, _, _, _ = linregress(x, y)
        return slope


class ReversalPatternDetector:
    """Detects reversal patterns: Double Top/Bottom, H&S, Wedges"""
    
    def __init__(self):
        self.peak_tolerance = 0.02  # 2% tolerance for double peaks
    
    def detect_double_patterns(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect double top and double bottom patterns"""
        patterns = []
        if len(df) < 30:
            return patterns
        
        trend_detector = TrendPatternDetector()
        swings_high = trend_detector._get_swing_highs(df)
        swings_low = trend_detector._get_swing_lows(df)
        
        # Double Top
        if len(swings_high) >= 2:
            last_two_highs = swings_high[-2:]
            peak1 = last_two_highs[0]['price']
            peak2 = last_two_highs[1]['price']
            
            if abs(peak2 - peak1) / peak1 < self.peak_tolerance:
                # Find valley between peaks
                valley_idx_start = last_two_highs[0]['index']
                valley_idx_end = last_two_highs[1]['index']
                valley = df.iloc[valley_idx_start:valley_idx_end]['low'].min()
                
                # Neckline is the valley
                if (peak1 - valley) / peak1 > 0.05:  # At least 5% depth
                    pattern = TechnicalPattern(
                        name="Double Top",
                        category=PatternCategory.REVERSAL,
                        bullish=False,
                        start_idx=last_two_highs[0]['index'],
                        end_idx=last_two_highs[1]['index'],
                        confidence=0.78,
                        entries=[valley * 0.998],  # Break below neckline
                        stops=[peak2 * 1.01],
                        targets=[valley - (peak1 - valley) * 0.5, valley - (peak1 - valley)],
                        metadata={'neckline': valley, 'peaks': [peak1, peak2]}
                    )
                    patterns.append(pattern)
        
        # Double Bottom
        if len(swings_low) >= 2:
            last_two_lows = swings_low[-2:]
            trough1 = last_two_lows[0]['price']
            trough2 = last_two_lows[1]['price']
            
            if abs(trough2 - trough1) / trough1 < self.peak_tolerance:
                # Find peak between troughs
                peak_idx_start = last_two_lows[0]['index']
                peak_idx_end = last_two_lows[1]['index']
                peak = df.iloc[peak_idx_start:peak_idx_end]['high'].max()
                
                if (peak - trough1) / trough1 > 0.05:
                    pattern = TechnicalPattern(
                        name="Double Bottom",
                        category=PatternCategory.REVERSAL,
                        bullish=True,
                        start_idx=last_two_lows[0]['index'],
                        end_idx=last_two_lows[1]['index'],
                        confidence=0.78,
                        entries=[peak * 1.002],  # Break above neckline
                        stops=[trough2 * 0.99],
                        targets=[peak + (peak - trough1) * 0.5, peak + (peak - trough1)],
                        metadata={'neckline': peak, 'troughs': [trough1, trough2]}
                    )
                    patterns.append(pattern)
        
        return patterns
    
    def detect_head_and_shoulders(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect head and shoulders (and inverted) patterns"""
        patterns = []
        if len(df) < 40:
            return patterns
        
        trend_detector = TrendPatternDetector()
        swings_high = trend_detector._get_swing_highs(df)
        swings_low = trend_detector._get_swing_lows(df)
        
        # Head and Shoulders (Bearish Reversal)
        if len(swings_high) >= 3:
            last_three = swings_high[-3:]
            left_shoulder = last_three[0]['price']
            head = last_three[1]['price']
            right_shoulder = last_three[2]['price']
            
            # Head should be highest, shoulders roughly equal
            if (head > left_shoulder and head > right_shoulder and
                0.9 < right_shoulder / left_shoulder < 1.1):
                
                # Head should be 3-8% higher than shoulders
                if 0.03 < (head - left_shoulder) / left_shoulder < 0.08:
                    # Find neckline (lows between shoulders)
                    neckline_start = last_three[0]['index']
                    neckline_end = last_three[2]['index']
                    neckline = df.iloc[neckline_start:neckline_end]['low'].min()
                    
                    pattern = TechnicalPattern(
                        name="Head and Shoulders",
                        category=PatternCategory.REVERSAL,
                        bullish=False,
                        start_idx=last_three[0]['index'],
                        end_idx=last_three[2]['index'],
                        confidence=0.82,
                        entries=[neckline * 0.998],
                        stops=[head * 1.01],
                        targets=[neckline - (head - neckline) * 0.618, neckline - (head - neckline)],
                        metadata={'neckline': neckline, 'head': head, 'shoulders': [left_shoulder, right_shoulder]}
                    )
                    patterns.append(pattern)
        
        # Inverted Head and Shoulders (Bullish Reversal)
        if len(swings_low) >= 3:
            last_three = swings_low[-3:]
            left_shoulder = last_three[0]['price']
            head = last_three[1]['price']
            right_shoulder = last_three[2]['price']
            
            if (head < left_shoulder and head < right_shoulder and
                0.9 < right_shoulder / left_shoulder < 1.1):
                
                if 0.03 < (left_shoulder - head) / left_shoulder < 0.08:
                    neckline_start = last_three[0]['index']
                    neckline_end = last_three[2]['index']
                    neckline = df.iloc[neckline_start:neckline_end]['high'].max()
                    
                    pattern = TechnicalPattern(
                        name="Inverted Head and Shoulders",
                        category=PatternCategory.REVERSAL,
                        bullish=True,
                        start_idx=last_three[0]['index'],
                        end_idx=last_three[2]['index'],
                        confidence=0.82,
                        entries=[neckline * 1.002],
                        stops=[head * 0.99],
                        targets=[neckline + (neckline - head) * 0.618, neckline + (neckline - head)],
                        metadata={'neckline': neckline, 'head': head, 'shoulders': [left_shoulder, right_shoulder]}
                    )
                    patterns.append(pattern)
        
        return patterns
    
    def detect_wedges(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect rising and falling wedge patterns"""
        patterns = []
        if len(df) < 30:
            return patterns
        
        trend_detector = TrendPatternDetector()
        swings_high = trend_detector._get_swing_highs(df[-30:])
        swings_low = trend_detector._get_swing_lows(df[-30:])
        
        if len(swings_high) < 3 or len(swings_low) < 3:
            return patterns
        
        # Calculate slopes
        high_slope = self._calculate_slope(swings_high[-3:])
        low_slope = self._calculate_slope(swings_low[-3:])
        
        latest = df.iloc[-1]
        
        # Rising Wedge (Both lines rising, converging - Bearish)
        if high_slope > 0 and low_slope > 0 and high_slope < low_slope * 0.8:
            pattern = TechnicalPattern(
                name="Rising Wedge",
                category=PatternCategory.REVERSAL,
                bullish=False,
                start_idx=swings_low[-3]['index'],
                end_idx=len(df) - 1,
                confidence=0.74,
                entries=[swings_low[-1]['price'] * 0.998],  # Break below support
                stops=[swings_high[-1]['price']],
                targets=[latest['close'] * 0.94, latest['close'] * 0.92],
                metadata={'upper_slope': high_slope, 'lower_slope': low_slope}
            )
            patterns.append(pattern)
        
        # Falling Wedge (Both lines falling, converging - Bullish)
        elif high_slope < 0 and low_slope < 0 and abs(high_slope) > abs(low_slope) * 0.8:
            pattern = TechnicalPattern(
                name="Falling Wedge",
                category=PatternCategory.REVERSAL,
                bullish=True,
                start_idx=swings_high[-3]['index'],
                end_idx=len(df) - 1,
                confidence=0.74,
                entries=[swings_high[-1]['price'] * 1.002],  # Break above resistance
                stops=[swings_low[-1]['price']],
                targets=[latest['close'] * 1.06, latest['close'] * 1.08],
                metadata={'upper_slope': high_slope, 'lower_slope': low_slope}
            )
            patterns.append(pattern)
        
        return patterns
    
    def _calculate_slope(self, swings: List[Dict]) -> float:
        """Calculate slope of swing points"""
        if len(swings) < 2:
            return 0.0
        
        x = np.array([s['index'] for s in swings])
        y = np.array([s['price'] for s in swings])
        
        slope, _, _, _, _ = linregress(x, y)
        return slope


class BreakoutPatternDetector:
    """Detects breakout patterns: Range, Channel, Volatility Compression"""
    
    def __init__(self):
        self.min_range_duration = 20  # candles
        self.min_range_height_pct = 0.02  # 2%
    
    def detect_range_breakout(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect horizontal range breakouts"""
        patterns = []
        if len(df) < self.min_range_duration + 5:
            return patterns
        
        # Look for recent consolidation range
        lookback = min(50, len(df) - 5)
        recent = df[-lookback:]
        
        # Find range
        range_high = recent['high'].max()
        range_low = recent['low'].min()
        range_height = (range_high - range_low) / range_low
        
        if range_height < self.min_range_height_pct:
            return patterns  # Too tight, likely noise
        
        # Check if price is currently breaking out
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Volume confirmation
        avg_volume = recent['volume'].mean()
        breakout_volume = latest['volume']
        
        # Bullish Breakout
        if latest['close'] > range_high and breakout_volume > avg_volume * 1.5:
            # Confirm with previous close
            if prev['close'] <= range_high:
                pattern = TechnicalPattern(
                    name="Range Breakout",
                    category=PatternCategory.BREAKOUT,
                    bullish=True,
                    start_idx=len(df) - lookback,
                    end_idx=len(df) - 1,
                    confidence=0.76,
                    entries=[range_high * 1.001],
                    stops=[range_high * 0.995],  # Tight stop above old resistance
                    targets=[range_high + (range_high - range_low) * 0.5, 
                             range_high + (range_high - range_low)],
                    metadata={'range_high': range_high, 'range_low': range_low, 'volume_ratio': breakout_volume / avg_volume}
                )
                patterns.append(pattern)
        
        # Bearish Breakdown
        elif latest['close'] < range_low and breakout_volume > avg_volume * 1.5:
            if prev['close'] >= range_low:
                pattern = TechnicalPattern(
                    name="Range Breakout",
                    category=PatternCategory.BREAKOUT,
                    bullish=False,
                    start_idx=len(df) - lookback,
                    end_idx=len(df) - 1,
                    confidence=0.76,
                    entries=[range_low * 0.999],
                    stops=[range_low * 1.005],
                    targets=[range_low - (range_high - range_low) * 0.5,
                             range_low - (range_high - range_low)],
                    metadata={'range_high': range_high, 'range_low': range_low, 'volume_ratio': breakout_volume / avg_volume}
                )
                patterns.append(pattern)
        
        return patterns
    
    def detect_channel_breakout(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect trend channel breakouts"""
        patterns = []
        if len(df) < 30:
            return patterns
        
        # Use trend detector to find channels
        trend_detector = TrendPatternDetector()
        channels = trend_detector.detect_trend_channel(df)
        
        if not channels:
            return patterns
        
        # Check if price is breaking out of channel
        latest = df.iloc[-1]
        
        for channel in channels:
            upper = channel.metadata['slope'] * (len(df) - 1) + channel.targets[0]
            lower = channel.entries[0]
            
            # Breakout above bullish channel (potential exhaustion or acceleration)
            if channel.bullish and latest['close'] > upper * 1.01:
                pattern = TechnicalPattern(
                    name="Channel Breakout",
                    category=PatternCategory.BREAKOUT,
                    bullish=True,
                    start_idx=channel.start_idx,
                    end_idx=len(df) - 1,
                    confidence=0.70,
                    entries=[upper],
                    stops=[lower],
                    targets=[upper * 1.03, upper * 1.05],
                    metadata={'channel_type': 'bullish', 'breakout_direction': 'up'}
                )
                patterns.append(pattern)
            
            # Breakdown below bearish channel
            elif not channel.bullish and latest['close'] < lower * 0.99:
                pattern = TechnicalPattern(
                    name="Channel Breakout",
                    category=PatternCategory.BREAKOUT,
                    bullish=False,
                    start_idx=channel.start_idx,
                    end_idx=len(df) - 1,
                    confidence=0.70,
                    entries=[lower],
                    stops=[upper],
                    targets=[lower * 0.97, lower * 0.95],
                    metadata={'channel_type': 'bearish', 'breakout_direction': 'down'}
                )
                patterns.append(pattern)
        
        return patterns
    
    def detect_volatility_compression(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """Detect volatility compression followed by expansion (ATR-based)"""
        patterns = []
        if len(df) < 30 or 'atr' not in df.columns:
            return patterns
        
        # Calculate ATR trend
        recent_atr = df['atr'].iloc[-20:].values
        
        if len(recent_atr) < 20:
            return patterns
        
        # Check for ATR decline
        atr_high = recent_atr.max()
        atr_current = recent_atr[-1]
        atr_decline = (atr_high - atr_current) / atr_high
        
        # Volatility compression: ATR declined by 40-60%
        if atr_decline > 0.40:
            # Check for recent expansion
            atr_prev = recent_atr[-5]
            atr_expansion = (atr_current - atr_prev) / atr_prev
            
            if atr_expansion > 0.20:  # 20% expansion in last 5 candles
                latest = df.iloc[-1]
                prev_5 = df.iloc[-5]
                
                # Determine direction from price action
                bullish = latest['close'] > prev_5['close']
                
                pattern = TechnicalPattern(
                    name="Volatility Compression Breakout",
                    category=PatternCategory.BREAKOUT,
                    bullish=bullish,
                    start_idx=len(df) - 20,
                    end_idx=len(df) - 1,
                    confidence=0.72,
                    entries=[latest['close']],
                    stops=[latest['close'] * 0.98 if bullish else latest['close'] * 1.02],
                    targets=[latest['close'] * 1.04 if bullish else latest['close'] * 0.96,
                             latest['close'] * 1.06 if bullish else latest['close'] * 0.94],
                    metadata={'atr_decline': atr_decline, 'atr_expansion': atr_expansion}
                )
                patterns.append(pattern)
        
        return patterns


class TechnicalPatternDetector:
    """
    Main Technical Pattern Detector
    Aggregates all pattern detection categories
    """
    
    def __init__(self, swing_lookback: int = 30, sr_tolerance_pct: float = 0.02):
        self.trend_detector = TrendPatternDetector(swing_lookback, sr_tolerance_pct)
        self.continuation_detector = ContinuationPatternDetector()
        self.reversal_detector = ReversalPatternDetector()
        self.breakout_detector = BreakoutPatternDetector()
    
    def detect(self, df: pd.DataFrame) -> List[TechnicalPattern]:
        """
        Detect all technical patterns in the given DataFrame
        
        Args:
            df: DataFrame with OHLCV data and indicators
        
        Returns:
            List of detected TechnicalPattern objects
        """
        all_patterns = []
        
        try:
            # Trend/Structure Patterns
            all_patterns.extend(self.trend_detector.detect_trend_structure(df))
            all_patterns.extend(self.trend_detector.detect_sr_flips(df))
            all_patterns.extend(self.trend_detector.detect_trend_channel(df))
            
            # Continuation Patterns
            all_patterns.extend(self.continuation_detector.detect_flags(df))
            all_patterns.extend(self.continuation_detector.detect_triangles(df))
            all_patterns.extend(self.continuation_detector.detect_cup_and_handle(df))
            
            # Reversal Patterns
            all_patterns.extend(self.reversal_detector.detect_double_patterns(df))
            all_patterns.extend(self.reversal_detector.detect_head_and_shoulders(df))
            all_patterns.extend(self.reversal_detector.detect_wedges(df))
            
            # Breakout Patterns
            all_patterns.extend(self.breakout_detector.detect_range_breakout(df))
            all_patterns.extend(self.breakout_detector.detect_channel_breakout(df))
            all_patterns.extend(self.breakout_detector.detect_volatility_compression(df))
            
        except Exception as e:
            logger.warning(f"Technical pattern detection error: {e}")
        
        # Sort by confidence (highest first)
        all_patterns.sort(key=lambda p: p.confidence, reverse=True)
        
        return all_patterns
    
    def get_best_pattern(self, df: pd.DataFrame, category: Optional[PatternCategory] = None) -> Optional[TechnicalPattern]:
        """
        Get the best (highest confidence) pattern
        
        Args:
            df: DataFrame with OHLCV data
            category: Optional filter by pattern category
        
        Returns:
            Best TechnicalPattern or None
        """
        patterns = self.detect(df)
        
        if category:
            patterns = [p for p in patterns if p.category == category]
        
        return patterns[0] if patterns else None
