"""
BotManager - Singleton Service
Manages independent TradingBot instances for each exchange
"""

import asyncio
import logging
from typing import Dict, Optional
from app.engine.bot import TradingBot
from app.exchanges.binance_adapter import BinanceAdapter
from app.exchanges.kucoin_adapter import KuCoinAdapter
from app.core.config import settings

logger = logging.getLogger("BotManager")


class BotManager:
    """
    Singleton managing bot instances for each exchange
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.bots: Dict[str, TradingBot] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        
        # Initialize bot instances for each exchange
        self._init_bots()
        
        logger.info("🤖 BotManager initialized")
    
    def _init_bots(self):
        """Create TradingBot instances for each exchange"""
        
        # Binance Bot
        binance_exchange = BinanceAdapter()
        self.bots["binance"] = TradingBot(
            exchange=binance_exchange,
            exchange_name="binance"
        )
        
        # KuCoin Bot
        kucoin_exchange = KuCoinAdapter()
        self.bots["kucoin"] = TradingBot(
            exchange=kucoin_exchange,
            exchange_name="kucoin"
        )
        
        logger.info(f"✅ Initialized bots: {list(self.bots.keys())}")
    
    def get_bot(self, exchange: str) -> Optional[TradingBot]:
        """Get bot instance for specific exchange"""
        return self.bots.get(exchange)
    
    async def start_bot(self, exchange: str) -> bool:
        """Start bot for specific exchange"""
        bot = self.bots.get(exchange)
        if not bot:
            logger.error(f"❌ No bot found for exchange: {exchange}")
            return False
        
        if bot.running:
            logger.warning(f"⚠️ Bot already running for {exchange}")
            return False
        
        # Start bot loop as asyncio task
        task = asyncio.create_task(bot.start())
        self.tasks[exchange] = task
        
        logger.info(f"🚀 Started {exchange} bot")
        return True
    
    async def stop_bot(self, exchange: str) -> bool:
        """Stop bot for specific exchange"""
        bot = self.bots.get(exchange)
        if not bot:
            return False
        
        bot.stop()
        
        # Cancel task if exists
        if exchange in self.tasks:
            task = self.tasks[exchange]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self.tasks[exchange]
        
        logger.info(f"🛑 Stopped {exchange} bot")
        return True
    
    def is_running(self, exchange: str) -> bool:
        """Check if bot is running for exchange"""
        bot = self.bots.get(exchange)
        return bot.running if bot else False


# Global singleton instance
_bot_manager: Optional[BotManager] = None


def get_bot_manager() -> BotManager:
    """Dependency injection for FastAPI"""
    global _bot_manager
    if _bot_manager is None:
        _bot_manager = BotManager()
    return _bot_manager


# Legacy compatibility
def get_bot() -> TradingBot:
    """Legacy function - returns Binance bot by default"""
    manager = get_bot_manager()
    return manager.get_bot("binance")
