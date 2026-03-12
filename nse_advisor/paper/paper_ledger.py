"""
Paper Trade Ledger.

Logs signals as paper trades and tracks virtual P&L.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Literal

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.paper.slippage_model import SlippageModel
from nse_advisor.recommender.engine import TradeRecommendation

logger = logging.getLogger(__name__)


@dataclass
class PaperTradeLeg:
    """A leg in a paper trade."""
    tradingsymbol: str
    strike: float
    option_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    quantity_lots: int
    lot_size: int
    entry_price: float
    current_price: float
    exit_price: float | None = None
    
    # Greeks at entry
    entry_delta: float = 0.0
    entry_theta: float = 0.0
    entry_vega: float = 0.0
    
    @property
    def total_quantity(self) -> int:
        """Total quantity in units."""
        return self.quantity_lots * self.lot_size
    
    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L."""
        if self.action == "BUY":
            return (self.current_price - self.entry_price) * self.total_quantity
        else:
            return (self.entry_price - self.current_price) * self.total_quantity
    
    @property
    def realized_pnl(self) -> float | None:
        """Calculate realized P&L if closed."""
        if self.exit_price is None:
            return None
        if self.action == "BUY":
            return (self.exit_price - self.entry_price) * self.total_quantity
        else:
            return (self.entry_price - self.exit_price) * self.total_quantity


@dataclass
class PaperTrade:
    """A paper trade."""
    trade_id: str
    recommendation_id: str
    underlying: str
    strategy_name: str
    expiry: date
    entry_time: datetime
    legs: list[PaperTradeLeg]
    
    # Entry conditions
    composite_score: float
    regime: str
    
    # P&L limits
    max_profit: float
    max_loss: float
    stop_loss: float
    take_profit: float
    
    # State
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    exit_time: datetime | None = None
    exit_reason: str = ""
    
    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized P&L."""
        return sum(leg.unrealized_pnl for leg in self.legs)
    
    @property
    def realized_pnl(self) -> float | None:
        """Total realized P&L if closed."""
        if self.status != "CLOSED":
            return None
        return sum(leg.realized_pnl or 0 for leg in self.legs)


class PaperLedger:
    """
    Paper trade ledger.
    
    Features:
    - Auto-logs all recommendations as paper trades
    - Tracks virtual cash, open positions, P&L
    - Applies slippage simulation
    - Daily MTM tracking
    """
    
    def __init__(self) -> None:
        """Initialize paper ledger."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        self._slippage = SlippageModel()
        
        # State
        self._capital = self._settings.paper_capital
        self._open_trades: dict[str, PaperTrade] = {}
        self._closed_trades: list[PaperTrade] = []
        self._daily_pnl: dict[date, float] = {}
    
    @property
    def available_capital(self) -> float:
        """Capital available for new trades."""
        margin_used = sum(
            trade.max_loss
            for trade in self._open_trades.values()
        )
        return self._capital - margin_used
    
    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized P&L across all open trades."""
        return sum(
            trade.unrealized_pnl
            for trade in self._open_trades.values()
        )
    
    @property
    def realized_pnl(self) -> float:
        """Total realized P&L from closed trades."""
        return sum(
            trade.realized_pnl or 0
            for trade in self._closed_trades
        )
    
    @property
    def total_pnl(self) -> float:
        """Total P&L (realized + unrealized)."""
        return self.realized_pnl + self.unrealized_pnl
    
    def log_recommendation(
        self,
        recommendation: TradeRecommendation,
        lot_size: int = 25,
    ) -> PaperTrade:
        """
        Log a recommendation as a paper trade.
        
        Args:
            recommendation: Trade recommendation
            lot_size: Lot size for underlying
            
        Returns:
            Created paper trade
        """
        now = datetime.now(self._ist)
        
        # Build legs with slippage
        legs = []
        for rec_leg in recommendation.legs:
            # Apply slippage
            slipped_price = self._slippage.apply_slippage(
                ltp=rec_leg.suggested_entry_price,
                action=rec_leg.action,
                quantity_lots=rec_leg.suggested_lots,
            )
            
            legs.append(PaperTradeLeg(
                tradingsymbol=rec_leg.tradingsymbol,
                strike=rec_leg.strike,
                option_type=rec_leg.option_type,
                action=rec_leg.action,
                quantity_lots=rec_leg.suggested_lots,
                lot_size=lot_size,
                entry_price=slipped_price,
                current_price=slipped_price,
                entry_delta=rec_leg.delta,
                entry_theta=rec_leg.theta,
                entry_vega=rec_leg.vega,
            ))
        
        trade = PaperTrade(
            trade_id=str(uuid.uuid4()),
            recommendation_id=recommendation.recommendation_id,
            underlying=recommendation.underlying,
            strategy_name=recommendation.strategy_name,
            expiry=recommendation.legs[0].expiry if recommendation.legs else date.today(),
            entry_time=now,
            legs=legs,
            composite_score=recommendation.composite_score,
            regime=recommendation.regime,
            max_profit=recommendation.max_profit_inr,
            max_loss=recommendation.max_loss_inr,
            stop_loss=recommendation.suggested_stop_loss_inr,
            take_profit=recommendation.suggested_take_profit_inr,
        )
        
        self._open_trades[trade.trade_id] = trade
        
        logger.info(
            f"Paper trade logged: {trade.trade_id} "
            f"{trade.strategy_name} on {trade.underlying}"
        )
        
        return trade
    
    def update_prices(self, price_map: dict[str, float]) -> None:
        """
        Update current prices for all open positions.
        
        Args:
            price_map: Map of tradingsymbol -> current LTP
        """
        for trade in self._open_trades.values():
            for leg in trade.legs:
                if leg.tradingsymbol in price_map:
                    leg.current_price = price_map[leg.tradingsymbol]
    
    def close_trade(
        self,
        trade_id: str,
        exit_prices: dict[str, float] | None = None,
        reason: str = "Manual close",
    ) -> PaperTrade | None:
        """
        Close a paper trade.
        
        Args:
            trade_id: ID of trade to close
            exit_prices: Map of symbol -> exit price (uses current if None)
            reason: Reason for closing
            
        Returns:
            Closed trade or None if not found
        """
        trade = self._open_trades.pop(trade_id, None)
        if not trade:
            return None
        
        now = datetime.now(self._ist)
        trade.status = "CLOSED"
        trade.exit_time = now
        trade.exit_reason = reason
        
        # Set exit prices
        for leg in trade.legs:
            if exit_prices and leg.tradingsymbol in exit_prices:
                leg.exit_price = exit_prices[leg.tradingsymbol]
            else:
                # Apply slippage on exit
                leg.exit_price = self._slippage.apply_slippage(
                    ltp=leg.current_price,
                    action="SELL" if leg.action == "BUY" else "BUY",
                    quantity_lots=leg.quantity_lots,
                )
        
        # Add to closed trades
        self._closed_trades.append(trade)
        
        # Apply transaction costs
        realized = trade.realized_pnl or 0
        costs = self._slippage.calculate_transaction_costs(trade.legs)
        final_pnl = realized - costs
        
        # Update daily P&L
        today = date.today()
        self._daily_pnl[today] = self._daily_pnl.get(today, 0) + final_pnl
        
        logger.info(
            f"Paper trade closed: {trade_id} "
            f"P&L: ₹{final_pnl:.0f} ({reason})"
        )
        
        return trade
    
    def get_open_trades(self) -> list[PaperTrade]:
        """Get all open trades."""
        return list(self._open_trades.values())
    
    def get_closed_trades(self) -> list[PaperTrade]:
        """Get all closed trades."""
        return self._closed_trades.copy()
    
    def get_daily_pnl_history(self) -> dict[date, float]:
        """Get daily P&L history."""
        return self._daily_pnl.copy()
    
    def get_performance_summary(self) -> dict:
        """Get performance summary."""
        total_trades = len(self._closed_trades)
        winning_trades = sum(
            1 for t in self._closed_trades
            if (t.realized_pnl or 0) > 0
        )
        
        return {
            "total_capital": self._capital,
            "available_capital": self.available_capital,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "total_pnl": self.total_pnl,
            "open_trades": len(self._open_trades),
            "closed_trades": total_trades,
            "win_rate": winning_trades / total_trades if total_trades > 0 else 0,
            "pnl_pct": (self.total_pnl / self._capital) * 100,
        }


# Global instance
_paper_ledger: PaperLedger | None = None


def get_paper_ledger() -> PaperLedger:
    """Get or create global paper ledger."""
    global _paper_ledger
    if _paper_ledger is None:
        _paper_ledger = PaperLedger()
    return _paper_ledger
