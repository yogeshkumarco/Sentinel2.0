# Volatility Labels - Classify market volatility regime

import pandas as pd
import numpy as np
from typing import Tuple
from enum import Enum
import logging


import sys
# sys.path.append('..')
from app.core.config import VolatilityLabel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VolatilityClassifier:
    """
    Classify volatility into regimes:
    - LOW: Below average, calm market
    - NORMAL: Around average volatility
    - HIGH: Above average, increased risk
    - EXTREME: Very high, dangerous conditions
    
    Uses ATR ratio and range percentile for classification.
    """
    
    def __init__(
        self,
        low_threshold: float = 0.6,
        high_threshold: float = 1.4,
        extreme_threshold: float = 2.0,
        lookback: int = 100
    ):
        """
        Args:
            low_threshold: ATR ratio below this = LOW
            high_threshold: ATR ratio above this = HIGH
            extreme_threshold: ATR ratio above this = EXTREME
            lookback: Period for baseline calculation
        """
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
        self.extreme_threshold = extreme_threshold
        self.lookback = lookback
    
    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add volatility labels to DataFrame
        
        Args:
            df: DataFrame with 'atr' or OHLC columns
            
        Returns:
            DataFrame with 'volatility_label' and 'volatility_score' columns
        """
        df = df.copy()
        
        # Calculate ATR if not present
        if 'atr' not in df.columns:
            df = self._calculate_atr(df)
        
        # Calculate ATR ratio (current ATR / rolling average ATR)
        df['atr_baseline'] = df['atr'].rolling(self.lookback).mean()
        df['atr_ratio'] = df['atr'] / df['atr_baseline']
        
        # Also consider range volatility
        df['range_pct'] = ((df['high'] - df['low']) / df['low']) * 100
        df['range_baseline'] = df['range_pct'].rolling(self.lookback).mean()
        df['range_ratio'] = df['range_pct'] / df['range_baseline']
        
        # Combined volatility score (average of ATR and range ratios)
        df['volatility_score'] = (df['atr_ratio'] + df['range_ratio']) / 2
        
        # Classify
        def label_volatility(score):
            if pd.isna(score):
                return VolatilityLabel.NORMAL.value
            if score >= self.extreme_threshold:
                return VolatilityLabel.EXTREME.value
            if score >= self.high_threshold:
                return VolatilityLabel.HIGH.value
            if score <= self.low_threshold:
                return VolatilityLabel.LOW.value
            return VolatilityLabel.NORMAL.value
        
        df['volatility_label'] = df['volatility_score'].apply(label_volatility)
        
        # Numeric encoding for ML
        label_map = {
            VolatilityLabel.LOW.value: 0,
            VolatilityLabel.NORMAL.value: 1,
            VolatilityLabel.HIGH.value: 2,
            VolatilityLabel.EXTREME.value: 3
        }
        df['volatility_encoded'] = df['volatility_label'].map(label_map)
        
        return df
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calculate ATR from OHLC"""
        df = df.copy()
        
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift())
        tr3 = abs(df['low'] - df['close'].shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(period).mean()
        
        return df
    
    def get_regime_stats(self, df: pd.DataFrame) -> dict:
        """Get statistics for each volatility regime"""
        if 'volatility_label' not in df.columns:
            df = self.classify(df)
        
        stats = {}
        for label in VolatilityLabel:
            mask = df['volatility_label'] == label.value
            regime_data = df[mask]
            
            if len(regime_data) > 0:
                stats[label.value] = {
                    'count': len(regime_data),
                    'percentage': (len(regime_data) / len(df)) * 100,
                    'avg_atr_pct': regime_data['range_pct'].mean() if 'range_pct' in df.columns else None,
                    'avg_volatility_score': regime_data['volatility_score'].mean()
                }
        
        return stats


def add_volatility_labels(
    df: pd.DataFrame,
    low_threshold: float = 0.6,
    high_threshold: float = 1.4,
    extreme_threshold: float = 2.0
) -> pd.DataFrame:
    """
    Convenience function to add volatility labels
    
    Args:
        df: DataFrame with OHLC or ATR
        low_threshold: Below this = LOW volatility
        high_threshold: Above this = HIGH volatility
        extreme_threshold: Above this = EXTREME volatility
        
    Returns:
        DataFrame with volatility labels
    """
    classifier = VolatilityClassifier(
        low_threshold=low_threshold,
        high_threshold=high_threshold,
        extreme_threshold=extreme_threshold
    )
    return classifier.classify(df)


def get_current_volatility(df: pd.DataFrame) -> Tuple[str, float]:
    """
    Get current volatility label and score
    
    Returns:
        Tuple of (label, score)
    """
    if 'volatility_label' not in df.columns:
        df = add_volatility_labels(df)
    
    if len(df) == 0:
        return VolatilityLabel.NORMAL.value, 1.0
    
    latest = df.iloc[-1]
    return latest['volatility_label'], latest['volatility_score']


# CLI for testing
if __name__ == "__main__":
    np.random.seed(42)
    n = 500
    
    # Generate sample data with varying volatility
    dates = pd.date_range(start='2024-01-01', periods=n, freq='15min')
    
    # Create volatility regimes
    volatility = np.ones(n)
    volatility[100:150] = 0.3  # Low vol period
    volatility[200:250] = 2.0  # High vol period
    volatility[350:370] = 3.5  # Extreme vol period
    
    close = 40000 + np.cumsum(np.random.randn(n) * 50 * volatility)
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close + np.random.randn(n) * 10 * volatility,
        'high': close + abs(np.random.randn(n) * 50 * volatility),
        'low': close - abs(np.random.randn(n) * 50 * volatility),
        'close': close,
        'volume': np.random.randint(1000, 5000, n)
    })
    
    # Classify
    classifier = VolatilityClassifier()
    df = classifier.classify(df)
    
    # Print stats
    print("Volatility Regime Distribution:")
    stats = classifier.get_regime_stats(df)
    for label, s in stats.items():
        print(f"  {label}: {s['count']} candles ({s['percentage']:.1f}%), avg_score={s['avg_volatility_score']:.2f}")
    
    # Show transitions
    print("\nSample volatility transitions:")
    sample_indices = [95, 105, 195, 205, 345, 355]
    for i in sample_indices:
        if i < len(df):
            row = df.iloc[i]
            print(f"  [{i}] score={row['volatility_score']:.2f} -> {row['volatility_label']}")
