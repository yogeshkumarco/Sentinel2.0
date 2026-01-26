# Model Training Pipeline

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
import logging
import json


import sys
# sys.path.append('..')
from app.core.config import settings as config, Timeframe, MarketState
from app.engine.storage import DataStorage
from app.engine.price_features import PriceFeatures
from app.engine.volatility_labels import add_volatility_labels
from app.models.Model_Sentinel.regime_classifier import RegimeClassifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ModelTrainer:
    """
    Training pipeline for regime classifier
    
    Steps:
    1. Load historical data
    2. Compute features
    3. Generate labels (rule-based or from outcomes)
    4. Train model
    5. Evaluate and save
    """
    
    def __init__(self, data_dir: Optional[str] = None):
        self.storage = DataStorage(data_dir or config.data_dir)
        self.classifier = RegimeClassifier()
        self.model_dir = Path(data_dir or config.data_dir) / "models"
        self.model_dir.mkdir(exist_ok=True)
    
    def load_training_data(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """Load OHLCV data for training"""
        df = self.storage.load_ohlcv(symbol, timeframe, start_time, end_time)
        
        if df.empty:
            logger.warning(f"No data found for {symbol} {timeframe.value}")
        else:
            logger.info(f"Loaded {len(df)} candles for {symbol} {timeframe.value}")
        
        return df
    
    def load_from_parquet(self, filepath: str) -> pd.DataFrame:
        """Load training data from parquet file"""
        df = pd.read_parquet(filepath)
        logger.info(f"Loaded {len(df)} rows from {filepath}")
        return df
    
    def load_from_csv(self, filepath: str) -> pd.DataFrame:
        """Load training data from CSV file"""
        df = pd.read_csv(filepath)
        
        # Normalize columns
        df.columns = df.columns.str.lower().str.strip()
        
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        elif 'date' in df.columns:
            df['timestamp'] = pd.to_datetime(df['date'])
        
        logger.info(f"Loaded {len(df)} rows from {filepath}")
        return df
    
    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all features from OHLCV"""
        # Price features
        pf = PriceFeatures(df)
        df = pf.compute_all()
        
        # Volatility labels
        df = add_volatility_labels(df)
        
        logger.info(f"Computed {len(pf.get_feature_columns())} features")
        return df
    
    def generate_labels_from_rules(self, df: pd.DataFrame) -> pd.Series:
        """Generate regime labels using rule-based classifier"""
        df = self.classifier.classify_rules(df)
        return df['regime_encoded']
    
    def generate_labels_from_outcomes(
        self,
        df: pd.DataFrame,
        forward_periods: int = 10,
        profit_threshold: float = 0.5
    ) -> pd.Series:
        """
        Generate labels based on future price outcome
        
        This creates supervised labels:
        - If price goes up significantly → TREND_BULL
        - If price goes down significantly → TREND_BEAR
        - Else → RANGE/VOLATILE based on volatility
        """
        df = df.copy()
        
        # Future returns
        df['future_return'] = df['close'].shift(-forward_periods) / df['close'] - 1
        df['future_return_pct'] = df['future_return'] * 100
        
        # Label based on outcome
        labels = []
        for idx, row in df.iterrows():
            future_ret = row.get('future_return_pct', 0)
            vol_score = row.get('volatility_score', 1)
            
            if pd.isna(future_ret):
                labels.append(2)  # RANGE
            elif future_ret > profit_threshold:
                labels.append(0)  # TREND_BULL
            elif future_ret < -profit_threshold:
                labels.append(1)  # TREND_BEAR
            elif vol_score > 1.4:
                labels.append(3)  # VOLATILE
            else:
                labels.append(2)  # RANGE
        
        return pd.Series(labels, index=df.index)
    
    def train(
        self,
        df: pd.DataFrame,
        model_type: str = 'xgboost',
        use_outcome_labels: bool = False
    ) -> Dict:
        """
        Train the regime classifier
        
        Args:
            df: DataFrame with features
            model_type: 'xgboost', 'random_forest', 'gradient_boost'
            use_outcome_labels: If True, use future outcome for labels
            
        Returns:
            Training metrics
        """
        # Generate labels
        if use_outcome_labels:
            labels = self.generate_labels_from_outcomes(df)
            logger.info("Using outcome-based labels")
        else:
            labels = self.generate_labels_from_rules(df)
            logger.info("Using rule-based labels")
        
        # Remove rows with NaN features
        feature_cols = self.classifier.FEATURE_COLS
        available_cols = [c for c in feature_cols if c in df.columns]
        
        valid_mask = df[available_cols].notna().all(axis=1) & labels.notna()
        df_clean = df[valid_mask]
        labels_clean = labels[valid_mask]
        
        logger.info(f"Training on {len(df_clean)} samples (dropped {len(df) - len(df_clean)} with NaN)")
        
        # Train
        metrics = self.classifier.train(df_clean, labels_clean, model_type)
        
        return metrics
    
    def save_model(self, name: Optional[str] = None):
        """Save trained model"""
        if name is None:
            name = f"regime_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        path = self.model_dir / f"{name}.pkl"
        self.classifier.save_model(str(path))
        
        return str(path)
    
    def run_pipeline(
        self,
        data_source: str,
        symbol: str = "BTCUSDT",
        timeframe: Timeframe = Timeframe.M15,
        model_type: str = 'xgboost',
        use_outcome_labels: bool = False
    ) -> Dict:
        """
        Run full training pipeline
        
        Args:
            data_source: 'db', path to csv, or path to parquet
            symbol: Trading symbol (for db source)
            timeframe: Timeframe (for db source)
            model_type: ML model type
            use_outcome_labels: Use future outcomes for labeling
            
        Returns:
            Training results
        """
        logger.info(f"Starting training pipeline")
        
        # 1. Load data
        if data_source == 'db':
            df = self.load_training_data(symbol, timeframe)
        elif data_source.endswith('.parquet'):
            df = self.load_from_parquet(data_source)
        elif data_source.endswith('.csv'):
            df = self.load_from_csv(data_source)
        else:
            raise ValueError(f"Unknown data source: {data_source}")
        
        if df.empty:
            raise ValueError("No data loaded")
        
        # 2. Compute features
        df = self.compute_features(df)
        
        # 3. Train
        metrics = self.train(df, model_type, use_outcome_labels)
        
        # 4. Save model
        model_path = self.save_model()
        metrics['model_path'] = model_path
        
        logger.info(f"Pipeline complete. Model saved to {model_path}")
        
        return metrics


# CLI for training
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Train regime classifier")
    parser.add_argument("--data", required=True, help="Data source: 'db', csv path, or parquet path")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol (for db)")
    parser.add_argument("--timeframe", default="15m", help="Timeframe (for db)")
    parser.add_argument("--model", default="xgboost", help="Model type")
    parser.add_argument("--outcome-labels", action="store_true", help="Use outcome-based labels")
    
    args = parser.parse_args()
    
    trainer = ModelTrainer()
    
    tf = Timeframe(args.timeframe)
    
    try:
        results = trainer.run_pipeline(
            data_source=args.data,
            symbol=args.symbol,
            timeframe=tf,
            model_type=args.model,
            use_outcome_labels=args.outcome_labels
        )
        
        print("\n" + "="*50)
        print("TRAINING RESULTS")
        print("="*50)
        print(f"Accuracy: {results['accuracy']:.4f}")
        print(f"Model Path: {results['model_path']}")
        print(f"\nClassification Report:\n{results['report']}")
        
    except Exception as e:
        logger.error(f"Training failed: {e}")
        raise
