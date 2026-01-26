
import time
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
import pytz

from app.core.config import settings, Timeframe
from app.exchanges.abstract import ExchangeProvider
from app.exchanges.kucoin_adapter import KuCoinAdapter
from app.exchanges.binance_adapter import BinanceAdapter
from app.models.Model_Sentinel.decision_engine import DecisionEngine
from app.engine.price_features import PriceFeatures

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SentinelBot")
IST = pytz.timezone('Asia/Kolkata')

class TradingBot:
    """
    Sentinel 3.0 Execution Engine
    Orchestrates Data -> Decision -> Execution
    """
    

    def __init__(self, exchange: ExchangeProvider, exchange_name: str):
        self.running = False
        self.exchange = exchange
        self.exchange_name = exchange_name
        self.decision_engine = DecisionEngine()
        
        # Feedback
        from app.models.Model_Sentinel.feedback_collector import FeedbackCollector
        self.feedback = FeedbackCollector()
        
        # State
        self.cooldowns = {} # {symbol: expiry_timestamp}
        self.pos_state = {} # {symbol: PositionState} - Metadata for active positions
        self.scan_symbols = settings.trading.symbols
        self.active_symbol = settings.trading.symbols[0] if settings.trading.symbols else "XBTUSDTM"
        
        logger.info(f"🤖 {exchange_name.upper()} Bot initialized")



    async def start(self):
        """Start the trading loop (async)"""
        if self.running:
            return
        self.running = True
        logger.info(f"🤖 {self.exchange_name.upper()} Bot Started! Polling markets...")
        await self._loop()

    def stop(self):
        """Stop the trading loop"""
        self.running = False
        logger.info("🛑 Bot Stopping...")

    async def _loop(self):
        """Main polling loop (async)"""
        last_refresh = 0
        refresh_interval = 300 # 5 minutes
        
        while self.running:
            try:
                # 0. Dynamic Watchlist Refresh
                if settings.trading.use_dynamic_watchlist and (time.time() - last_refresh > refresh_interval):
                    try:
                        top_coins = self.exchange.get_top_symbols(limit=settings.trading.watchlist_limit)
                        if top_coins:
                            new_symbols = [c['symbol'] for c in top_coins]
                            
                            # Keep user defined symbols if any? For now overwrite or merge?
                            # Strategy: Merge with hardcoded favorites or just replace
                            # Let's simple Replace
                            self.scan_symbols = new_symbols
                            
                            logger.info(f"🔄 Watchlist Updated ({len(new_symbols)}): {', '.join(new_symbols)}")
                            last_refresh = time.time()
                    except Exception as e:
                        logger.error(f"Watchlist refresh failed: {e}")
                
                # 1. Manage Active Positions
                self._manage_positions()
                
                # 2. Scan for Entry across all configured symbols
                open_positions = self.exchange.get_open_positions()
                open_symbols = {p.symbol for p in open_positions}
                
                # Scan each symbol if we don't already have a position on it
                for symbol in self.scan_symbols:
                    if symbol not in open_symbols:
                        self._check_entry(symbol)
                
                # Sleep (async)
                await asyncio.sleep(1) # 1s Polling
                
            except Exception as e:
                logger.error(f"[{self.exchange_name}] Loop Error: {e}")
                await asyncio.sleep(5)


    def _close(self, symbol: str, reason: str, pnl_pct: float):
        """Helper to close and record feedback"""
        self.exchange.close_position(symbol)
        
        # Record Feedback
        trade_id = self.pos_state.get(symbol, {}).get('trade_id')
        if trade_id:
             price = self.exchange.get_current_price(symbol)
             self.feedback.record_exit(trade_id, price, pnl_pct)
        
        self._set_cooldown(symbol, 300 if pnl_pct > 0 else 1800, reason)
        del self.pos_state[symbol]

    def _manage_positions(self):
        """Check open positions for Exit Signals (SL, TP, Reversal)"""
        positions = self.exchange.get_open_positions()
        
        # --- LIVE PRICE REFRESH ---
        # Update mark prices for all open positions (critical for P&L calculation)
        for pos in positions:
            try:
                current_price = self.exchange.get_current_price(pos.symbol)
                if current_price > 0:
                    # Update via paper engine's method
                    if hasattr(self.exchange, 'paper'):
                        self.exchange.paper.update_mark_prices({pos.symbol: current_price})
            except Exception as e:
                logger.warning(f"Price refresh failed for {pos.symbol}: {e}")
        
        # Re-fetch positions with updated prices
        positions = self.exchange.get_open_positions()
        
        # Sync state: Remove closed positions
        active_symbols = {p.symbol for p in positions}
        for s in list(self.pos_state.keys()):
            if s not in active_symbols:
                # Close detected externally?
                del self.pos_state[s]
                
        for pos in positions:
            symbol = pos.symbol
            pnl_pct = pos.pnl_pct
            
            # Initialize state if new
            if symbol not in self.pos_state:
                self.pos_state[symbol] = {'peak_pnl': pnl_pct, 'entry_time': time.time(), 'is_breakeven': False}
            
            state = self.pos_state[symbol]
            
            # Update Peak PnL
            if pnl_pct > state['peak_pnl']:
                state['peak_pnl'] = pnl_pct
            
            duration_candles = (time.time() - state['entry_time']) / 60
            
            # --- EXIT LOGIC ---
            
            # 1. Fixed Stop Loss
            sl_pct = settings.trading.stop_loss_pct * pos.leverage
            if state['is_breakeven']:
                sl_pct = -0.05 
                
            if pnl_pct <= -sl_pct:
                logger.info(f"🚨 SL TRIGGERED for {symbol}: {pnl_pct:.2f}%")
                self._close(symbol, "SL Hit", pnl_pct)
                continue

            # 2. Smart Trail (Breakeven Trigger)
            if pnl_pct > 0.15 and not state['is_breakeven']:
                state['is_breakeven'] = True
                logger.info(f"🔒 PROTECT: Profit > 0.15%, moving SL to Breakeven for {symbol}")

            # 3. Time Stop
            if duration_candles >= settings.trading.max_hold_candles and pnl_pct < 0.1:
                logger.info(f"⏱️ Time Stop: Held {duration_candles:.1f}m with low profit.")
                self._close(symbol, "Time Stop", pnl_pct)
                continue
            
            # 4. Reversal Check
            should_close, reason = self._check_reversal(symbol, pos.side)
            if should_close:
                logger.info(f"🔄 Reversal Detected: {reason}")
                self._close(symbol, "Reversal", pnl_pct)
                continue
            
            # 5. Let Winners Run (Dynamic Trail)
            target = settings.trading.take_profit_pct * pos.leverage
            if pnl_pct >= target:
                trail_dist = 0.3 * pos.leverage 
                if (state['peak_pnl'] - pnl_pct) >= trail_dist:
                     logger.info(f"💰 Trailing Stop Hit: Peak {state['peak_pnl']:.2f}%, Current {pnl_pct:.2f}%")
                     self._close(symbol, "Win (Trail)", pnl_pct)
                     continue


    def _check_entry(self, symbol: str):
        """Analyze market for entry"""
        # Logging for user visibility (Requested)
        logger.info(f"🔍 Scanning {symbol}...")

        # Check Cooldown
        if self._is_cooldown(symbol):
            return

        # Check if already in position
        positions = self.exchange.get_open_positions()
        if any(p.symbol == symbol for p in positions):
            return

        # Get Data
        df = self.exchange.get_market_structure(symbol, settings.trading.primary_timeframe)
        if df.empty or len(df) < 50:
            logger.warning(f"⚠️ Insufficient data for {symbol}: {len(df)} candles")
            return

        # Compute Features
        pf = PriceFeatures(df)
        df = pf.compute_all()

        # Analyze
        decision = self.decision_engine.analyze(df)
        
        # DEBUG: Log decision details
        logger.info(f"   📊 {symbol}: Dir={decision.direction_bias}, Qual={decision.setup_quality:.2f}, Rec={decision.recommendation}")
        
        if decision.recommendation in ['ALLOW_TRADE', 'MICRO_TRADE']:
            # Execute Entry
            side = decision.direction_bias
            if side not in ['LONG', 'SHORT']:
                return
                
            # Position Sizing Logic
            # ---------------------
            balance = self.exchange.get_balance()
            price = self.exchange.get_current_price(symbol)
            leverage = settings.risk.max_leverage or 5
            
            if price <= 0:
                logger.error(f"Invalid price for {symbol}: {price}")
                return
                
            # Use 95% of available balance (Full Port for MVP testing)
            # Or use Risk Config if implemented. For now, max usage.
            margin_amt = balance * 0.95
            
            if margin_amt < 5: # Min $5 margin
                logger.warning(f"Balance too low ($ {balance:.2f}) to open trade.")
                return

            notional_value = margin_amt * leverage
            qty = notional_value / price
            
            # Rounding (Safety)
            qty = round(qty, 6)
            
            logger.info(f"🚀 ENTRY SIGNAL: {symbol} {side} ({decision.reasoning})")
            logger.info(f"   📐 Sizing: ${margin_amt:.2f} Margin x {leverage}x = {qty:.6f} {symbol}")
            
            # Delegate execution to adapter (handles both Live and Paper)
            res = self.exchange.place_order(symbol, side, 'market', qty, leverage)
            
            if res:
                # Record Entry for Learning
                # We need features JSON. 'df' has features? No, 'df' from PriceFeatures compute_all has them.
                # Re-extract features from last row of df
                latest_features = df.iloc[-1].to_dict()
                
                # Trade ID: We need a unique ID. 
                # For paper, we can generate one.
                trade_id = f"TRADE-{int(time.time())}"
                
                self.feedback.record_entry(
                     trade_id=trade_id,
                     symbol=symbol,
                     side=side,
                     entry_price=self.exchange.get_current_price(symbol),
                     features=latest_features,
                     mode=settings.mode.value
                )
                
                # Store trade_id in position state so we can reference it on exit
                # Include all required fields to avoid KeyError
                if symbol not in self.pos_state:
                     self.pos_state[symbol] = {
                         'peak_pnl': 0.0,
                         'entry_time': time.time(),
                         'is_breakeven': False
                     }
                self.pos_state[symbol]['trade_id'] = trade_id

    def _check_reversal(self, symbol: str, current_side: str) -> (bool, str):
        """Check for reversal signals"""
        df = self.exchange.get_market_structure(symbol, settings.trading.primary_timeframe.value)
        if df.empty:
            return False, ""
        pf = PriceFeatures(df)
        df = pf.compute_all()
        return self.decision_engine.check_reversal(df, current_side)

    def _set_cooldown(self, symbol: str, seconds: int, reason: str):
        self.cooldowns[symbol] = time.time() + seconds
        logger.info(f"❄️ Cooldown {symbol}: {reason} ({seconds}s)")

    def reset_paper_state(self):
        """Reset paper trading state for all bots"""
        for exchange, bot in self.bots.items():
            if hasattr(bot.exchange, 'paper'):
                bot.exchange.paper.reset()
                bot.pos_state = {}
                self.cooldowns = {} # Clear all cooldowns for the manager
        # The original instruction had `return False` here, but it's usually `None` or `True` for success.
        # Assuming it should return None or True if successful.
        # For now, keeping it as is, but noting the potential inconsistency.
        # If the intent was to indicate if *any* bot was reset, it would need more logic.
        # As it iterates through all bots, it implies a full reset, so `return True` or no return is more common.
        # Sticking to the provided code for now.
        return False

    def _is_cooldown(self, symbol: str) -> bool:
        if symbol in self.cooldowns:
            if time.time() < self.cooldowns[symbol]:
                return True
            else:
                del self.cooldowns[symbol]
        return False
