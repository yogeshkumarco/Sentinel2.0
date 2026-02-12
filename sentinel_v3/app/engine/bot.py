
import time
import logging
import asyncio
import json
import os
from typing import Dict, List, Optional
from datetime import datetime
import pytz

from app.core.config import settings, Timeframe, LEVERAGE_TIERS
from app.exchanges.abstract import ExchangeProvider
from app.exchanges.kucoin_adapter import KuCoinAdapter
from app.exchanges.binance_adapter import BinanceAdapter
from app.models.Model_Sentinel.decision_engine import DecisionEngine
from app.engine.price_features import PriceFeatures
from app.engine.run_mode_detector import RunModeDetector
from app.engine.pattern_engine import PatternEngine
from app.engine.chop_detector import ChopDetector

# Logging Setup: Force FileHandler to work (uvicorn hijack bypass)
logger = logging.getLogger("SentinelBot")
logger.setLevel(logging.INFO)

# Clear any existing handlers (prevent duplicates on reload)
if logger.hasHandlers():
    logger.handlers.clear()

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s'))
logger.addHandler(console_handler)

# File Handler (CRITICAL: Force flush with autoflush)
file_handler = logging.FileHandler('bot.log', mode='a', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s'))
logger.addHandler(file_handler)

IST = pytz.timezone('Asia/Kolkata')

# Brain Persistence File Path
STATE_FILE = os.path.join(os.path.dirname(__file__), 'bot_memory.json')

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
        self.pending_setups = {} # {symbol: PendingSetup} - Setups waiting for zone trigger
        self.scan_symbols = settings.trading.symbols
        self.active_symbol = settings.trading.symbols[0] if settings.trading.symbols else "XBTUSDTM"
        
        # ============================================
        # RUN_MODE State (Isolated from Daily Active)
        # ============================================
        self.run_mode_detector = RunModeDetector()
        self.current_mode = "DAILY_ACTIVE"  # "DAILY_ACTIVE" or "RUN_MODE"
        self.run_mode_direction = "NONE"  # "BULL", "BEAR", or "NONE"
        self.run_mode_state = {
            'entry_count': 0,  # 0, 1, 2, or 3 (scaling entries)
            'total_size': 0.0,  # Total position size built
            'avg_entry': 0.0,  # Weighted average entry price
            'tp1_hit': False,
            'tp2_hit': False,
            'activation_time': 0
        }
        
        # ============================================
        # PATTERN EXECUTION LAYER (VETO Power)
        # ============================================
        self.pattern_engine = PatternEngine()
        
        # ============================================
        # ADAPTIVE TIMEFRAME (Chop Detection)
        # ============================================
        self.chop_detector = ChopDetector()
        self.current_market_mode = None  # Cache for logging
        
        # Brain Persistence: Load previous state if exists
        self._load_state()
        
        logger.info(f"🤖 {exchange_name.upper()} Bot initialized")
        logger.info(f"🛡️ ENTRY DISCIPLINE 6.0: ACTIVE (Conditional Zones + Structural SL)")
        logger.info(f"📐 PATTERN LAYER: ACTIVE (Swing-based execution with Proof-of-Move)")
        if settings.risk.enable_run_mode:
            logger.info(f"🚀 RUN_MODE: ENABLED (Bull/Bear Run Exploitation available)")

    def _save_state(self):
        """Save position state to disk for persistence across restarts"""
        try:
            # Only save pos_state (cooldowns can restart fresh)
            data = {
                'pos_state': self.pos_state,
                'exchange': self.exchange_name,
                'saved_at': time.time()
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    def _load_state(self):
        """Load position state from disk if available"""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                
                # Only restore if same exchange
                if data.get('exchange') == self.exchange_name:
                    self.pos_state = data.get('pos_state', {})
                    saved_at = data.get('saved_at', 0)
                    age_minutes = (time.time() - saved_at) / 60
                    logger.info(f"🧠 Brain Loaded! Restored {len(self.pos_state)} position states (Age: {age_minutes:.1f}m)")
                    
                    # Log peak PnL for each restored position
                    for symbol, state in self.pos_state.items():
                        logger.info(f"   📍 {symbol}: Peak PnL = {state.get('peak_pnl', 0):.2f}%")
                else:
                    logger.info("🧠 State file exists but for different exchange. Starting fresh.")
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            self.pos_state = {}



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
        loop_count = 0  # Heartbeat counter
        
        last_scan_time = 0
        scan_interval = 60 # Check entries every 60s
        
        while self.running:
            loop_count += 1
            current_time = time.time()
            
            # Heartbeat every 10 cycles (~10 seconds)
            if loop_count % 10 == 0:
                logger.info(f"💓 Loop alive | Cycle {loop_count} | Watching {len(self.scan_symbols)} symbols")
            try:
                # 1. ALWAYS Manage Active Positions (FAST: Every 1s)
                # This ensures Trailing Stops and SL are triggered instantly
                self._manage_positions()
                
                # 2. Entry Scan & Watchlist Update (SLOW: Every 60s)
                if current_time - last_scan_time > scan_interval:
                    
                    # 2a. Watchlist Refresh
                    if settings.trading.use_dynamic_watchlist and (current_time - last_refresh > refresh_interval):
                        try:
                            top_coins = self.exchange.get_top_symbols(limit=settings.trading.watchlist_limit)
                            if top_coins:
                                new_symbols = [c['symbol'] for c in top_coins]
                                self.scan_symbols = new_symbols
                                logger.info(f"🔄 Watchlist Updated ({len(new_symbols)}): {', '.join(new_symbols)}")
                                last_refresh = current_time
                        except Exception as e:
                            logger.error(f"Watchlist refresh failed: {e}")

                    # 2b. Scan for Entries
                    open_positions = self.exchange.get_open_positions()
                    open_symbols = {p.symbol for p in open_positions}
                    
                    # ============================================
                    # RUN_MODE CHECK (Isolated from Daily Active)
                    # ============================================
                    if settings.risk.enable_run_mode:
                        self._check_run_mode_status()
                    
                    # Route to appropriate entry logic based on mode
                    for symbol in self.scan_symbols:
                        if symbol not in open_symbols:
                            if self.current_mode == "RUN_MODE":
                                self._check_entry_run_mode(symbol)
                            else:
                                self._check_entry(symbol)  # Daily Active (unchanged)
                    
                    # 2c. Process Pending Setups (Conditional Entries)
                    self._process_pending_setups(open_symbols)
                            
                    last_scan_time = current_time
                
                # Sleep (Fast Loop)
                await asyncio.sleep(1) 
                
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

    def _process_pending_setups(self, open_symbols: set):
        """
        ENTRY DISCIPLINE 6.0: Process Pending Setups
        Check if price has entered zone and candle confirms.
        Execute or expire setups.
        """
        if not self.pending_setups:
            return
        
        current_time = time.time()
        expired_symbols = []
        
        for symbol, setup in list(self.pending_setups.items()):
            # Skip if already have a position
            if symbol in open_symbols:
                expired_symbols.append(symbol)
                continue
            
            # Check expiration
            if current_time > setup['expires_at']:
                logger.info(f"⏰ SETUP_EXPIRED | {symbol} {setup['side']} | Zone never reached")
                expired_symbols.append(symbol)
                continue
            
            # Get current price data
            try:
                df = self.exchange.get_market_structure(symbol, settings.trading.primary_timeframe)
                if df.empty or len(df) < 5:
                    continue
                
                # Use closed candles only
                df = df.iloc[:-1]
                latest = df.iloc[-1]
                current_price = latest['close']
                
                # Check if price is in zone
                price_in_zone = setup['entry_zone_low'] <= current_price <= setup['entry_zone_high']
                
                if not price_in_zone:
                    continue  # Still waiting
                
                # Check candle confirmation WITH WICK REQUIREMENT
                candle_range = latest['high'] - latest['low']
                
                if candle_range > 0:
                    lower_wick = min(latest['open'], latest['close']) - latest['low']
                    upper_wick = latest['high'] - max(latest['open'], latest['close'])
                    lower_wick_pct = lower_wick / candle_range
                    upper_wick_pct = upper_wick / candle_range
                else:
                    lower_wick_pct = 0
                    upper_wick_pct = 0
                
                if setup['side'] == 'LONG':
                    is_bullish = latest['close'] > latest['open']
                    wick_confirms = lower_wick_pct >= 0.30
                    candle_confirms = is_bullish and wick_confirms
                else:
                    is_bearish = latest['close'] < latest['open']
                    wick_confirms = upper_wick_pct >= 0.30
                    candle_confirms = is_bearish and wick_confirms
                
                if not candle_confirms:
                    logger.info(f"⏳ ZONE_TOUCHED_NO_CONFIRM | {symbol} | Wick: {lower_wick_pct*100:.1f}%L / {upper_wick_pct*100:.1f}%U | Need 30%")
                    continue
                
                # ============================================
                # EXECUTE CONDITIONAL ENTRY
                # ============================================
                logger.info(f"🎯 PENDING_TRIGGER | {symbol} {setup['side']} | Confirmed at {current_price:.6f}")
                
                balance = self.exchange.get_balance()
                price = self.exchange.get_current_price(symbol)
                
                # Dynamic Leverage based on Symbol Tier
                # Prioritize Tier (e.g. PEPE=3x) over global max
                tier_leverage = LEVERAGE_TIERS.get(symbol, 5) # Default 5x for unknowns
                config_max = settings.risk.max_leverage or 10
                leverage = min(tier_leverage, config_max)
                
                if price <= 0 or balance < 5:
                    continue
                    
                margin_amt = balance * 0.95
                notional_value = margin_amt * leverage
                qty = round(notional_value / price, 6)
                
                logger.info(f"🚀 ENTRY_EXECUTE | {symbol} {setup['side']} | SL={setup['stop_loss']:.6f} TP={setup['take_profit']:.6f}")
                
                res = self.exchange.place_order(symbol, setup['side'], 'market', qty, leverage)
                
                if res:
                    trade_id = f"TRADE-{int(time.time())}"
                    
                    self.feedback.record_entry(
                         trade_id=trade_id,
                         symbol=symbol,
                         side=setup['side'],
                         entry_price=price,
                         features=latest.to_dict(),
                         mode=settings.mode.value
                    )
                    
                    self.pos_state[symbol] = {
                        'peak_pnl': 0.0,
                        'entry_time': time.time(),
                        'is_breakeven': False,
                        'trade_id': trade_id,
                        'structural_sl': setup['stop_loss'],
                        'structural_tp': setup['take_profit']
                    }
                    
                    expired_symbols.append(symbol)
                    
            except Exception as e:
                logger.warning(f"Pending setup check failed for {symbol}: {e}")
        
        # Clean up expired/executed setups
        for symbol in expired_symbols:
            if symbol in self.pending_setups:
                del self.pending_setups[symbol]

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
                self._save_state()  # Persist to disk immediately
            
            trade_age_minutes = (time.time() - state['entry_time']) / 60
            
            # ============================================
            # RUN_MODE EXIT ROUTING (Isolated)
            # ============================================
            if state.get('is_run_mode') and self.current_mode == "RUN_MODE":
                self._manage_positions_run_mode(symbol, pos)
                continue  # Skip Daily Active exit logic
            
            # ============================================
            # 3-MODE EXIT SYSTEM (Candle-Close Only)
            # Daily Active Mode (unchanged)
            # ============================================
            
            # 1️⃣ HARD STOP (ALWAYS ACTIVE - Tick Based)
            # This is the only tick-based exit for catastrophic protection
            if trade_age_minutes < 15:
                sl_pct = 25.0
            else:
                sl_pct = 15.0
                
            if pnl_pct <= -sl_pct:
                logger.info(f"🚨 HARD_STOP: {symbol} PnL={pnl_pct:.2f}% hit SL ({-sl_pct}%)")
                self._close(symbol, "Hard Stop", pnl_pct)
                continue
            
            # Get market data for momentum check
            try:
                df = self.exchange.get_market_structure(symbol, settings.trading.primary_timeframe)
                if df.empty or len(df) < 10:
                    continue  # Can't evaluate without data
                
                # Use CLOSED candles only for exit decisions
                df = df.iloc[:-1]
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest
                
                # Calculate momentum indicators
                avg_volume = df['volume'].iloc[-10:].mean()
                current_volume = latest['volume']
                current_body = abs(latest['close'] - latest['open'])
                prev_body = abs(prev['close'] - prev['open'])
                
                # 2️⃣ MOMENTUM OVERRIDE MODE
                # If market is in strong impulse, DISABLE all profit exits
                momentum_override = False
                
                # Condition 1: Volume expansion (1.3x average)
                volume_expansion = current_volume >= (avg_volume * 1.3)
                
                # Condition 2: Body expansion (current > previous)
                body_expansion = current_body > prev_body
                
                # Condition 3: Price direction matches our side
                if pos.side.upper() == 'SHORT':
                    direction_match = latest['close'] < latest['open']  # Bearish candle
                else:
                    direction_match = latest['close'] > latest['open']  # Bullish candle
                
                # Momentum Override activates if 2+ conditions met
                if sum([volume_expansion, body_expansion, direction_match]) >= 2:
                    momentum_override = True
                    logger.info(f"� MOMENTUM_OVERRIDE_ACTIVE | {symbol} | Vol:{volume_expansion} Body:{body_expansion} Dir:{direction_match}")
                
                if momentum_override:
                    # Don't exit during momentum - let it run
                    logger.info(f"⏳ EXIT_BLOCKED_DUE_TO_MOMENTUM | {symbol} | Holding through impulse")
                    continue
                
                # ============================================
                # LAYER 1: FEE GATE (Block ALL exits below +1.8% net)
                # This is FEE ARMOR - prevents bleeding profit to fees
                # ============================================
                if pnl_pct < 1.8 and pnl_pct > -15.0:  # Between -15% (hard SL territory) and +1.8%
                    # Block all non-catastrophic exits
                    continue  # Silent continue - don't log spam
                
                # ============================================
                # LAYER 2: RUNNER MODE (Arm at +3.0% Gross, Trail 1.2% on Candle Close)
                # ============================================
                runner_armed = state['peak_pnl'] >= 3.0
                
                if runner_armed:
                    # Track if armed (for logging)
                    if not state.get('runner_logged'):
                        logger.info(f"🏃 RUNNER_ARMED | {symbol} | Peak: {state['peak_pnl']:.2f}%")
                        state['runner_logged'] = True
                    
                    # Trailing Stop: Exit if dropped 1.2% from peak (evaluated on candle close)
                    trailing_level = state['peak_pnl'] - 1.2
                    if pnl_pct <= trailing_level:
                        logger.info(f"🏃 RUNNER_EXIT | {symbol} | Peak:{state['peak_pnl']:.2f}% -> PnL:{pnl_pct:.2f}% | Trail hit")
                        self._close(symbol, "Runner Trail (1%)", pnl_pct)
                        continue
                    
                    # Check for reversal candle (close against direction)
                    if pos.side.upper() == 'SHORT' and latest['close'] > latest['open']:
                        logger.info(f"🔄 EXIT_EVAL_ON_CANDLE_CLOSE | REVERSAL_CANDLE | {symbol} bullish close while SHORT")
                        self._close(symbol, "Reversal Candle", pnl_pct)
                        continue
                    elif pos.side.upper() == 'LONG' and latest['close'] < latest['open']:
                        logger.info(f"🔄 EXIT_EVAL_ON_CANDLE_CLOSE | REVERSAL_CANDLE | {symbol} bearish close while LONG")
                        self._close(symbol, "Reversal Candle", pnl_pct)
                        continue
                
                # 4️⃣ PROTECTION MODE (Default - Candle Close Exits Only)
                else:
                    # Dead Trade Check (45 mins, PnL < 0.5%)
                    if trade_age_minutes >= 45 and pnl_pct < 0.5:
                        logger.info(f"� DEAD_TRADE | {symbol} | Age:{trade_age_minutes:.1f}m PnL:{pnl_pct:.2f}%")
                        self._close(symbol, "Dead Trade", pnl_pct)
                        continue
                    
                    # Reversal Check (only if losing or near breakeven)
                    if pnl_pct < 0.5:
                        should_close, reason = self._check_reversal(symbol, pos.side)
                        if should_close:
                            logger.info(f"🔄 EXIT_EVAL_ON_CANDLE_CLOSE | REVERSAL | {symbol} | {reason}")
                            self._close(symbol, "Reversal", pnl_pct)
                            continue
                            
            except Exception as e:
                logger.warning(f"Exit logic error for {symbol}: {e}")

    # ============================================
    # CHANGE 1: TRADE TYPE CLASSIFICATION METHOD
    # Determines if trade is CONTINUATION, REACTION, or RANGE
    # ============================================
    def _classify_trade_type(self, df, direction_bias: str, high_impulse: bool = False) -> str:
        """
        Classify trade type based on market structure.
        
        Types:
        - CONTINUATION: Trend following (pullbacks, flags)
        - REACTION: Counter-trend (V-reversals, failed breakdowns)
        - RANGE: Mean reversion (support/resistance bounces)
        
        Returns:
            str: "CONTINUATION", "REACTION", or "RANGE"
        """
        try:
            if len(df) < 20:
                return "CONTINUATION"  # Default
            
            # Calculate structure metrics
            recent_high = df['high'].iloc[-20:].max()
            recent_low = df['low'].iloc[-20:].min()
            current_price = df['close'].iloc[-1]
            range_size = recent_high - recent_low
            
            if range_size == 0:
                return "CONTINUATION"
            
            # Position in range (0 = at low, 1 = at high)
            position_in_range = (current_price - recent_low) / range_size
            
            # Check for trending structure (higher highs or lower lows)
            highs = df['high'].iloc[-10:]
            lows = df['low'].iloc[-10:]
            
            higher_highs = sum(highs.iloc[i] > highs.iloc[i-1] for i in range(1, len(highs)))
            lower_lows = sum(lows.iloc[i] < lows.iloc[i-1] for i in range(1, len(lows)))
            
            trending_up = higher_highs >= 6
            trending_down = lower_lows >= 6
            
            # ATR check for volatility context
            avg_range = (df['high'] - df['low']).iloc[-10:].mean()
            current_range = df['high'].iloc[-1] - df['low'].iloc[-1]
            vol_expansion = current_range > avg_range * 1.5
            
            # Classification Logic:
            
            # 1. HIGH_IMPULSE + Reversal candle = REACTION
            if high_impulse:
                # Check for rejection wick (potential reversal)
                latest = df.iloc[-1]
                candle_range = latest['high'] - latest['low']
                if candle_range > 0:
                    if direction_bias == "LONG":
                        lower_wick = min(latest['open'], latest['close']) - latest['low']
                        wick_ratio = lower_wick / candle_range
                        if wick_ratio > 0.40:  # Strong lower wick rejection
                            return "REACTION"
                    elif direction_bias == "SHORT":
                        upper_wick = latest['high'] - max(latest['open'], latest['close'])
                        wick_ratio = upper_wick / candle_range
                        if wick_ratio > 0.40:  # Strong upper wick rejection
                            return "REACTION"
            
            # 2. Trending structure + pullback = CONTINUATION
            if direction_bias == "LONG" and trending_up:
                # Pullback: price is in lower half of range but trend is up
                if position_in_range < 0.5:
                    return "CONTINUATION"
            elif direction_bias == "SHORT" and trending_down:
                # Pullback: price is in upper half of range but trend is down
                if position_in_range > 0.5:
                    return "CONTINUATION"
            
            # 3. Price at extremes of range = RANGE trade
            if position_in_range < 0.25 or position_in_range > 0.75:
                # At support/resistance in a non-trending market
                if not trending_up and not trending_down:
                    return "RANGE"
            
            # 4. Default to CONTINUATION for unclear cases
            return "CONTINUATION"
            
        except Exception as e:
            logger.warning(f"Trade type classification error: {e}")
            return "CONTINUATION"  # Safe default

    # ============================================
    # CHANGE 6: CONFIRMATION & DISPLACEMENT CANDLE CHECKS
    # Two-stage entry: Confirm -> Arm, Displacement -> Trigger
    # ============================================
    
    def _check_confirmation_candle(self, df, direction: str) -> bool:
        """
        Check for confirmation candle (Stage 1: ARMS the trade).
        
        LONG: Bullish close above prior close
        SHORT: Bearish close below prior close
        """
        if len(df) < 2:
            return False
        
        latest = df.iloc[-1]
        prior = df.iloc[-2]
        
        if direction == "LONG":
            # Bullish confirmation: close > open AND close > prior close
            return latest['close'] > latest['open'] and latest['close'] > prior['close']
        else:
            # Bearish confirmation: close < open AND close < prior close
            return latest['close'] < latest['open'] and latest['close'] < prior['close']
    
    def _check_displacement_candle(self, df, direction: str) -> bool:
        """
        Check for displacement candle (Stage 2: TRIGGERS entry).
        
        Displacement = Strong directional move (body > 1.2x average)
        """
        if len(df) < 10:
            return False
        
        latest = df.iloc[-1]
        body_size = abs(latest['close'] - latest['open'])
        
        # Calculate average body size over last 10 candles
        bodies = abs(df['close'].iloc[-10:] - df['open'].iloc[-10:])
        avg_body = bodies.mean()
        
        # Displacement requires body > 1.2x average
        is_displacement = body_size > (avg_body * 1.2)
        
        if direction == "LONG":
            correct_direction = latest['close'] > latest['open']
        else:
            correct_direction = latest['close'] < latest['open']
        
        return is_displacement and correct_direction


    def _check_entry(self, symbol: str):
        """Analyze market for entry"""
        # Logging for user visibility (Requested)
        logger.info(f"🔍 Scanning {symbol}...")

        # Check Cooldown
        if self._is_cooldown(symbol):
            # logger.info(f"⏳ Cooldown active for {symbol}")
            return

        # Check if already in position
        positions = self.exchange.get_open_positions()
        if any(p.symbol == symbol for p in positions):
            return

        # Get 15m Data (always needed for chop detection)
        df_15m = self.exchange.get_market_structure(symbol, "15m")
        if df_15m is None or df_15m.empty or len(df_15m) < 50:
            logger.warning(f"⚠️ Insufficient data for {symbol}: {len(df_15m) if df_15m is not None else 0} candles")
            return

        # ============================================
        # ADAPTIVE TIMEFRAME: Detect market mode
        # ============================================
        market_mode = self.chop_detector.detect(df_15m)
        
        # Log mode changes (only when mode changes)
        if self.current_market_mode != market_mode.mode:
            logger.info(f"📊 MARKET_MODE | {market_mode.mode} | ATR_Ratio={market_mode.atr_ratio:.2f} | ADX={market_mode.adx:.1f} | BB_Width={market_mode.bb_width_pct:.1f}%")
            self.current_market_mode = market_mode.mode
        
        # Select timeframe based on market mode
        if market_mode.mode == "CHOPPY":
            # Fetch 5m data for entries in choppy markets
            df = self.exchange.get_market_structure(symbol, "5m")
            if df is None or df.empty or len(df) < 50:
                df = df_15m  # Fallback to 15m
        else:
            df = df_15m

        if df.empty or len(df) < 50:
            logger.warning(f"⚠️ Insufficient data for {symbol}")
            return

        # ============================================
        # CRITICAL: Use CLOSED CANDLES ONLY
        # The last candle in the cache is still forming (live).
        # Patterns detected on live candles can disappear on close.
        # Drop the last row to ensure we analyze only confirmed data.
        # ============================================
        df = df.iloc[:-1]  # Remove live candle
        
        if len(df) < 50:
            return  # Not enough closed candles

        # Compute Features
        pf = PriceFeatures(df)
        df = pf.compute_all()

        # ============================================
        # IMPULSE + EXHAUSTION GUARD (Critical Entry Filter)
        # ============================================
        if len(df) >= 15:
            # Detection Window
            impulse_window = df.iloc[-15:-5]  # Candles 15 to 5 bars ago (the move)
            compression_window = df.iloc[-5:]  # Last 5 candles (current state)
            
            # STEP 1: Was there a recent impulse?
            impulse_high = impulse_window['high'].max()
            impulse_low = impulse_window['low'].min()
            impulse_move_pct = (impulse_high - impulse_low) / impulse_low * 100
            had_impulse = impulse_move_pct >= 4.0  # 4% move in 10 candles
            
            if had_impulse:
                # STEP 2: Are we now in POST-IMPULSE EXHAUSTION?
                # Condition 1: Bodies are shrinking (compression)
                impulse_avg_body = (impulse_window['high'] - impulse_window['low']).mean()
                compression_avg_body = (compression_window['high'] - compression_window['low']).mean()
                body_shrinking = compression_avg_body < (impulse_avg_body * 0.5)
                
                # Condition 2: Volume is declining
                impulse_avg_vol = impulse_window['volume'].mean()
                compression_avg_vol = compression_window['volume'].mean()
                volume_declining = compression_avg_vol < (impulse_avg_vol * 0.7)
                
                # Condition 3: Price is near the extreme (floor/ceiling)
                current_price = df['close'].iloc[-1]
                near_floor = current_price < (impulse_low + (impulse_move_pct * 0.20 / 100 * impulse_low))
                near_ceiling = current_price > (impulse_high - (impulse_move_pct * 0.20 / 100 * impulse_high))
                near_extreme = near_floor or near_ceiling
                
                # Condition 4: No fresh structure break (range is narrowing)
                compression_range = compression_window['high'].max() - compression_window['low'].min()
                impulse_range = impulse_high - impulse_low
                range_narrowing = compression_range < (impulse_range * 0.3)
                
                # EXHAUSTION = 3+ conditions met
                exhaustion_score = sum([body_shrinking, volume_declining, near_extreme, range_narrowing])
                
                if exhaustion_score >= 3:
                    logger.info(f"🚫 ENTRY_BLOCKED | LATE_ENTRY_EXHAUSTION | {symbol} | Impulse={impulse_move_pct:.1f}% | Body:{body_shrinking} Vol:{volume_declining} Extreme:{near_extreme} RangeNarrow:{range_narrowing}")
                    return
                
            # ============================================
            # CHANGE 2: HIGH_IMPULSE TAG (Not a hard block)
            # Instead of blocking, tag asset as REACTION candidate
            # ============================================
            recent_high = df['high'].iloc[-10:].max()
            recent_low = df['low'].iloc[-10:].min()
            recent_move_pct = (recent_high - recent_low) / recent_low * 100
            high_impulse = recent_move_pct > 6.0
            
            if high_impulse:
                logger.info(f"⚡ HIGH_IMPULSE_TAG | {symbol} moved {recent_move_pct:.1f}% | REACTION candidate only")
                # Continue with trade_type = REACTION (set below)
        
        # ============================================
        # PATTERN-BASED OVERRIDE (For REACTION Trades)
        # Run pattern engine EARLY for HIGH_IMPULSE coins
        # REACTION patterns can override NO_TRADE
        # ============================================
        pattern_override = False
        early_pattern = None
        if 'high_impulse' in dir() and high_impulse:
            early_pattern = self.pattern_engine.analyze(
                df=df,
                direction_bias=None,  # Let pattern engine decide direction
                htf_trend=None,
                run_mode=False
            )
            
            # REACTION patterns can override NO_TRADE
            if early_pattern.family == "REACTION" and early_pattern.score >= 0.50:
                pattern_override = True
                logger.info(f"⚡ PATTERN_OVERRIDE | {symbol} | {early_pattern.variant} (Score={early_pattern.score:.2f}) found")

        # ============================================
        # NO-TRADE ZONE FILTER (Mid-Range Chop Protection)
        # Prevent entries in the middle of consolidation ranges
        # Only allow trades at Support/Resistance edges
        # ============================================
        if len(df) >= 20:
            # Detect recent Support (lowest low in last 20 bars)
            support = df['low'].iloc[-20:].min()
            
            # Detect recent Resistance (highest high in last 20 bars)
            resistance = df['high'].iloc[-20:].max()
            
            # Define Range
            range_size = resistance - support
            current_price = df['close'].iloc[-1]
            
            # Only apply if range is meaningful (> 0.5% of price)
            if range_size > (support * 0.005):
                # Define No-Trade Zone (Middle 10% of range - Relaxed from 30%)
                no_trade_low = support + (range_size * 0.45)
                no_trade_high = resistance - (range_size * 0.45)
                
                # Check if price is inside No-Trade Zone
                if no_trade_low < current_price < no_trade_high:
                    logger.info(f"🚫 ENTRY_BLOCKED | NO_TRADE_ZONE | {symbol} price={current_price:.4f} inside range [{no_trade_low:.4f} - {no_trade_high:.4f}]")
                    return


        # Analyze
        decision = self.decision_engine.analyze(df)
        
        # ============================================
        # PATTERN-BASED DECISION OVERRIDE
        # REACTION patterns can override NO_TRADE
        # ============================================
        if pattern_override and decision.recommendation == 'NO_TRADE':
            # Determine direction from pattern and candle
            override_direction = "LONG" if df['close'].iloc[-1] > df['open'].iloc[-1] else "SHORT"
            decision.recommendation = 'MICRO_TRADE'  # Use smaller size for safety
            decision.direction_bias = override_direction
            decision.confidence = early_pattern.score  # Use pattern score as confidence
            decision.entry_zone_low = early_pattern.sl_zone[0] if early_pattern.sl_zone else df['low'].iloc[-1]
            decision.entry_zone_high = df['close'].iloc[-1]
            decision.stop_loss = early_pattern.sl_zone[1] if early_pattern.sl_zone else df['low'].iloc[-3:].min()
            decision.take_profit = early_pattern.tp_zone[0] if early_pattern.tp_zone else df['close'].iloc[-1] * 1.02
            logger.info(f"🔄 DECISION_OVERRIDE | {symbol} | NO_TRADE -> MICRO_TRADE | Dir={override_direction} | Pattern={early_pattern.variant}")
        
        # ============================================
        # CHANGE 1: TRADE TYPE CLASSIFICATION
        # Classify trade type EARLY in the pipeline
        # Types: CONTINUATION, REACTION, RANGE
        # ============================================
        trade_type = self._classify_trade_type(df, decision.direction_bias, high_impulse if 'high_impulse' in dir() else False)
        logger.info(f"📊 TRADE_TYPE | {symbol} | {trade_type}")
        
        # ============================================
        # CHANGE 3: CONDITIONAL CONFIDENCE THRESHOLDS
        # Different thresholds per trade type & market mode
        # ============================================
        if self.current_market_mode == "CHOPPY":
            # Loose thresholds for scalping in chop
            confidence_thresholds = {
                "CONTINUATION": 0.45,  # Much lower for 5m scalps
                "REACTION": 0.40,
                "RANGE": 0.40
            }
        else:
            # Strict thresholds for trending moves
            confidence_thresholds = {
                "CONTINUATION": 0.60,
                "REACTION": 0.52,
                "RANGE": 0.55
            }
        
        min_confidence = confidence_thresholds.get(trade_type, 0.50)
        
        if decision.recommendation in ['ALLOW_TRADE', 'MICRO_TRADE'] and decision.confidence < min_confidence:
            logger.info(f"🚫 LOW_CONFIDENCE | {symbol} | {trade_type} requires {min_confidence}, got {decision.confidence:.2f}")
            return
        
        # ============================================
        # CHANGE 4: HTF TREND FILTER (Type-Aware)
        # Strict enforcement ONLY for CONTINUATION trades
        # REACTION trades bypass HTF veto
        # ============================================
        if decision.recommendation in ['ALLOW_TRADE', 'MICRO_TRADE']:
             try:
                 df_h1 = self.exchange.get_market_structure(symbol, "1h")
                 if not df_h1.empty and len(df_h1) > 60:
                     # Use closed candles only for trend check
                     df_h1 = df_h1.iloc[:-1]
                     # Quick EMA 55 calculation
                     ema_55 = df_h1['close'].ewm(span=55, adjust=False).mean().iloc[-1]
                     price_h1 = df_h1['close'].iloc[-1]
                     
                     h1_bullish = price_h1 > ema_55
                     
                     if trade_type == "CONTINUATION":
                         # STRICT HTF enforcement for CONTINUATION trades only
                         if decision.direction_bias == "SHORT" and h1_bullish:
                             logger.info(f"🛑 HTF MISMATCH: {symbol} CONTINUATION SHORT vs Bull Trend. BLOCKED.")
                             return
                         if decision.direction_bias == "LONG" and not h1_bullish:
                             logger.info(f"🛑 HTF MISMATCH: {symbol} CONTINUATION LONG vs Bear Trend. BLOCKED.")
                             return
                         logger.info(f"✅ HTF CONFIRMED: H1 Trend aligns with {decision.direction_bias}")
                     elif trade_type == "REACTION":
                         # HTF veto IGNORED for REACTION trades
                         logger.info(f"⚡ HTF BYPASS | {symbol} | REACTION trade, HTF veto ignored")
                     else:
                         # RANGE trades: soft warning but allow
                         if (decision.direction_bias == "SHORT" and h1_bullish) or \
                            (decision.direction_bias == "LONG" and not h1_bullish):
                             logger.info(f"⚠️ HTF SOFT_WARN | {symbol} | RANGE trade against HTF, proceeding with caution")
             except Exception as e:
                 logger.warning(f"HTF Check failed for {symbol}: {e}. Proceeding with caution.")
        
        # DEBUG: Log decision details
        if decision.recommendation != "NO_TRADE":
            logger.info(f"🚨 TRADE POTENTIAL: {symbol} | Rec: {decision.recommendation} | Type: {trade_type}")
            logger.info(f"   Reason: {decision.reasoning}")
            logger.info(f"   Stats: Qual={decision.setup_quality:.2f} | Conf={decision.confidence:.2f} | Dir={decision.direction_bias}")
        else:
             logger.info(f"   📊 {symbol}: Rec={decision.recommendation} (Qual={decision.setup_quality:.2f})")
        
        if decision.recommendation in ['ALLOW_TRADE', 'MICRO_TRADE']:
            side = decision.direction_bias
            if side not in ['LONG', 'SHORT']:
                return
            
            # ============================================
            # ENTRY DISCIPLINE 6.0: Conditional Zones
            # Signal != Entry. Store setup. Wait for zone.
            # ============================================
            
            # Check if zone is valid (non-zero)
            if decision.entry_zone_low <= 0 or decision.entry_zone_high <= 0:
                logger.info(f"🚫 ENTRY_BLOCKED | NO_VALID_ZONE | {symbol} Zone: [{decision.entry_zone_low:.6f} - {decision.entry_zone_high:.6f}]")
                return
            
            # ============================================
            # PATTERN EXECUTION LAYER (VETO POWER)
            # Sits between Permission and Entry.
            # Can block trades if pattern incomplete or no proof.
            # ============================================
            pattern = self.pattern_engine.analyze(
                df=df,
                direction_bias=side,
                htf_trend=None,  # HTF already validated above
                run_mode=(self.current_mode == "RUN_MODE")
            )
            
            # Log pattern detection with score (for training and debugging)
            if pattern.variant != "NONE":
                logger.info(f"📐 PATTERN | {symbol} | {pattern.family}.{pattern.variant} | Score={pattern.score:.2f} | Complete={pattern.is_complete} | Proof={pattern.proof_of_move}")
            
            # ============================================
            # CHANGE 5: SCORING REPLACES VETO
            # Pattern score affects POSITION SIZE, not permission
            # Score tiers: 0.7+ = 100%, 0.5-0.7 = 70%, 0.3-0.5 = 50%, <0.3 = 30%
            # ============================================
            position_size_multiplier = 1.0
            if pattern.score >= 0.70:
                position_size_multiplier = 1.0
                logger.info(f"💪 FULL_SIZE | {symbol} | Pattern score {pattern.score:.2f} >= 0.70")
            elif pattern.score >= 0.50:
                position_size_multiplier = 0.70
                logger.info(f"📉 REDUCED_SIZE | {symbol} | Pattern score {pattern.score:.2f} -> 70% position")
            elif pattern.score >= 0.30:
                position_size_multiplier = 0.50
                logger.info(f"📉 SMALL_SIZE | {symbol} | Pattern score {pattern.score:.2f} -> 50% position")
            else:
                position_size_multiplier = 0.30
                logger.info(f"📉 MIN_SIZE | {symbol} | Pattern score {pattern.score:.2f} -> 30% position")
            
            # ============================================
            # CHANGE 6: TIMING FIX (Confirm -> Arm, Displacement -> Trigger)
            # Confirmation candle = ARMS the trade
            # Displacement candle = TRIGGERS entry
            # ============================================
            
            # Check for confirmation candle (first stage)
            confirmation = self._check_confirmation_candle(df, side)
            displacement = self._check_displacement_candle(df, side)
            
            # If already armed, check for displacement trigger
            is_armed = self.pending_setups.get(symbol, {}).get('armed', False)
            
            if not is_armed and confirmation:
                # ARM the trade - store in pending with armed flag
                logger.info(f"🔫 TRADE_ARMED | {symbol} {side} | Waiting for displacement trigger")
                # Continue to store setup below (will be armed)
                
            elif is_armed and displacement:
                # TRIGGER the entry
                logger.info(f"🚀 DISPLACEMENT_TRIGGER | {symbol} | Executing entry")
                # Continue to execution below
                
            elif is_armed and not displacement:
                # Still waiting for displacement
                logger.info(f"⏳ ARMED_WAITING | {symbol} | Trade armed, waiting for displacement")
                return  # Wait for next cycle
            
            # Override decision's SL/TP with pattern levels (always use pattern levels)
            if pattern.stop_loss > 0 and pattern.take_profit > 0:
                logger.info(f"✅ PATTERN_LEVELS | {symbol} | {pattern.variant} | SL={pattern.stop_loss:.6f} TP={pattern.take_profit:.6f}")
                decision.stop_loss = pattern.stop_loss
                decision.take_profit = pattern.take_profit
            
            # Check if SL is valid (non-zero and reasonable distance)
            current_price = df['close'].iloc[-1]
            if side == 'LONG':
                sl_distance_pct = abs(current_price - decision.stop_loss) / current_price * 100
            else:
                sl_distance_pct = abs(decision.stop_loss - current_price) / current_price * 100
            
            # Structure-based SL: No fixed % limit (removed 1.2% cap)
            # Just validate SL exists
            if decision.stop_loss <= 0:
                logger.info(f"🚫 ENTRY_BLOCKED | NO_VALID_SL | {symbol}")
                return
            
            # Check Risk:Reward
            # Dynamic R:R based on Market Mode & Pattern Quality
            min_rr = 1.5
            
            # Allow lower R:R for CHOPPY mode OR High-Quality Institutional Patterns
            if self.current_market_mode == "CHOPPY" or pattern.family == "INSTITUTIONAL":
                min_rr = 1.0  # Lower R:R for scalping/institutional setups
            
            entry_mid = (decision.entry_zone_low + decision.entry_zone_high) / 2
            
            # ADJUST ENTRY MID IF PATTERN SUGGESTS ENTRY
            if pattern.is_complete and pattern.entry_price > 0:
                 # Use pattern entry as the new reference for R:R
                 entry_mid = pattern.entry_price
            
            if side == 'LONG':
                risk = entry_mid - decision.stop_loss
                reward = decision.take_profit - entry_mid
            else:
                risk = decision.stop_loss - entry_mid
                reward = entry_mid - decision.take_profit
            
            if risk <= 0 or reward / risk < min_rr:
                logger.info(f"🚫 ENTRY_BLOCKED | BAD_RR | {symbol} R:R = 1:{reward/risk if risk > 0 else 0:.2f} < 1:{min_rr} ({self.current_market_mode})")
                return
            
            # Check if price is INSIDE the zone right now (immediate entry allowed)
            price_in_zone = decision.entry_zone_low <= current_price <= decision.entry_zone_high
            
            # ============================================
            # CANDLE CONFIRMATION WITH WICK REQUIREMENT
            # LONG: Lower wick >= 30% + bullish close
            # SHORT: Upper wick >= 30% + bearish close
            # ============================================
            latest = df.iloc[-1]
            candle_range = latest['high'] - latest['low']
            
            if candle_range > 0:
                lower_wick = min(latest['open'], latest['close']) - latest['low']
                upper_wick = latest['high'] - max(latest['open'], latest['close'])
                lower_wick_pct = lower_wick / candle_range
                upper_wick_pct = upper_wick / candle_range
            else:
                lower_wick_pct = 0
                upper_wick_pct = 0
            
            if side == 'LONG':
                is_bullish = latest['close'] > latest['open']
                wick_confirms = lower_wick_pct >= 0.30  # 30% lower wick = rejection
                candle_confirms = is_bullish and wick_confirms
            else:
                is_bearish = latest['close'] < latest['open']
                wick_confirms = upper_wick_pct >= 0.30  # 30% upper wick = rejection
                candle_confirms = is_bearish and wick_confirms
            
            if price_in_zone and candle_confirms:
                # ============================================
                # EXECUTE ENTRY (Zone + Confirmation Achieved)
                # ============================================
                logger.info(f"✅ ZONE_CONFIRMED | {symbol} | Price {current_price:.6f} in Zone [{decision.entry_zone_low:.6f} - {decision.entry_zone_high:.6f}]")
                
                balance = self.exchange.get_balance()
                price = self.exchange.get_current_price(symbol)
                leverage = settings.risk.max_leverage or 5
                
                if price <= 0:
                    logger.error(f"Invalid price for {symbol}: {price}")
                    return
                
                # CHANGE 5: Apply pattern score to position sizing
                # position_size_multiplier is set above based on pattern.score
                base_margin = balance * 0.95
                margin_amt = base_margin * position_size_multiplier
                logger.info(f"📊 SIZING | Base: ${base_margin:.2f} x {position_size_multiplier:.0%} = ${margin_amt:.2f}")
                
                if margin_amt < 2:
                    logger.warning(f"Balance too low ($ {balance:.2f}) to open trade.")
                    return

                notional_value = margin_amt * leverage
                qty = round(notional_value / price, 6)
                
                logger.info(f"🚀 ENTRY_EXECUTE | {symbol} {side} | SL={decision.stop_loss:.6f} TP={decision.take_profit:.6f}")
                logger.info(f"   📐 Sizing: ${margin_amt:.2f} x {leverage}x = {qty:.6f} {symbol}")
                
                res = self.exchange.place_order(symbol, side, 'market', qty, leverage)
                
                if res:
                    latest_features = df.iloc[-1].to_dict()
                    trade_id = f"TRADE-{int(time.time())}"
                    
                    self.feedback.record_entry(
                         trade_id=trade_id,
                         symbol=symbol,
                         side=side,
                         entry_price=self.exchange.get_current_price(symbol),
                         features=latest_features,
                         mode=settings.mode.value
                    )
                    
                    if symbol not in self.pos_state:
                         self.pos_state[symbol] = {
                             'peak_pnl': 0.0,
                             'entry_time': time.time(),
                             'is_breakeven': False
                         }
                    self.pos_state[symbol]['trade_id'] = trade_id
                    self.pos_state[symbol]['structural_sl'] = decision.stop_loss
                    self.pos_state[symbol]['structural_tp'] = decision.take_profit
                    
                    # Clear pending setup if exists
                    if symbol in self.pending_setups:
                        del self.pending_setups[symbol]
                        
            else:
                # ============================================
                # STORE PENDING SETUP (Wait for Zone)
                # ============================================
                logger.info(f"⏳ BIAS_DETECTED | {symbol} {side} | Waiting for Zone [{decision.entry_zone_low:.6f} - {decision.entry_zone_high:.6f}]")
                
                self.pending_setups[symbol] = {
                    'side': side,
                    'entry_zone_low': decision.entry_zone_low,
                    'entry_zone_high': decision.entry_zone_high,
                    'stop_loss': decision.stop_loss,
                    'take_profit': decision.take_profit,
                    'created_at': time.time(),
                    'expires_at': time.time() + (3600 * 2),  # Expire after 2 hours
                    'reasoning': decision.reasoning
                }


    def _check_reversal(self, symbol: str, current_side: str) -> (bool, str):
        """Check for reversal signals"""
        df = self.exchange.get_market_structure(symbol, settings.trading.primary_timeframe)
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

    # ============================================
    # RUN_MODE METHODS (Isolated from Daily Active)
    # Bull/Bear Run Exploitation System
    # ============================================
    
    def _check_run_mode_status(self):
        """
        Check if RUN_MODE should activate or deactivate.
        Called every scan cycle. Mode switching is logged.
        """
        try:
            # Get HTF data for detector
            # Use first symbol for market-wide trend check
            primary_symbol = self.scan_symbols[0] if self.scan_symbols else "XBTUSDTM"
            
            df_15m = self.exchange.get_market_structure(primary_symbol, settings.trading.primary_timeframe)
            df_1h = self.exchange.get_market_structure(primary_symbol, "1h")
            
            if df_15m is None or df_15m.empty or df_1h is None or df_1h.empty:
                return
            
            if self.current_mode == "DAILY_ACTIVE":
                # Check for activation
                is_active, direction, metrics = self.run_mode_detector.check_activation(df_15m, df_1h)
                
                if is_active and direction in ["BULL", "BEAR"]:
                    self.current_mode = "RUN_MODE"
                    self.run_mode_direction = direction
                    self.run_mode_state = {
                        'entry_count': 0,
                        'total_size': 0.0,
                        'avg_entry': 0.0,
                        'tp1_hit': False,
                        'tp2_hit': False,
                        'activation_time': time.time()
                    }
                    logger.info(f"🚀 RUN_MODE_ENTERED | {direction} | Conf={metrics.get('confidence', 0)} | ATR={metrics.get('atr_ratio', 0)}")
                    
            elif self.current_mode == "RUN_MODE":
                # Check for exit conditions
                should_exit, reason = self.run_mode_detector.check_exit_conditions(
                    df_15m, df_1h, self.run_mode_direction
                )
                
                if should_exit:
                    self._reset_run_mode(reason)
                    
        except Exception as e:
            logger.warning(f"RUN_MODE status check error: {e}")
    
    def _reset_run_mode(self, reason: str):
        """Reset to DAILY_ACTIVE mode"""
        logger.info(f"🛑 RUN_MODE_EXITED | Reason: {reason}")
        self.current_mode = "DAILY_ACTIVE"
        self.run_mode_direction = "NONE"
        self.run_mode_state = {
            'entry_count': 0,
            'total_size': 0.0,
            'avg_entry': 0.0,
            'tp1_hit': False,
            'tp2_hit': False,
            'activation_time': 0
        }
    
    def _check_entry_run_mode(self, symbol: str):
        """
        RUN_MODE Entry Logic (Isolated)
        - ONLY pullback-continuation entries in trend direction
        - NO wick fades, NO mid-range scalps
        - Position scaling: 40% / 30% / 30%
        """
        # Check cooldown
        if self._is_cooldown(symbol):
            return
        
        # Already at max entries for this run?
        if self.run_mode_state['entry_count'] >= 3:
            return
        
        try:
            df = self.exchange.get_market_structure(symbol, settings.trading.primary_timeframe)
            if df.empty or len(df) < 50:
                return
            
            # Use closed candles
            df = df.iloc[:-1]
            latest = df.iloc[-1]
            current_price = latest['close']
            
            # Calculate EMA for pullback detection
            df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
            ema_20 = df['ema_20'].iloc[-1]
            ema_50 = df['ema_50'].iloc[-1]
            
            # Determine entry type based on entry count
            entry_num = self.run_mode_state['entry_count'] + 1
            valid_entry = False
            entry_reason = ""
            
            if self.run_mode_direction == "BULL":
                # BULL RUN: Look for pullbacks to EMA
                price_above_ema50 = current_price > ema_50
                pullback_to_ema20 = current_price <= ema_20 * 1.005  # Within 0.5% of EMA20
                
                # Bullish candle confirmation
                is_bullish = latest['close'] > latest['open']
                lower_wick = min(latest['open'], latest['close']) - latest['low']
                candle_range = latest['high'] - latest['low']
                wick_rejection = (lower_wick / candle_range >= 0.25) if candle_range > 0 else False
                
                if entry_num == 1:
                    # Entry 1: First pullback to EMA20
                    valid_entry = price_above_ema50 and pullback_to_ema20 and is_bullish and wick_rejection
                    entry_reason = "First pullback to EMA20"
                elif entry_num == 2:
                    # Entry 2: Second pullback (higher low)
                    prev_low = df['low'].iloc[-10:-1].min()
                    higher_low = latest['low'] > prev_low
                    valid_entry = price_above_ema50 and is_bullish and higher_low
                    entry_reason = "Second pullback (higher low)"
                elif entry_num == 3:
                    # Entry 3: Continuation break
                    recent_high = df['high'].iloc[-10:-1].max()
                    break_high = current_price > recent_high
                    valid_entry = break_high and is_bullish
                    entry_reason = "Continuation break"
                    
            elif self.run_mode_direction == "BEAR":
                # BEAR RUN: Look for rallies to EMA
                price_below_ema50 = current_price < ema_50
                rally_to_ema20 = current_price >= ema_20 * 0.995  # Within 0.5% of EMA20
                
                # Bearish candle confirmation
                is_bearish = latest['close'] < latest['open']
                upper_wick = latest['high'] - max(latest['open'], latest['close'])
                candle_range = latest['high'] - latest['low']
                wick_rejection = (upper_wick / candle_range >= 0.25) if candle_range > 0 else False
                
                if entry_num == 1:
                    valid_entry = price_below_ema50 and rally_to_ema20 and is_bearish and wick_rejection
                    entry_reason = "First rally to EMA20"
                elif entry_num == 2:
                    prev_high = df['high'].iloc[-10:-1].max()
                    lower_high = latest['high'] < prev_high
                    valid_entry = price_below_ema50 and is_bearish and lower_high
                    entry_reason = "Second rally (lower high)"
                elif entry_num == 3:
                    recent_low = df['low'].iloc[-10:-1].min()
                    break_low = current_price < recent_low
                    valid_entry = break_low and is_bearish
                    entry_reason = "Continuation break"
            
            if valid_entry:
                self._scale_entry(symbol, entry_num, entry_reason, current_price)
                
        except Exception as e:
            logger.warning(f"RUN_MODE entry check error for {symbol}: {e}")
    
    def _scale_entry(self, symbol: str, entry_num: int, reason: str, price: float):
        """
        Execute scaled entry for RUN_MODE
        Entry 1: 40%, Entry 2: 30%, Entry 3: 30%
        """
        try:
            balance = self.exchange.get_balance()
            if balance < 4:
                return
            
            # Determine size based on entry number
            if entry_num == 1:
                size_pct = 0.40
            else:
                size_pct = 0.30
            
            margin_amt = balance * size_pct * 0.95  # 95% of allocated portion
            leverage = settings.risk.max_leverage
            
            side = "LONG" if self.run_mode_direction == "BULL" else "SHORT"
            
            notional_value = margin_amt * leverage
            qty = round(notional_value / price, 6)
            
            logger.info(f"📈 RUN_MODE_ENTRY_{entry_num} | {symbol} {side} | {reason}")
            logger.info(f"   📐 Size: {size_pct*100:.0f}% (${margin_amt:.2f}) x {leverage}x = {qty:.6f}")
            
            res = self.exchange.place_order(symbol, side, 'market', qty, leverage)
            
            if res:
                # Update RUN_MODE state
                self.run_mode_state['entry_count'] = entry_num
                old_total = self.run_mode_state['total_size']
                new_total = old_total + qty
                
                # Update weighted average entry
                if new_total > 0:
                    old_avg = self.run_mode_state['avg_entry']
                    self.run_mode_state['avg_entry'] = (old_avg * old_total + price * qty) / new_total
                else:
                    self.run_mode_state['avg_entry'] = price
                    
                self.run_mode_state['total_size'] = new_total
                
                # Initialize position state for exit tracking
                if symbol not in self.pos_state:
                    self.pos_state[symbol] = {
                        'peak_pnl': 0,
                        'entry_time': time.time(),
                        'is_run_mode': True,  # Flag for RUN_MODE exit logic
                        'trade_id': f"RUN-{int(time.time())}"
                    }
                
                self._set_cooldown(symbol, settings.risk.loss_cooldown, f"RUN_MODE Entry {entry_num}")
                
        except Exception as e:
            logger.error(f"RUN_MODE scale entry error: {e}")
    
    def _manage_positions_run_mode(self, symbol: str, pos):
        """
        RUN_MODE Exit Logic (Called from _manage_positions)
        - Fee Gate remains active
        - Partial TPs: 25% at TP1, 25% at TP2, 50% runner
        - Structure-based trailing for runner
        """
        pnl_pct = pos.pnl_pct
        state = self.pos_state.get(symbol, {})
        
        # Track peak PnL
        if pnl_pct > state.get('peak_pnl', 0):
            state['peak_pnl'] = pnl_pct
            self.pos_state[symbol] = state
        
        # Fee Gate (UNCHANGED - still active)
        if pnl_pct < 1.8 and pnl_pct > -15.0:
            return  # Block exits in fee zone
        
        # TP1: 25% at +2.5% (local extension)
        if not self.run_mode_state['tp1_hit'] and pnl_pct >= 2.5:
            self._partial_exit(symbol, 0.25, "TP1_LOCAL_EXTENSION", pnl_pct)
            self.run_mode_state['tp1_hit'] = True
            logger.info(f"💰 RUN_MODE_PARTIAL_EXIT | TP1 | {symbol} | {pnl_pct:.2f}%")
            return
        
        # TP2: 25% at +4.0% (HTF target)
        if not self.run_mode_state['tp2_hit'] and pnl_pct >= 4.0:
            self._partial_exit(symbol, 0.25, "TP2_HTF_TARGET", pnl_pct)
            self.run_mode_state['tp2_hit'] = True
            logger.info(f"💰 RUN_MODE_PARTIAL_EXIT | TP2 | {symbol} | {pnl_pct:.2f}%")
            return
        
        # Runner (50%): Structure-based trailing
        if self.run_mode_state['tp2_hit']:
            if not state.get('runner_active'):
                state['runner_active'] = True
                logger.info(f"🏃 RUN_MODE_RUNNER_ACTIVE | {symbol} | Trailing structure")
            
            # Emergency trail at 1.2% from peak (hard protection)
            trailing_level = state['peak_pnl'] - 1.2
            if pnl_pct <= trailing_level:
                logger.info(f"🏃 RUNNER_EXIT | {symbol} | Peak:{state['peak_pnl']:.2f}% -> {pnl_pct:.2f}%")
                self._close(symbol, "RUN_MODE Runner Exit", pnl_pct)
                self._reset_run_mode("RUNNER_CLOSED")
    
    def _partial_exit(self, symbol: str, pct: float, reason: str, pnl_pct: float):
        """
        Close a percentage of position
        """
        try:
            positions = self.exchange.get_open_positions()
            pos = next((p for p in positions if p.symbol == symbol), None)
            
            if pos is None:
                return
            
            close_qty = round(pos.qty * pct, 6)
            
            # Close partial via market order in opposite direction
            close_side = "SHORT" if pos.side.upper() == "LONG" else "LONG"
            
            logger.info(f"   📤 Partial Close: {pct*100:.0f}% ({close_qty}) | {reason}")
            self.exchange.place_order(symbol, close_side, 'market', close_qty, 1)
            
        except Exception as e:
            logger.warning(f"Partial exit error: {e}")
