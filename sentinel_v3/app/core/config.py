
import os
from dataclasses import dataclass, field
from typing import List
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

class Mode(Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class Timeframe(Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class MarketState(Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"
    BREAKOUT = "BREAKOUT"
    MOMENTUM = "MOMENTUM"


class DirectionBias(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class Recommendation(Enum):
    ALLOW_TRADE = "ALLOW_TRADE"
    MICRO_TRADE = "MICRO_TRADE"
    NO_TRADE = "NO_TRADE"


class VolatilityLabel(Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class ExchangeConfig:
    """API configuration for exchanges"""
    name: str
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True


# Leverage Tiers (Symbol -> Max Leverage)
LEVERAGE_TIERS = {
    # Tier 1: High Liquidity
    "XBTUSDTM": 15,
    "BTCUSDT": 15,
    "ETHUSDTM": 15,
    "ETHUSDT": 15,
    
    # Tier 2: Medium Liquidity
    "SOLUSDTM": 8,
    "SOLUSDT": 8,
    "XRPUSDTM": 8,
    "XRPUSDT": 8,
    "ADAUSDTM": 8,
    "ADAUSDT": 8,
    
    # Tier 3: Lower Liquidity / Meme Coins
    "DOGEUSDTM": 3,
    "DOGEUSDT": 3,
    "PEPEUSDTM": 3,
    "PEPEUSDT": 3,
}


@dataclass
class TradingConfig:
    """Trading parameters"""
    # Trading pairs (Updated for Sniper Strategy)
    symbols: List[str] = field(default_factory=lambda: [
        "XBTUSDTM",    # BTC
        "ETHUSDTM",    # ETH
        "SOLUSDTM",    # SOL
        "XRPUSDTM",    # XRP
        "DOGEUSDTM",   # DOGE
        "ADAUSDTM",    # ADA
    ])
    
    # WebSocket Toggle (Enable for low latency)
    use_websockets: bool = True
    
    # Dynamic Watchlist
    use_dynamic_watchlist: bool = True
    watchlist_limit: int = 15  # Top N coins by volume
    
    # Timeframes to analyze
    timeframes: List[Timeframe] = field(default_factory=lambda: [
        Timeframe.M5,   # Execution refinement
        Timeframe.M15,  # Signal generation
        Timeframe.H1,   # Context
    ])
    
    # Primary Timeframe for Analysis (15m as requested)
    primary_timeframe: str = "15m"
    
    # Strategy Mode
    strategy_mode: str = "sniper"  # "sniper" = Harmonic Patterns + Intraday Logic
    
    # Bot logic settings (Intraday Specifics)
    take_profit_pct: float = 2.5  # Target +2.5% for partials
    stop_loss_pct: float = 2.0    # 2% Risk (Adjusted dynamically by leverage)
    trailing_stop_pct: float = 0.5 # Tighter trail
    
    # Harmonic Pattern Specifics
    max_hold_candles: int = 30  # ~2 hours max hold on 5m
    min_volume_multiplier: float = 1.2  # Volume must be 1.2x average
    max_spread_pct: float = 0.1


@dataclass
class RiskConfig:
    """Risk management parameters"""
    # Max risk per trade (% of capital) - SAFE INTRADAY
    max_risk_per_trade_pct: float = 1.5
    
    # Max daily loss (% of capital)
    max_daily_loss: float = 4.0
    
    # Max drawdown before pause (% of capital)
    max_drawdown: float = 8.0
    
    # Max concurrent positions
    max_positions: int = 4
    
    # Max leverage allowed (overridden by LEVERAGE_TIERS per symbol)
    max_leverage: int = 10
    
    # Cooldown after loss (seconds)
    loss_cooldown: int = 300


@dataclass
class ModelConfig:
    """Model parameters"""
    # Minimum setup quality to consider trade (lowered for more trades)
    min_setup_quality: float = 0.35
    
    # Minimum confidence for ALLOW_TRADE (lowered for more trades)
    min_confidence_allow: float = 0.45
    
    # Minimum confidence for MICRO_TRADE
    min_confidence_micro: float = 0.5
    
    # Lookback periods for features
    lookback_candles: int = 100


@dataclass
class Config:
    """Main configuration"""
    # Operating mode
    mode: Mode = Mode.LIVE
    
    # Exchange Selection
    exchange_provider: str = field(default_factory=lambda: os.getenv("EXCHANGE_PROVIDER", "binance").lower())

    # Exchange configs
    binance: ExchangeConfig = field(default_factory=lambda: ExchangeConfig(
        name="binance",
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        testnet=True
    ))
    
    bybit: ExchangeConfig = field(default_factory=lambda: ExchangeConfig(
        name="bybit",
        api_key=os.getenv("BYBIT_API_KEY", ""),
        api_secret=os.getenv("BYBIT_API_SECRET", ""),
        testnet=True
    ))
    
    kucoin: ExchangeConfig = field(default_factory=lambda: ExchangeConfig(
        name="kucoin",
        api_key=os.getenv("KUCOIN_API_KEY", ""),
        api_secret=os.getenv("KUCOIN_API_SECRET", ""),
        testnet=False
    ))
    
    # KuCoin passphrase (separate field)
    kucoin_passphrase: str = field(default_factory=lambda: os.getenv("KUCOIN_PASSPHRASE", ""))
    
    # Sub-configs
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    
    # Data storage
    data_dir: str = "./data_storage"
    logs_dir: str = "./logs"


# Global config instance
settings = Config()
