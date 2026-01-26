# Price Features - Derived from OHLCV

import pandas as pd
import numpy as np
from typing import Optional
import logging

try:
    import ta
    from ta.volatility import AverageTrueRange, BollingerBands
    from ta.trend import EMAIndicator, SMAIndicator, MACD
    from ta.momentum import RSIIndicator, StochasticOscillator
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PriceFeatures:
    """
    Calculate derived price features from OHLCV data
    
    Features computed:
    - Returns (% change)
    - Candle body size / wick ratios
    - Range %
    - Distance from high/low
    - Distance from moving averages
    - ATR (volatility)
    - Momentum indicators
    """
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize with OHLCV DataFrame
        
        Args:
            df: DataFrame with columns [timestamp, open, high, low, close, volume]
        """
        self.df = df.copy()
        self._validate()
    
    def _validate(self):
        """Validate required columns exist"""
        required = ['open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required if col not in self.df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
    
        return self
    
    def add_returns(self, periods: list = [1, 5, 15, 30]) -> 'PriceFeatures':
        """
        Add percentage returns for multiple periods
        """
        for p in periods:
            self.df[f'return_{p}'] = self.df['close'].pct_change(p, fill_method=None) * 100
        return self

    # ... (Skipping intermediate functions to focus on pct_change usage) 
    
    def add_moving_averages(self, ema_periods=[9,21,50], sma_periods=[20,50,200]):
        # ... (Same logic until slope)
        if not TA_AVAILABLE:
             # Manual calculation
            for p in ema_periods:
                self.df[f'ema_{p}'] = self.df['close'].ewm(span=p, adjust=False).mean()
                self.df[f'dist_ema_{p}'] = ((self.df['close'] - self.df[f'ema_{p}']) / self.df[f'ema_{p}']) * 100
            
            for p in sma_periods:
                self.df[f'sma_{p}'] = self.df['close'].rolling(p).mean()
                self.df[f'dist_sma_{p}'] = ((self.df['close'] - self.df[f'sma_{p}']) / self.df[f'sma_{p}']) * 100
        else:
            for p in ema_periods:
                ema = EMAIndicator(close=self.df['close'], window=p)
                self.df[f'ema_{p}'] = ema.ema_indicator()
                self.df[f'dist_ema_{p}'] = ((self.df['close'] - self.df[f'ema_{p}']) / self.df[f'ema_{p}']) * 100
            
            for p in sma_periods:
                sma = SMAIndicator(close=self.df['close'], window=p)
                self.df[f'sma_{p}'] = sma.sma_indicator()
                self.df[f'dist_sma_{p}'] = ((self.df['close'] - self.df[f'sma_{p}']) / self.df[f'sma_{p}']) * 100
        
        # EMA slopes
        for p in ema_periods:
            self.df[f'ema_{p}_slope'] = self.df[f'ema_{p}'].pct_change(5, fill_method=None) * 100
        
        return self
    
    def add_candle_features(self) -> 'PriceFeatures':
        """Add candle body and wick features"""
        
        # Body size (absolute and relative)
        self.df['body'] = self.df['close'] - self.df['open']
        self.df['body_abs'] = self.df['body'].abs()
        self.df['body_pct'] = (self.df['body'] / self.df['open']) * 100
        
        # Range
        self.df['range'] = self.df['high'] - self.df['low']
        self.df['range_pct'] = (self.df['range'] / self.df['low']) * 100
        
        # Body to range ratio
        self.df['body_range_ratio'] = np.where(
            self.df['range'] > 0,
            self.df['body_abs'] / self.df['range'],
            0
        )
        
        # Wick sizes
        self.df['upper_wick'] = self.df['high'] - self.df[['open', 'close']].max(axis=1)
        self.df['lower_wick'] = self.df[['open', 'close']].min(axis=1) - self.df['low']
        
        # Wick ratios
        self.df['upper_wick_ratio'] = np.where(
            self.df['range'] > 0,
            self.df['upper_wick'] / self.df['range'],
            0
        )
        self.df['lower_wick_ratio'] = np.where(
            self.df['range'] > 0,
            self.df['lower_wick'] / self.df['range'],
            0
        )
        
        # Candle direction
        self.df['is_bullish'] = (self.df['close'] > self.df['open']).astype(int)
        self.df['is_bearish'] = (self.df['close'] < self.df['open']).astype(int)
        
        # --- SCALP FEATURES ---
        # 1. Rejection signals (>40% of range is wick)
        self.df['rejection_top'] = (self.df['upper_wick_ratio'] > 0.4).astype(int)
        self.df['rejection_bottom'] = (self.df['lower_wick_ratio'] > 0.4).astype(int)
        
        # 2. Color Consistency (3 candle streak)
        # Shift introduces NaNs (floats), so we must fill them
        bull_1 = self.df['is_bullish'].shift(1).fillna(0).astype(int)
        bull_2 = self.df['is_bullish'].shift(2).fillna(0).astype(int)
        
        self.df['green_streak_3'] = (
            self.df['is_bullish'] & bull_1 & bull_2
        ).astype(int)
        
        bear_1 = self.df['is_bearish'].shift(1).fillna(0).astype(int)
        bear_2 = self.df['is_bearish'].shift(2).fillna(0).astype(int)
        
        self.df['red_streak_3'] = (
            self.df['is_bearish'] & bear_1 & bear_2
        ).astype(int)
        
        return self
    
    def add_distance_features(self, lookback: int = 20) -> 'PriceFeatures':
        """
        Add distance from recent high/low and moving averages
        
        Args:
            lookback: Period for rolling high/low
        """
        # Distance from rolling high/low
        rolling_high = self.df['high'].rolling(lookback).max()
        rolling_low = self.df['low'].rolling(lookback).min()
        
        self.df['dist_from_high'] = ((self.df['close'] - rolling_high) / rolling_high) * 100
        self.df['dist_from_low'] = ((self.df['close'] - rolling_low) / rolling_low) * 100
        
        # Position within range (0 = at low, 1 = at high)
        range_size = rolling_high - rolling_low
        self.df['range_position'] = np.where(
            range_size > 0,
            (self.df['close'] - rolling_low) / range_size,
            0.5
        )
        
        return self
    
    def add_moving_averages(
        self,
        ema_periods: list = [9, 21, 50],
        sma_periods: list = [20, 50, 200]
    ) -> 'PriceFeatures':
        """
        Add EMAs, SMAs and distance from them
        """
        if not TA_AVAILABLE:
            # Manual calculation
            for p in ema_periods:
                self.df[f'ema_{p}'] = self.df['close'].ewm(span=p, adjust=False).mean()
                self.df[f'dist_ema_{p}'] = ((self.df['close'] - self.df[f'ema_{p}']) / self.df[f'ema_{p}']) * 100
            
            for p in sma_periods:
                self.df[f'sma_{p}'] = self.df['close'].rolling(p).mean()
                self.df[f'dist_sma_{p}'] = ((self.df['close'] - self.df[f'sma_{p}']) / self.df[f'sma_{p}']) * 100
        else:
            for p in ema_periods:
                ema = EMAIndicator(close=self.df['close'], window=p)
                self.df[f'ema_{p}'] = ema.ema_indicator()
                self.df[f'dist_ema_{p}'] = ((self.df['close'] - self.df[f'ema_{p}']) / self.df[f'ema_{p}']) * 100
            
            for p in sma_periods:
                sma = SMAIndicator(close=self.df['close'], window=p)
                self.df[f'sma_{p}'] = sma.sma_indicator()
                self.df[f'dist_sma_{p}'] = ((self.df['close'] - self.df[f'sma_{p}']) / self.df[f'sma_{p}']) * 100
        
        # EMA slopes
        for p in ema_periods:
            self.df[f'ema_{p}_slope'] = self.df[f'ema_{p}'].pct_change(5) * 100
        
        return self
    
    def add_volatility_features(self, atr_period: int = 14) -> 'PriceFeatures':
        """
        Add ATR and Bollinger Band width
        """
        if not TA_AVAILABLE:
            # Manual ATR calculation
            tr1 = self.df['high'] - self.df['low']
            tr2 = abs(self.df['high'] - self.df['close'].shift())
            tr3 = abs(self.df['low'] - self.df['close'].shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            self.df['atr'] = tr.rolling(atr_period).mean()
        else:
            atr = AverageTrueRange(
                high=self.df['high'],
                low=self.df['low'],
                close=self.df['close'],
                window=atr_period
            )
            self.df['atr'] = atr.average_true_range()
            
            bb = BollingerBands(close=self.df['close'], window=20, window_dev=2)
            self.df['bb_width'] = bb.bollinger_wband()
            self.df['bb_pct'] = bb.bollinger_pband()
        
        # ATR percentage
        self.df['atr_pct'] = (self.df['atr'] / self.df['close']) * 100
        
        # Volatility relative to average
        self.df['atr_ratio'] = self.df['atr'] / self.df['atr'].rolling(50).mean()
        
        return self
    
    def add_momentum_features(self) -> 'PriceFeatures':
        """Add RSI, MACD, Stochastic"""
        if not TA_AVAILABLE:
            logger.warning("ta library not available, skipping momentum features")
            return self
        
        # RSI
        rsi = RSIIndicator(close=self.df['close'], window=14)
        self.df['rsi'] = rsi.rsi()
        
        # MACD
        macd = MACD(close=self.df['close'])
        self.df['macd'] = macd.macd()
        self.df['macd_signal'] = macd.macd_signal()
        self.df['macd_diff'] = macd.macd_diff()
        
        # Stochastic
        stoch = StochasticOscillator(
            high=self.df['high'],
            low=self.df['low'],
            close=self.df['close']
        )
        self.df['stoch_k'] = stoch.stoch()
        self.df['stoch_d'] = stoch.stoch_signal()
        
        return self
    
    def add_volume_features(self) -> 'PriceFeatures':
        """Add volume-related features"""
        # Volume change
        self.df['volume_change'] = self.df['volume'].pct_change() * 100
        
        # Volume moving average
        self.df['volume_ma'] = self.df['volume'].rolling(20).mean()
        self.df['volume_ratio'] = self.df['volume'] / self.df['volume_ma']
        
        # Volume Trend (Last 3 candles rising)
        self.df['vol_increasing_3'] = (
            (self.df['volume'] > self.df['volume'].shift(1)) &
            (self.df['volume'].shift(1) > self.df['volume'].shift(2))
        ).astype(int)
        
        # Price-volume correlation (rolling)
        self.df['price_volume_corr'] = (
            self.df['close'].rolling(20).corr(self.df['volume'])
        )
        
        # Taker buy/sell ratio if available
        if 'taker_buy_volume' in self.df.columns and 'taker_sell_volume' in self.df.columns:
            total_taker = self.df['taker_buy_volume'] + self.df['taker_sell_volume']
            self.df['taker_buy_ratio'] = np.where(
                total_taker > 0,
                self.df['taker_buy_volume'] / total_taker,
                0.5
            )
            self.df['taker_imbalance'] = (
                self.df['taker_buy_volume'] - self.df['taker_sell_volume']
            ) / (total_taker + 1e-10)
        
        return self
    
    def add_trend_features(self) -> 'PriceFeatures':
        """Add trend identification features"""
        # Higher highs, higher lows (bullish structure)
        self.df['higher_high'] = (self.df['high'] > self.df['high'].shift(1)).astype(int)
        self.df['higher_low'] = (self.df['low'] > self.df['low'].shift(1)).astype(int)
        self.df['lower_high'] = (self.df['high'] < self.df['high'].shift(1)).astype(int)
        self.df['lower_low'] = (self.df['low'] < self.df['low'].shift(1)).astype(int)
        
        # Rolling counts
        lookback = 10
        self.df['hh_count'] = self.df['higher_high'].rolling(lookback).sum()
        self.df['hl_count'] = self.df['higher_low'].rolling(lookback).sum()
        self.df['lh_count'] = self.df['lower_high'].rolling(lookback).sum()
        self.df['ll_count'] = self.df['lower_low'].rolling(lookback).sum()
        
        # Trend score (-1 to 1)
        bullish_score = (self.df['hh_count'] + self.df['hl_count']) / (2 * lookback)
        bearish_score = (self.df['lh_count'] + self.df['ll_count']) / (2 * lookback)
        self.df['trend_score'] = bullish_score - bearish_score
        
        return self
    
    def compute_all(self) -> pd.DataFrame:
        """Compute all features and return DataFrame"""
        return (
            self.add_returns()
            .add_candle_features()
            .add_distance_features()
            .add_moving_averages()
            .add_volatility_features()
            .add_momentum_features()
            .add_volume_features()
            .add_trend_features()
            .df
        )
    
    def get_feature_columns(self) -> list:
        """Get list of computed feature columns (excludes OHLCV)"""
        base_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume',
                     'taker_buy_volume', 'taker_sell_volume']
        return [col for col in self.df.columns if col not in base_cols]


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience function to compute all features"""
    return PriceFeatures(df).compute_all()


# CLI for testing
if __name__ == "__main__":
    # Generate sample data
    np.random.seed(42)
    n = 200
    
    dates = pd.date_range(start='2024-01-01', periods=n, freq='15min')
    close = 40000 + np.cumsum(np.random.randn(n) * 50)
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close + np.random.randn(n) * 10,
        'high': close + abs(np.random.randn(n) * 50),
        'low': close - abs(np.random.randn(n) * 50),
        'close': close,
        'volume': np.random.randint(1000, 5000, n)
    })
    
    # Compute features
    pf = PriceFeatures(df)
    features_df = pf.compute_all()
    
    print(f"Original columns: {len(df.columns)}")
    print(f"With features: {len(features_df.columns)}")
    print(f"\nFeature columns ({len(pf.get_feature_columns())}):")
    for col in pf.get_feature_columns()[:20]:
        print(f"  - {col}")
    print("  ...")
    
    print(f"\nSample data:")
    print(features_df[['close', 'return_1', 'body_pct', 'atr_pct', 'trend_score']].tail(10))
