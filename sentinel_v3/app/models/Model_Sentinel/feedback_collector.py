# Feedback Collector - Records trade outcomes for model learning
# Stores features at entry + exit results for retraining

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
import json
import sqlite3
from pathlib import Path

import logging
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FeedbackCollector:
    """
    Collects trade feedback for model retraining.
    
    Stores:
    - Features at trade entry
    - Trade outcome (WIN/LOSS/NEUTRAL)
    - P&L percentage
    
    Used to retrain model on actual trade results.
    """
    
    def __init__(self, db_path: str = None):
        path_obj = Path(db_path or str(Path(settings.data_dir) / "sentinel.db"))
        self.db_path = str(path_obj)
        # Ensure directory exists
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        self._init_table()
        
    def _init_table(self):
        """Create feedback table if not exists"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                entry_price REAL NOT NULL,
                exit_price REAL,
                pnl_pct REAL,
                outcome TEXT,  -- WIN, LOSS, NEUTRAL
                features_json TEXT,  -- Features at entry as JSON
                mode TEXT NOT NULL,  -- dry_run or live
                created_at TEXT NOT NULL
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("Feedback table initialized")
    
    def record_entry(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        features: Dict,
        mode: str = 'dry_run'
    ):
        """Record trade entry with current features"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Convert features to JSON (handle numpy types)
        features_clean = {}
        for k, v in features.items():
            if isinstance(v, (np.floating, np.integer)):
                features_clean[k] = float(v)
            elif isinstance(v, np.ndarray):
                features_clean[k] = v.tolist()
            else:
                features_clean[k] = v
        
        cursor.execute("""
            INSERT OR REPLACE INTO trade_feedback 
            (trade_id, symbol, side, entry_time, entry_price, features_json, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id,
            symbol,
            side,
            datetime.now().isoformat(),
            entry_price,
            json.dumps(features_clean),
            mode,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"[TRADE] Recorded entry for {trade_id}: {side} {symbol} @ {entry_price}")
    
    def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        pnl_pct: float
    ):
        """Record trade exit and outcome"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Classify outcome
        if pnl_pct >= 1.0:
            outcome = 'WIN'
        elif pnl_pct <= -1.0:
            outcome = 'LOSS'
        else:
            outcome = 'NEUTRAL'
        
        cursor.execute("""
            UPDATE trade_feedback 
            SET exit_time = ?, exit_price = ?, pnl_pct = ?, outcome = ?
            WHERE trade_id = ?
        """, (
            datetime.now().isoformat(),
            exit_price,
            pnl_pct,
            outcome,
            trade_id
        ))
        
        conn.commit()
        conn.close()
        
        emoji = "[WIN]" if outcome == 'WIN' else "[LOSS]" if outcome == 'LOSS' else "[EVEN]"
        logger.info(f"{emoji} Recorded exit for {trade_id}: {pnl_pct:+.2f}% ({outcome})")
    
    def get_feedback_data(self, min_trades: int = 10) -> pd.DataFrame:
        """
        Get feedback data for training.
        
        Returns DataFrame with features and outcomes.
        Only returns data if enough trades exist.
        """
        conn = sqlite3.connect(self.db_path)
        
        df = pd.read_sql_query("""
            SELECT * FROM trade_feedback
            WHERE outcome IS NOT NULL
            ORDER BY created_at DESC
        """, conn)
        
        conn.close()
        
        if len(df) < min_trades:
            logger.warning(f"Only {len(df)} trades with outcomes. Need {min_trades}+ for training.")
            return pd.DataFrame()
        
        # Parse features JSON back to columns
        feature_rows = []
        for _, row in df.iterrows():
            try:
                features = json.loads(row['features_json'])
                features['outcome'] = row['outcome']
                features['pnl_pct'] = row['pnl_pct']
                features['symbol'] = row['symbol']
                features['side'] = row['side']
                feature_rows.append(features)
            except:
                pass
        
        if not feature_rows:
            return pd.DataFrame()
            
        return pd.DataFrame(feature_rows)
    
    def get_stats(self) -> Dict:
        """Get feedback statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN outcome = 'NEUTRAL' THEN 1 ELSE 0 END) as neutral,
                AVG(pnl_pct) as avg_pnl
            FROM trade_feedback
            WHERE outcome IS NOT NULL
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        total = row[0] or 0
        wins = row[1] or 0
        losses = row[2] or 0
        
        return {
            'total_trades': total,
            'wins': wins,
            'losses': losses,
            'neutral': row[3] or 0,
            'win_rate': (wins / total * 100) if total > 0 else 0,
            'avg_pnl': row[4] or 0
        }


# CLI for testing
if __name__ == "__main__":
    collector = FeedbackCollector()
    
    # Test entry
    test_features = {
        'rsi': 65.5,
        'trend_score': 0.3,
        'ob_imbalance': 0.15,
        'return_5': 0.8
    }
    
    collector.record_entry(
        trade_id="TEST-001",
        symbol="XBTUSDTM",
        side="LONG",
        entry_price=95000,
        features=test_features,
        mode="dry_run"
    )
    
    # Test exit
    collector.record_exit(
        trade_id="TEST-001",
        exit_price=96500,
        pnl_pct=1.58
    )
    
    # Get stats
    print("\nFeedback Stats:")
    print(collector.get_stats())
