import logging
import json
import os
from typing import Dict, List, Optional
from app.exchanges.abstract import Position
from app.core.config import settings

logger = logging.getLogger("PaperEngine")

class PaperTradingEngine:
    """
    Simulates a Futures Exchange.
    Tracks persistent balance, positions, and PnL.
    Each exchange has its own separate state file.
    """
    
    def __init__(self, exchange_name: str = "default", initial_balance: float = 100.0):
        self.exchange_name = exchange_name
        self.balance = initial_balance
        self.positions: Dict[str, Position] = {} # symbol -> Position
        self.trade_history: List[Dict] = []  # Trade history
        self.fee_rate = 0.0006 # Approx futures taker fee (0.06%)
        
        # Separate state file per exchange
        self.data_file = f"data/{exchange_name}/state.json"
        
        # Load persisted state
        self._load_state()
        
        logger.info(f"📝 {exchange_name.upper()} Paper Engine Initialized | Balance: ${self.balance:.2f}")

    def _load_state(self):
        """Load balance and positions from JSON file"""
        if not os.path.exists(self.data_file):
            # Create directory if needed
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
            return
            
        try:
            with open(self.data_file, "r") as f:
                data = json.load(f)
                
            self.balance = data.get("balance", self.balance)
            
            # Reconstruct positions
            saved_positions = data.get("positions", {})
            for sym, p_data in saved_positions.items():
                self.positions[sym] = Position(
                    symbol=p_data['symbol'],
                    side=p_data['side'],
                    size=p_data['size'],
                    entry_price=p_data['entry_price'],
                    current_price=p_data['current_price'],
                    unrealized_pnl=p_data['unrealized_pnl'],
                    pnl_pct=p_data['pnl_pct'],
                    leverage=p_data['leverage'],
                    raw=p_data.get('raw', {'paper': True})
                )
            # Load trade history
            self.trade_history = data.get("trade_history", [])
            
            logger.info(f"📝 Loaded Paper State: ${self.balance:.2f} | {len(self.positions)} Pos | {len(self.trade_history)} Trades")
        except Exception as e:
            logger.error(f"Failed to load paper state: {e}")

    def reset(self):
        """Hard reset paper account"""
        self.balance = 10000.0
        self.positions = {}
        self.trade_history = []
        self._save_state()
        logger.info(f"♻️ {self.exchange_name.upper()} Paper State RESET to $10,000")

    def _save_state(self):
        """Save balance and positions to JSON file"""
        try:
            # Serialize positions
            pos_data = {}
            for sym, p in self.positions.items():
                pos_data[sym] = {
                    "symbol": p.symbol,
                    "side": p.side,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "pnl_pct": p.pnl_pct,
                    "leverage": p.leverage,
                    "raw": p.raw
                }
            
            import time
            data = {
                "balance": self.balance,
                "positions": pos_data,
                "trade_history": self.trade_history,
                "last_updated": int(time.time())
            }
            
            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=4)
                
        except Exception as e:
            logger.error(f"Failed to save paper state: {e}")

    def get_balance(self) -> float:
        return self.balance

    def get_positions(self) -> List[Position]:
        return list(self.positions.values())

    def place_order(self, symbol: str, side: str, qty: float, leverage: int, price: float):
        """
        Simulate opening a position.
        qty: Size in contracts (or base asset amount depending on logic). 
        For simplicity in this MVP, we treat 'qty' as 'contracts' or 'units of asset'.
        """
        if symbol in self.positions:
            logger.warning(f"Paper: Position already exists for {symbol}. Averaging not implemented.")
            return

        # Calculate Margin
        # Value = Price * Qty
        trade_value = price * qty
        margin_required = trade_value / leverage
        fee = trade_value * self.fee_rate

        total_cost = margin_required + fee

        # Check Balance
        if total_cost > self.balance:
            logger.warning(f"Paper: Insufficient funds. Req: ${total_cost:.2f}, Avail: ${self.balance:.2f}")
            return None

        # Deduct Balance (Margin + Fee)
        self.balance -= (margin_required + fee)
        
        # Create Position
        pos = Position(
            symbol=symbol,
            side=side,
            size=qty,
            entry_price=price,
            current_price=price,
            unrealized_pnl=0.0,
            pnl_pct=0.0,
            leverage=leverage,
            raw={'paper': True}
        )
        self.positions[symbol] = pos
        logger.info(f"📝 Paper Order Filled: {side} {symbol} x{qty} @ ${price:.2f} (Fee: ${fee:.4f})")
        self._save_state() # SAVE STATE
        return {'status': 'filled', 'price': price, 'qty': qty, 'fee': fee}

    def close_position(self, symbol: str, price: float):
        """
        Simulate closing a position.
        Calculates PnL and credits wallet.
        """
        if symbol not in self.positions:
            return None
            
        pos = self.positions[symbol]
        
        # Calculate PnL
        # Long: (Exit - Entry) * Size
        # Short: (Entry - Exit) * Size
        price_diff = (price - pos.entry_price) if pos.side == 'LONG' else (pos.entry_price - price)
        pnl = price_diff * pos.size
        
        # Calculate ROE %
        # Margin = (Entry * Size) / Leverage
        margin = (pos.entry_price * pos.size) / pos.leverage
        roe_pct = (pnl / margin) * 100 if margin > 0 else 0
        
        # Fees
        exit_value = price * pos.size
        fee = exit_value * self.fee_rate
        
        final_pnl = pnl - fee
        
        # Re-calc initial margin to return it
        initial_margin = (pos.entry_price * pos.size) / pos.leverage
        
        # Update Balance: Return Margin + PnL - Exit Fee
        self.balance += (initial_margin + final_pnl)
        
        logger.info(f"📝 Paper Position Closed: {symbol} | PnL: ${final_pnl:.2f} ({roe_pct:.2f}%) | New Bal: ${self.balance:.2f}")
        
        # Record trade in history
        import time as time_module
        self.trade_history.append({
            "symbol": symbol,
            "side": pos.side,
            "size": pos.size,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "pnl": round(final_pnl, 4),
            "roe_pct": round(roe_pct, 2),
            "leverage": pos.leverage,
            "timestamp": int(time_module.time() * 1000)
        })
        
        del self.positions[symbol]
        self._save_state() # SAVE STATE
        return {'status': 'closed', 'pnl': final_pnl, 'roe': roe_pct, 'fee': fee}

    def update_mark_prices(self, prices: Dict[str, float]):
        """Update unrealized PnL for open positions"""
        for symbol, pos in self.positions.items():
            if symbol in prices:
                current = prices[symbol]
                pos.current_price = current
                
                # Calc PnL
                diff = (current - pos.entry_price) if pos.side == 'LONG' else (pos.entry_price - current)
                pos.unrealized_pnl = diff * pos.size
                
                margin = (pos.entry_price * pos.size) / pos.leverage
                pos.pnl_pct = (pos.unrealized_pnl / margin) * 100 if margin > 0 else 0
        
        # NOTE: Removed _save_state() here - state is saved on trade open/close only
        # Continuous saving on every tick was causing issues with resets
