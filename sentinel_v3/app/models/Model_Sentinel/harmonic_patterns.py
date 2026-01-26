import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class HarmonicPattern:
    name: str
    bullish: bool
    start_idx: int
    end_idx: int
    confidence: float
    entries: List[float]
    stops: List[float]
    targets: List[float]

class HarmonicDetector:
    """
    Detects Harmonic Patterns (Gartley, Butterfly) using strict Fibonacci ratios.
    Math-based approach for precision.
    """
    
    def __init__(self, error_tolerance: float = 0.10):
        self.err_tol = error_tolerance

    def detect(self, df: pd.DataFrame) -> List[HarmonicPattern]:
        """
        Scan DataFrame for patterns ending at the most recent candle.
        """
        patterns = []
        if len(df) < 50:
            return patterns
            
        # 1. Identify Pivots (Fractals) - Simple 5-bar fractal
        # A pivot is a high/low surrounded by 2 lower highs/higher lows
        # We need the last 5 pivots to form X-A-B-C-D
        
        pivots_high, pivots_low = self._find_pivots(df)
        
        # We need at least 5 points (X, A, B, C, D)
        # Check for Bullish Patterns (W-shape: X high, A low, B high, C low, D high... wait. 
        # Bullish Pattern is usually M-shape (Buying at D) or W-shape?
        # Bullish Gartley: X (Low) -> A (High) -> B (Low) -> C (High) -> D (Low) -> BUY
        # Shape is "M" like? No, X-A (Up), A-B (Down), B-C (Up), C-D (Down).
        # Looks like a lightning bolt or "M" distorted.
        
        # Let's simplify: 
        # Bullish: D is a potential BUY zone. D is a Local LOW.
        # So X(L), A(H), B(L), C(H), D(L)
        
        if len(pivots_low) >= 3 and len(pivots_high) >= 2:
            # Check Bullish (Last point is D, which matches current price area approx)
            # We look for completed patterns, so D should be the recent low pivot
            # OR determining if the *current price* is forming D.
            
            # For this 'Sniper' implementation, we look for completed pivots X, A, B, C 
            # and verify if Current Price is in the D-zone.
            pass

        # To keep it robust and simple for V3:
        # We will iterate through recent determined pivots.
        
        # Simplified Search:
        # Get last 5 alternating pivots.
        # This is complex to do perfectly in one pass without a ZigZag indicator.
        # We'll use a ZigZag helper.
        
        pivots = self._get_zigzag(df)
        if len(pivots) < 5:
            return patterns
            
        # Check last 5 points: X, A, B, C, D
        # D is the last point (index -1)
        
        # Bullish Gartley/Butterfly: X=Low, A=High, B=Low, C=High, D=Low
        # Bearish Gartley/Butterfly: X=High, A=Low, B=High, C=Low, D=High
        
        # Try last combination
        pts = pivots[-5:]
        
        # Check types
        p0, p1, p2, p3, p4 = pts # X, A, B, C, D
        
        # Determine Orientation
        is_bullish = False
        is_bearish = False
        
        if p0['type'] == 'low' and p1['type'] == 'high' and p2['type'] == 'low' and p3['type'] == 'high' and p4['type'] == 'low':
            is_bullish = True
        elif p0['type'] == 'high' and p1['type'] == 'low' and p2['type'] == 'high' and p3['type'] == 'low' and p4['type'] == 'high':
            is_bearish = True
            
        if not (is_bullish or is_bearish):
            return patterns
            
        # Extract Prices
        X = p0['price']
        A = p1['price']
        B = p2['price']
        C = p3['price']
        D = p4['price']
        
        XA = abs(A - X)
        AB = abs(B - A)
        BC = abs(C - B)
        CD = abs(D - C)
        AD = abs(D - A) # For Gartley consistency check sometimes
        XD = abs(D - X)
        
        # Ratios
        ratio_AB_XA = AB / XA if XA > 0 else 0
        ratio_BC_AB = BC / AB if AB > 0 else 0
        ratio_CD_BC = CD / BC if BC > 0 else 0
        ratio_XD_XA = XD / XA if XA > 0 else 0
        
        # --- GARTLEY ---
        # B: 0.618 retracement of XA
        # D: 0.786 retracement of XA
        if self._is_near(ratio_AB_XA, 0.618) and self._is_near(ratio_XD_XA, 0.786):
            if self._is_near(ratio_BC_AB, 0.382) or self._is_near(ratio_BC_AB, 0.886):
                patterns.append(HarmonicPattern(
                    name="Gartley",
                    bullish=is_bullish,
                    start_idx=p0['index'],
                    end_idx=p4['index'],
                    confidence=0.9,
                    entries=[D],
                    stops=[X - (A-X)*0.15] if is_bullish else [X + (X-A)*0.15],
                    targets=[C, A]
                ))

        # --- BUTTERFLY ---
        # B: 0.786 retracement of XA
        # D: 1.27 or 1.618 extension of XA
        elif self._is_near(ratio_AB_XA, 0.786) and (self._is_near(ratio_XD_XA, 1.27) or self._is_near(ratio_XD_XA, 1.618)):
            patterns.append(HarmonicPattern(
                name="Butterfly",
                bullish=is_bullish,
                start_idx=p0['index'],
                end_idx=p4['index'],
                confidence=0.85,
                entries=[D],
                stops=[D * 0.98] if is_bullish else [D * 1.02],
                targets=[C, A]
            ))
            
        # --- BAT ---
        # B: 0.382 or 0.5 retracement of XA
        # D: 0.886 retracement of XA (key distinguisher)
        elif (self._is_near(ratio_AB_XA, 0.382) or self._is_near(ratio_AB_XA, 0.5)) and self._is_near(ratio_XD_XA, 0.886):
            if self._is_near(ratio_BC_AB, 0.382) or self._is_near(ratio_BC_AB, 0.886):
                patterns.append(HarmonicPattern(
                    name="Bat",
                    bullish=is_bullish,
                    start_idx=p0['index'],
                    end_idx=p4['index'],
                    confidence=0.88,
                    entries=[D],
                    stops=[X - (A-X)*0.12] if is_bullish else [X + (X-A)*0.12],
                    targets=[C, B]
                ))
                
        # --- CRAB ---
        # B: 0.382 or 0.618 retracement of XA
        # D: 1.618 extension of XA (most extreme extension)
        elif self._is_near(ratio_XD_XA, 1.618):
            if self._is_near(ratio_AB_XA, 0.382) or self._is_near(ratio_AB_XA, 0.618):
                patterns.append(HarmonicPattern(
                    name="Crab",
                    bullish=is_bullish,
                    start_idx=p0['index'],
                    end_idx=p4['index'],
                    confidence=0.92,  # High confidence - extreme reversal
                    entries=[D],
                    stops=[D * 0.97] if is_bullish else [D * 1.03],
                    targets=[C, A]
                ))
                
        # --- SHARK ---
        # B: 1.13 to 1.618 retracement of XA (Reciprocal of 0.886 to 0.618)
        # D: 0.886 or 1.13 retracement of BC
        # Shark is unique - uses reciprocal ratios
        ratio_BC_XA = BC / XA if XA > 0 else 0
        if self._is_near(ratio_BC_XA, 1.13, tolerance=0.15) or self._is_near(ratio_BC_XA, 1.618, tolerance=0.15):
            if self._is_near(ratio_XD_XA, 0.886) or self._is_near(ratio_XD_XA, 1.13):
                patterns.append(HarmonicPattern(
                    name="Shark",
                    bullish=is_bullish,
                    start_idx=p0['index'],
                    end_idx=p4['index'],
                    confidence=0.80,
                    entries=[D],
                    stops=[D * 0.97] if is_bullish else [D * 1.03],
                    targets=[C]
                ))
                
        # --- CYPHER ---
        # B: 0.382 to 0.618 retracement of XA
        # C: 1.272 to 1.414 extension of XA (overshoots)
        # D: 0.786 retracement of XC
        ratio_XC_XA = abs(C - X) / XA if XA > 0 else 0
        ratio_DC_XC = abs(C - D) / abs(C - X) if abs(C - X) > 0 else 0
        
        if (self._is_near(ratio_AB_XA, 0.382) or self._is_near(ratio_AB_XA, 0.618)):
            if self._is_near(ratio_XC_XA, 1.272, tolerance=0.15) or self._is_near(ratio_XC_XA, 1.414, tolerance=0.15):
                if self._is_near(ratio_DC_XC, 0.786):
                    patterns.append(HarmonicPattern(
                        name="Cypher",
                        bullish=is_bullish,
                        start_idx=p0['index'],
                        end_idx=p4['index'],
                        confidence=0.87,
                        entries=[D],
                        stops=[C * 0.99] if is_bullish else [C * 1.01],
                        targets=[A, X]
                    ))
            
        return patterns

    def _is_near(self, value, target, tolerance=None):
        tol = tolerance if tolerance else self.err_tol
        return abs(value - target) <= (target * tol)

    def _find_pivots(self, df, order=2):
        """
        Find local peaks and valleys
        """
        # Using simple local extrema check
        # This is a placeholder for the ZigZag logic used in _get_zigzag
        return [], []

    def _get_zigzag(self, df: pd.DataFrame, deviation=0.01) -> List[dict]:
        """
        Calculates ZigZag pivots (Points of reversal > deviation %)
        Returns list of dicts: {'index': i, 'price': p, 'type': 'high'|'low'}
        """
        pivots = []
        
        # Simple implementation
        trend = 0 # 1 up, -1 down
        last_high = df.iloc[0]['high']
        last_low = df.iloc[0]['low']
        last_high_idx = 0
        last_low_idx = 0
        
        # Initial point
        pivots.append({'index': 0, 'price': df.iloc[0]['close'], 'type': 'start'})
        
        for i in range(1, len(df)):
            high = df.iloc[i]['high']
            low = df.iloc[i]['low']
            
            # Simple Swing logic logic
            # If we were going up, check for reversal down
            # If we were going down, check for reversal up
            
            # This is a naive zigzag for brevity. 
            # For production, we'd use a more robust swing point detector.
            pass
            
        # REPLACEMENT: Better simple fractal approach (Bill Williams Fractals)
        # 5 bars: High[i] > High[i-2, i-1, i+1, i+2]
        
        highs = []
        lows = []
        
        # Create a list of all fractals and sort by index
        window = 2
        for i in range(window, len(df) - window):
            # High Pivot
            if all(df['high'].iloc[i] > df['high'].iloc[i-j] for j in [-2,-1,1,2] if i-j >= 0 and i-j < len(df)):
                highs.append({'index': i, 'price': df['high'].iloc[i], 'type': 'high'})
                
            # Low Pivot
            if all(df['low'].iloc[i] < df['low'].iloc[i-j] for j in [-2,-1,1,2] if i-j >= 0 and i-j < len(df)):
                lows.append({'index': i, 'price': df['low'].iloc[i], 'type': 'low'})
                
        all_pivots = sorted(highs + lows, key=lambda x: x['index'])
        
        # Filter Alternating (High -> Low -> High)
        # If High -> High, take the higher High
        clean_pivots = []
        if not all_pivots: return []
        
        curr = all_pivots[0]
        for next_p in all_pivots[1:]:
            if next_p['type'] == curr['type']:
                # Update if better
                if curr['type'] == 'high':
                    if next_p['price'] > curr['price']:
                        curr = next_p
                else: 
                    if next_p['price'] < curr['price']:
                        curr = next_p
            else:
                clean_pivots.append(curr)
                curr = next_p
        clean_pivots.append(curr)
        
        return clean_pivots
