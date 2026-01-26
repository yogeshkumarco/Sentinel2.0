# Data Storage - SQLite and Parquet

import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, List
import logging
import os


import sys
# sys.path.append('..')
from app.core.config import settings as config, Timeframe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataStorage:
    """Store and retrieve historical data"""
    
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = self.data_dir / "sentinel.db"
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # OHLCV table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                taker_buy_volume REAL,
                taker_sell_volume REAL,
                UNIQUE(symbol, timeframe, timestamp)
            )
        """)
        
        # Open Interest table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS open_interest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                open_interest REAL NOT NULL,
                open_interest_value REAL,
                oi_change REAL,
                UNIQUE(symbol, timestamp)
            )
        """)
        
        # Funding Rate table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funding_rate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                funding_rate REAL NOT NULL,
                extreme_funding INTEGER,
                UNIQUE(symbol, timestamp)
            )
        """)
        
        # Decisions log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                symbol TEXT NOT NULL,
                market_state TEXT NOT NULL,
                direction_bias TEXT NOT NULL,
                setup_quality REAL NOT NULL,
                confidence REAL NOT NULL,
                recommendation TEXT NOT NULL,
                reasoning TEXT,
                outcome TEXT,
                pnl REAL
            )
        """)
        
        # Trades table (for position tracking)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                exit_price REAL,
                exit_time TEXT,
                size REAL NOT NULL,
                leverage INTEGER NOT NULL,
                mode TEXT NOT NULL,
                pnl_pct REAL,
                realized_pnl REAL,
                outcome TEXT,
                status TEXT DEFAULT 'OPEN'
            )
        """)
        
        # Daily P&L table (for calendar)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                total_pnl REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                mode TEXT NOT NULL
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf ON ohlcv(symbol, timeframe)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oi_symbol ON open_interest(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fr_symbol ON funding_rate(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_pnl(date)")
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")
    
    def save_ohlcv(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: Timeframe
    ) -> int:
        """
        Save OHLCV data to database
        
        Args:
            df: DataFrame with OHLCV data
            symbol: Trading pair
            timeframe: Candle timeframe
            
        Returns:
            Number of rows saved
        """
        conn = sqlite3.connect(self.db_path)
        
        df_save = df.copy()
        df_save['symbol'] = symbol
        df_save['timeframe'] = timeframe.value
        
        # Ensure required columns
        required = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        for col in required:
            if col not in df_save.columns:
                raise ValueError(f"Missing column: {col}")
        
        # Optional columns
        if 'taker_buy_volume' not in df_save.columns:
            df_save['taker_buy_volume'] = None
        if 'taker_sell_volume' not in df_save.columns:
            df_save['taker_sell_volume'] = None
        
        cols = ['symbol', 'timeframe', 'timestamp', 'open', 'high', 'low', 
                'close', 'volume', 'taker_buy_volume', 'taker_sell_volume']
        
        try:
            df_save[cols].to_sql(
                'ohlcv', conn, if_exists='append', index=False,
                method='multi'
            )
            saved = len(df_save)
        except sqlite3.IntegrityError:
            # Handle duplicates by inserting one by one
            saved = 0
            for _, row in df_save[cols].iterrows():
                try:
                    pd.DataFrame([row]).to_sql(
                        'ohlcv', conn, if_exists='append', index=False
                    )
                    saved += 1
                except sqlite3.IntegrityError:
                    pass  # Skip duplicates
        
        conn.close()
        logger.info(f"Saved {saved} OHLCV records for {symbol} {timeframe.value}")
        return saved
    
    def load_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Load OHLCV data from database
        
        Returns:
            DataFrame with OHLCV data
        """
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT timestamp, open, high, low, close, volume,
                   taker_buy_volume, taker_sell_volume
            FROM ohlcv
            WHERE symbol = ? AND timeframe = ?
        """
        params = [symbol, timeframe.value]
        
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.isoformat())
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.isoformat())
        
        query += " ORDER BY timestamp DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)
        
        return df
    
    def save_decision(
        self,
        symbol: str,
        decision: dict
    ):
        """Save a trading decision to the log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO decisions 
            (timestamp, symbol, market_state, direction_bias, setup_quality,
             confidence, recommendation, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            symbol,
            decision.get('market_state', ''),
            decision.get('direction_bias', ''),
            decision.get('setup_quality', 0),
            decision.get('confidence', 0),
            decision.get('recommendation', ''),
            decision.get('reasoning', '')
        ))
        
        conn.commit()
        conn.close()
    
    def save_to_parquet(
        self,
        df: pd.DataFrame,
        filename: str
    ):
        """Save DataFrame to Parquet file for large datasets"""
        filepath = self.data_dir / filename
        df.to_parquet(filepath, index=False)
        logger.info(f"Saved {len(df)} records to {filepath}")
    
    def load_from_parquet(self, filename: str) -> pd.DataFrame:
        """Load DataFrame from Parquet file"""
        filepath = self.data_dir / filename
        if filepath.exists():
            return pd.read_parquet(filepath)
        return pd.DataFrame()
    
    def get_latest_timestamp(
        self,
        symbol: str,
        timeframe: Timeframe
    ) -> Optional[datetime]:
        """Get the most recent timestamp for a symbol/timeframe"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT MAX(timestamp) FROM ohlcv
            WHERE symbol = ? AND timeframe = ?
        """, (symbol, timeframe.value))
        
        result = cursor.fetchone()[0]
        conn.close()
        
        if result:
            return datetime.fromisoformat(result)
        return None
    
    def save_trade(self, trade: dict):
        """Save a trade/position to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trades 
            (position_id, symbol, exchange, side, entry_price, entry_time,
             exit_price, exit_time, size, leverage, mode, pnl_pct, realized_pnl, outcome, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get('id'),
            trade.get('symbol'),
            trade.get('exchange'),
            trade.get('side'),
            trade.get('entry_price'),
            trade.get('entry_time'),
            trade.get('exit_price'),
            trade.get('exit_time'),
            trade.get('size'),
            trade.get('leverage'),
            trade.get('mode'),
            trade.get('pnl_pct'),
            trade.get('realized_pnl'),
            trade.get('outcome'),
            trade.get('status', 'OPEN')
        ))
        
        conn.commit()
        conn.close()
    
    def get_trades(self, status: str = None, mode: str = None, limit: int = 100) -> List[dict]:
        """Get trades from database"""
        conn = sqlite3.connect(self.db_path)
        
        query = "SELECT * FROM trades"
        conditions = []
        params = []
        
        if status:
            conditions.append("status = ?")
            params.append(status)
            
        if mode:
            conditions.append("mode = ?")
            params.append(mode)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        return df.to_dict('records')
    
    def update_daily_pnl(self, date: str, pnl: float, is_win: bool, mode: str = 'dry_run'):
        """Update or insert daily P&L record"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if exists
        cursor.execute("SELECT * FROM daily_pnl WHERE date = ?", (date,))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute("""
                UPDATE daily_pnl 
                SET total_pnl = total_pnl + ?,
                    trade_count = trade_count + 1,
                    wins = wins + ?,
                    losses = losses + ?
                WHERE date = ?
            """, (pnl, 1 if is_win else 0, 0 if is_win else 1, date))
        else:
            cursor.execute("""
                INSERT INTO daily_pnl (date, total_pnl, trade_count, wins, losses, mode)
                VALUES (?, ?, 1, ?, ?, ?)
            """, (date, pnl, 1 if is_win else 0, 0 if is_win else 1, mode))
        
        conn.commit()
        conn.close()
    
    def get_daily_pnl(self, mode: str = None, days: int = 30) -> List[dict]:
        """Get daily P&L for calendar (last N days)"""
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT date, total_pnl, trade_count, wins, losses, mode
            FROM daily_pnl
        """
        params = []
        
        if mode:
            query += " WHERE mode = ?"
            params.append(mode)
            
        query += " ORDER BY date DESC LIMIT ?"
        params.append(days)
        
        df = pd.read_sql_query(query, conn, params=params)
        
        conn.close()
        return df.to_dict('records')


# CLI for testing
if __name__ == "__main__":
    storage = DataStorage()
    
    # Test with sample data
    sample_data = pd.DataFrame({
        'timestamp': pd.date_range(start='2024-01-01', periods=10, freq='15min'),
        'open': [100 + i for i in range(10)],
        'high': [101 + i for i in range(10)],
        'low': [99 + i for i in range(10)],
        'close': [100.5 + i for i in range(10)],
        'volume': [1000 + i * 100 for i in range(10)]
    })
    
    print("Saving sample data...")
    storage.save_ohlcv(sample_data, "BTCUSDT", Timeframe.M15)
    
    print("\nLoading data...")
    loaded = storage.load_ohlcv("BTCUSDT", Timeframe.M15)
    print(loaded)
