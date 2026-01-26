"""
API Endpoints for Sentinel V3
Matches frontend contract exactly
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
import logging
import os

from app.engine.services import get_bot_manager, BotManager
from app.core.config import settings, Mode

logger = logging.getLogger(__name__)

router = APIRouter()


# Request Models
class SettingsUpdate(BaseModel):
    mode: Optional[str] = None
    allocation: Optional[int] = None


# =============================================================================
# GLOBAL ENDPOINTS
# =============================================================================

@router.get("/settings")
async def get_settings():
    """
    Frontend expects: { "mode": "dry_run" | "live", "allocation": int, ... }
    """
    return {
        "mode": settings.mode.value,
        "allocation": 100,  # Default full allocation
        "execution": "auto",
        "balance": 10000  # Default paper balance
    }


@router.post("/settings")
async def update_settings(data: SettingsUpdate):
    """
    Update global settings (mode, allocation)
    Frontend sends: { "mode": "live" | "dry_run", "allocation": 25|50|100 }
    """
    if data.mode:
        # Update mode
        if data.mode == "live":
            settings.mode = Mode.LIVE
            logger.info("⚠️ Mode changed to LIVE")
        else:
            settings.mode = Mode.DRY_RUN
            logger.info("📝 Mode changed to DRY_RUN")
    
    if data.allocation:
        logger.info(f"Allocation set to {data.allocation}%")
    
    return {
        "status": "updated",
        "mode": settings.mode.value,
        "allocation": data.allocation or 100
    }


@router.get("/sentiment")
async def get_sentiment():
    """
    Frontend expects: { "regime": str, "direction": str, "confidence": float }
    """
    # Placeholder - can be enhanced with real sentiment analysis
    return {
        "regime": "NEUTRAL",
        "direction": "NONE",
        "confidence": 0.5
    }


@router.post("/reset")
async def reset_state(manager: BotManager = Depends(get_bot_manager)):
    """Hard reset paper trading state"""
    manager.reset_paper_state()
    return {"status": "reset", "message": "Paper trading state reset to $10,000"}


@router.get("/pnl/daily")
async def get_daily_pnl(manager: BotManager = Depends(get_bot_manager)):
    """
    Frontend expects: [{ "date": str, "total_pnl": float }]
    Aggregate P&L from all exchanges
    """
    total_pnl = 0.0
    
    # Aggregate from both exchanges
    for exchange_name in ["binance", "kucoin"]:
        bot = manager.get_bot(exchange_name)
        if not bot or not bot.exchange:
            continue
            
        # Check Mode
        if settings.mode == Mode.LIVE:
            # TODO: Implement Live PnL Fetching
            # For now, return 0 to avoid showing paper stats
            exchange_pnl = 0.0
        else:
            # Paper Mode
            if hasattr(bot.exchange, 'paper'):
                trades = bot.exchange.paper.trade_history
                exchange_pnl = sum(t.get('pnl', 0) for t in trades)
            else:
                exchange_pnl = 0.0
                
        total_pnl += exchange_pnl
    
    from datetime import date
    return [{
        "date": date.today().isoformat(),
        "total_pnl": round(total_pnl, 2)
    }]


@router.get("/trades")
async def get_trades(manager: BotManager = Depends(get_bot_manager)):
    """
    Frontend expects: List of trade objects
    [{ "symbol": str, "side": str, "entry_price": float, "exit_price": float, "pnl": float, "roe_pct": float }]
    """
    all_trades = []
    
    # Aggregate from both exchanges
    for exchange_name in ["binance", "kucoin"]:
        bot = manager.get_bot(exchange_name)
        if not bot or not bot.exchange:
            continue
            
        # Get trades (Adapter handles Paper/Live switch)
        if hasattr(bot.exchange, 'get_trade_history'):
            trades = bot.exchange.get_trade_history(limit=50)
            all_trades.extend(trades)
    
    # Deduplicate by unique key (symbol + timestamp + side + price)
    seen = set()
    unique_trades = []
    for t in all_trades:
        key = (t.get('symbol', ''), t.get('timestamp', ''), t.get('side', ''), t.get('entry_price', 0))
        if key not in seen:
            seen.add(key)
            unique_trades.append(t)
                
    # Sort by time desc
    unique_trades.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return unique_trades[:100]


# =============================================================================
# EXCHANGE-SPECIFIC ENDPOINTS
# =============================================================================

@router.post("/{exchange}/start")
async def start_bot(exchange: str = Path(..., regex="^(binance|kucoin)$"), manager: BotManager = Depends(get_bot_manager)):
    """
    Start bot for specific exchange (idempotent - returns success if already running)
    Frontend calls: POST /api/binance/start or POST /api/kucoin/start
    """
    # Check if already running
    if manager.is_running(exchange):
        return {"status": "already_running", "exchange": exchange}
    
    success = await manager.start_bot(exchange)
    if success:
        return {"status": "started", "exchange": exchange}
    else:
        raise HTTPException(status_code=400, detail=f"Failed to start {exchange} bot")


@router.post("/{exchange}/stop")
async def stop_bot(exchange: str = Path(..., regex="^(binance|kucoin)$"), manager: BotManager = Depends(get_bot_manager)):
    """
    Stop bot for specific exchange
    """
    success = await manager.stop_bot(exchange)
    if success:
        return {"status": "stopped", "exchange": exchange}
    else:
        raise HTTPException(status_code=400, detail=f"Failed to stop {exchange} bot")


@router.get("/{exchange}/status")
async def get_exchange_status(exchange: str = Path(..., regex="^(binance|kucoin)$"), manager: BotManager = Depends(get_bot_manager)):
    """
    Frontend expects: { "running": bool, "pid": int }
    """
    is_running = manager.is_running(exchange)
    return {
        "running": is_running,
        "pid": os.getpid()  # Process ID
    }


@router.get("/{exchange}/balance")
async def get_balance(exchange: str = Path(..., regex="^(binance|kucoin)$"), manager: BotManager = Depends(get_bot_manager)):
    """
    Frontend expects: { "balance": float }
    """
    bot = manager.get_bot(exchange)
    if not bot or not bot.exchange:
        return {"balance": 0.0}
    
    balance = bot.exchange.get_balance()
    return {"balance": balance}


@router.get("/{exchange}/positions")
async def get_positions(exchange: str = Path(..., regex="^(binance|kucoin)$"), manager: BotManager = Depends(get_bot_manager)):
    """
    Frontend expects: [{ "symbol": str, "side": str, "size": float, "entry_price": float, 
                         "current_price": float, "pnl_pct": float, "leverage": int }]
    """
    bot = manager.get_bot(exchange)
    if not bot or not bot.exchange:
        return []
    
    # Refresh mark prices for live P&L updates
    positions = bot.exchange.get_open_positions()
    if hasattr(bot.exchange, 'paper') and positions:
        for pos in positions:
            try:
                current_price = bot.exchange.get_current_price(pos.symbol)
                if current_price > 0:
                    bot.exchange.paper.update_mark_prices({pos.symbol: current_price})
            except Exception as e:
                logger.warning(f"Price refresh failed for {pos.symbol}: {e}")
        
        # Re-fetch with updated prices
        positions = bot.exchange.get_open_positions()
    
    # Convert Position dataclass to dict for JSON serialization
    return [
        {
            "symbol": p.symbol,
            "side": p.side,
            "size": p.size,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "pnl_pct": round(p.pnl_pct, 2),
            "leverage": p.leverage
        }
        for p in positions
    ]


@router.post("/{exchange}/positions/{symbol}/close")
async def close_position(
    exchange: str = Path(..., regex="^(binance|kucoin)$"),
    symbol: str = Path(...),
    manager: BotManager = Depends(get_bot_manager)
):
    """
    Close specific position by symbol
    """
    bot = manager.get_bot(exchange)
    if not bot or not bot.exchange:
        raise HTTPException(status_code=404, detail=f"Bot not found for {exchange}")
    
    # Get current price for close
    try:
        current_price = bot.exchange.get_current_price(symbol)
        if hasattr(bot.exchange, 'paper'):
            result = bot.exchange.paper.close_position(symbol, current_price)
        else:
            bot.exchange.close_position(symbol)
            result = {"status": "closed"}
        
        return result
    except Exception as e:
        logger.error(f"Failed to close {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
