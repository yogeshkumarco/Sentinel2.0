# Market Regime Classifier

import pandas as pd
import numpy as np
from typing import Tuple, Optional, Dict
import logging
import pickle
from pathlib import Path

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, accuracy_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False


import sys
# sys.path.append('..')
from app.core.config import MarketState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RegimeClassifier:
    """
    Classify market into regimes:
    - TREND_BULL: Clear uptrend with higher highs/lows
    - TREND_BEAR: Clear downtrend with lower highs/lows
    - RANGE: Sideways movement, bounded price action
    - VOLATILE: High volatility, unclear direction
    - BREAKOUT: Strong move breaking key levels
    - MOMENTUM: Sustained velocity in one direction
    
    Supports:
    - Rule-based classification (immediate)
    - ML-based classification (after training)
    """
    
    # Feature columns used for classification
    FEATURE_COLS = [
        'return_1', 'return_5', 'return_15',
        'trend_score',
        'dist_ema_9', 'dist_ema_21',
        'ema_9_slope', 'ema_21_slope',
        'atr_ratio', 'volatility_score',
        'rsi', 'macd_diff',
        'body_range_ratio', 'range_position',
        'volume_ratio',
        # Futures
        'oi_change', 'funding_rate', 'long_short_ratio',
        # Order Book
        'ob_spread_pct', 'ob_imbalance', 'ob_imbalance_1pct', 
        'ob_large_bid_walls', 'ob_large_ask_walls',
        # Exchange differentiation
        'exchange_encoded'
    ]
    
    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.scaler = None
        self.model_path = model_path
        self.use_ml = False
        
        if model_path and Path(model_path).exists():
            self.load_model(model_path)
    
    def classify_rules(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Rule-based regime classification
        
        Args:
            df: DataFrame with computed features
            
        Returns:
            DataFrame with 'regime' column
        """
        df = df.copy()
        
        # Initialize regime as RANGE (default)
        df['regime'] = MarketState.RANGE.value
        
        # Get required columns with fallbacks
        trend_score = df.get('trend_score', pd.Series(0, index=df.index))
        volatility_score = df.get('volatility_score', pd.Series(1, index=df.index))
        atr_ratio = df.get('atr_ratio', pd.Series(1, index=df.index))
        return_5 = df.get('return_5', pd.Series(0, index=df.index))
        ema_slope = df.get('ema_9_slope', pd.Series(0, index=df.index))
        rsi = df.get('rsi', pd.Series(50, index=df.index))
        volume_ratio = df.get('volume_ratio', pd.Series(1, index=df.index))
        
        # EXTREME VOLATILITY (highest priority)
        extreme_vol = volatility_score >= 2.0
        df.loc[extreme_vol, 'regime'] = MarketState.VOLATILE.value
        
        # BREAKOUT: Strong move + high volume + breaking range
        breakout_up = (
            (return_5 > 1.5) &  # Strong up move
            (volume_ratio > 1.5) &  # High volume
            (ema_slope > 0.1) &  # EMAs turning up
            ~extreme_vol
        )
        breakout_down = (
            (return_5 < -1.5) &  # Strong down move
            (volume_ratio > 1.5) &  # High volume
            (ema_slope < -0.1) &  # EMAs turning down
            ~extreme_vol
        )
        df.loc[breakout_up | breakout_down, 'regime'] = MarketState.BREAKOUT.value
        
        # MOMENTUM: Sustained move with trend confirmation
        momentum_up = (
            (trend_score > 0.4) &  # Bullish structure
            (ema_slope > 0.05) &  # EMAs sloping up
            (rsi > 55) & (rsi < 80) &  # Momentum but not overbought
            ~extreme_vol & ~breakout_up & ~breakout_down
        )
        momentum_down = (
            (trend_score < -0.4) &  # Bearish structure
            (ema_slope < -0.05) &  # EMAs sloping down
            (rsi < 45) & (rsi > 20) &  # Momentum but not oversold
            ~extreme_vol & ~breakout_up & ~breakout_down
        )
        df.loc[momentum_up | momentum_down, 'regime'] = MarketState.MOMENTUM.value
        
        # TREND_BULL: Clear bullish structure
        trend_bull = (
            (trend_score > 0.3) &  # More highs than lows
            (ema_slope > 0) &  # EMAs pointing up
            (volatility_score < 1.5) &  # Not too volatile
            ~extreme_vol & ~(breakout_up | breakout_down) & ~(momentum_up | momentum_down)
        )
        df.loc[trend_bull, 'regime'] = MarketState.TREND_BULL.value
        
        # TREND_BEAR: Clear bearish structure
        trend_bear = (
            (trend_score < -0.3) &  # More lows than highs
            (ema_slope < 0) &  # EMAs pointing down
            (volatility_score < 1.5) &  # Not too volatile
            ~extreme_vol & ~(breakout_up | breakout_down) & ~(momentum_up | momentum_down)
        )
        df.loc[trend_bear, 'regime'] = MarketState.TREND_BEAR.value
        
        # VOLATILE: High volatility without clear direction
        high_vol = (
            (volatility_score > 1.4) & (volatility_score < 2.0) &
            (abs(trend_score) < 0.2) &
            ~(breakout_up | breakout_down)
        )
        df.loc[high_vol, 'regime'] = MarketState.VOLATILE.value
        
        # Numeric encoding
        regime_map = {
            MarketState.TREND_BULL.value: 0,
            MarketState.TREND_BEAR.value: 1,
            MarketState.RANGE.value: 2,
            MarketState.VOLATILE.value: 3,
            MarketState.BREAKOUT.value: 4,
            MarketState.MOMENTUM.value: 5
        }
        df['regime_encoded'] = df['regime'].map(regime_map)
        
        return df
    
    def prepare_features(self, df: pd.DataFrame) -> Tuple[np.ndarray, list]:
        """
        Prepare feature matrix for ML
        
        Returns:
            Tuple of (feature_matrix, available_columns)
        """
        available_cols = [col for col in self.FEATURE_COLS if col in df.columns]
        
        if len(available_cols) < 5:
            logger.warning(f"Only {len(available_cols)} features available")
        
        X = df[available_cols].fillna(0).values
        return X, available_cols
    
    def train(
        self,
        df: pd.DataFrame,
        labels: pd.Series,
        model_type: str = 'xgboost'
    ) -> Dict:
        """
        Train ML regime classifier
        
        Args:
            df: Feature DataFrame
            labels: Regime labels (from rule-based or manual)
            model_type: 'xgboost', 'random_forest', or 'gradient_boost'
            
        Returns:
            Training metrics dict
        """
        if not SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn required for training")
        
        from sklearn.preprocessing import LabelEncoder
        
        X, feature_cols = self.prepare_features(df)
        y = labels.values
        
        # Encode labels to consecutive integers (XGBoost requirement)
        self.label_encoder = LabelEncoder()
        y_encoded = self.label_encoder.fit_transform(y)
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y_encoded, test_size=0.2, random_state=42
        )
        
        # Scale features
        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        X_test = self.scaler.transform(X_test)
        
        # Select model
        if model_type == 'xgboost' and XGBOOST_AVAILABLE:
            self.model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42
            )
        elif model_type == 'random_forest':
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                random_state=42
            )
        else:
            self.model = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=5,
                random_state=42
            )
        
        # Train
        self.model.fit(X_train, y_train)
        self.use_ml = True
        
        # Evaluate
        y_pred = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        
        logger.info(f"Model trained: {model_type}, accuracy: {accuracy:.3f}")
        logger.info(f"Classes: {self.label_encoder.classes_}")
        
        return {
            'model_type': model_type,
            'accuracy': accuracy,
            'features_used': feature_cols,
            'n_samples': len(X),
            'report': classification_report(y_test, y_pred)
        }
    
    def classify_ml(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        ML-based regime classification
        
        Args:
            df: DataFrame with features
            
        Returns:
            DataFrame with 'regime' column
        """
        if not self.use_ml or self.model is None:
            logger.warning("ML model not trained, using rules")
            return self.classify_rules(df)
        
        df = df.copy()
        X, _ = self.prepare_features(df)
        
        if self.scaler is not None:
            X = self.scaler.transform(X)
        
        predictions = self.model.predict(X)
        
        # Map back to labels
        regime_reverse = {
            0: MarketState.TREND_BULL.value,
            1: MarketState.TREND_BEAR.value,
            2: MarketState.RANGE.value,
            3: MarketState.VOLATILE.value,
            4: MarketState.BREAKOUT.value,
            5: MarketState.MOMENTUM.value
        }
        
        df['regime'] = [regime_reverse.get(p, MarketState.RANGE.value) for p in predictions]
        df['regime_encoded'] = predictions
        
        return df
    
    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Classify using ML if available, else rules
        """
        if self.use_ml and self.model is not None:
            return self.classify_ml(df)
        return self.classify_rules(df)
    
    def save_model(self, path: str):
        """Save trained model to file"""
        if self.model is None:
            raise ValueError("No model to save")
        
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'feature_cols': self.FEATURE_COLS
        }
        
        with open(path, 'wb') as f:
            pickle.dump(model_data, f)
        
        logger.info(f"Model saved to {path}")

    def save(self, path: str):
        """Alias for save_model"""
        self.save_model(path)
    
    def load_model(self, path: str):
        """Load trained model from file"""
        with open(path, 'rb') as f:
            model_data = pickle.load(f)
        
        self.model = model_data['model']
        self.scaler = model_data.get('scaler')
        self.use_ml = True
        
        logger.info(f"Model loaded from {path}")
    
    def get_current_regime(self, df: pd.DataFrame) -> Tuple[str, Optional[float]]:
        """
        Get current regime from most recent data
        
        Returns:
            Tuple of (regime_label, confidence)
        """
        if len(df) == 0:
            return MarketState.RANGE.value, None
        
        classified = self.classify(df)
        latest = classified.iloc[-1]
        
        # Confidence from ML model probabilities if available
        confidence = None
        if self.use_ml and hasattr(self.model, 'predict_proba'):
            X, _ = self.prepare_features(df.iloc[[-1]])
            if self.scaler is not None:
                X = self.scaler.transform(X)
            proba = self.model.predict_proba(X)[0]
            confidence = float(max(proba))
        
        return latest['regime'], confidence


def classify_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience function for rule-based classification"""
    classifier = RegimeClassifier()
    return classifier.classify_rules(df)


def get_regime(df: pd.DataFrame) -> str:
    """Get current regime label"""
    classifier = RegimeClassifier()
    regime, _ = classifier.get_current_regime(df)
    return regime


# CLI for testing
if __name__ == "__main__":
    np.random.seed(42)
    n = 300
    
    # Generate sample data with features
    df = pd.DataFrame({
        'close': 40000 + np.cumsum(np.random.randn(n) * 50),
        'return_1': np.random.randn(n) * 0.5,
        'return_5': np.random.randn(n) * 1.0,
        'return_15': np.random.randn(n) * 1.5,
        'trend_score': np.random.randn(n) * 0.3,
        'dist_ema_9': np.random.randn(n) * 0.5,
        'dist_ema_21': np.random.randn(n) * 0.8,
        'ema_9_slope': np.random.randn(n) * 0.1,
        'ema_21_slope': np.random.randn(n) * 0.08,
        'atr_ratio': 1 + np.random.rand(n) * 0.5,
        'volatility_score': 1 + np.random.rand(n) * 0.8,
        'rsi': 30 + np.random.rand(n) * 40,
        'macd_diff': np.random.randn(n) * 20,
        'body_range_ratio': np.random.rand(n),
        'range_position': np.random.rand(n),
        'volume_ratio': 0.5 + np.random.rand(n) * 1.5
    })
    
    # Classify
    classifier = RegimeClassifier()
    df = classifier.classify_rules(df)
    
    print("Regime Distribution:")
    print(df['regime'].value_counts())
    
    print("\nSample classifications:")
    print(df[['close', 'trend_score', 'volatility_score', 'regime']].tail(10))
