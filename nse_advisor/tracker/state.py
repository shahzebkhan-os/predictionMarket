"""
Trade State Models.

Dataclasses for ManualTrade and TradeLeg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Literal

from zoneinfo import ZoneInfo


@dataclass
class TradeLeg:
    """A leg in a tracked trade."""
    tradingsymbol: str
    underlying: str
    strike: float
    expiry: date
    option_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    quantity_lots: int
    lot_size: int
    entry_price: float
    current_price: float
    
    # Entry Greeks (buyer perspective, SELL legs should multiply by -1)
    entry_iv: float = 0.0
    entry_delta: float = 0.0
    entry_gamma: float = 0.0
    entry_theta: float = 0.0
    entry_vega: float = 0.0
    
    # Current Greeks
    current_delta: float = 0.0
    current_gamma: float = 0.0
    current_theta: float = 0.0
    current_vega: float = 0.0
    
    # Exit
    exit_price: float | None = None
    exit_time: datetime | None = None
    
    @property
    def total_quantity(self) -> int:
        """Total quantity in units."""
        return self.quantity_lots * self.lot_size
    
    @property
    def greeks_multiplier(self) -> int:
        """Multiplier for Greeks (1 for BUY, -1 for SELL)."""
        return 1 if self.action == "BUY" else -1
    
    @property
    def adjusted_delta(self) -> float:
        """Delta adjusted for position direction."""
        return self.current_delta * self.greeks_multiplier * self.total_quantity
    
    @property
    def adjusted_gamma(self) -> float:
        """Gamma adjusted for position direction."""
        return self.current_gamma * self.greeks_multiplier * self.total_quantity
    
    @property
    def adjusted_theta(self) -> float:
        """Theta adjusted for position direction."""
        return self.current_theta * self.greeks_multiplier * self.total_quantity
    
    @property
    def adjusted_vega(self) -> float:
        """Vega adjusted for position direction."""
        return self.current_vega * self.greeks_multiplier * self.total_quantity
    
    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L for this leg."""
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
    
    @property
    def is_closed(self) -> bool:
        """Check if leg is closed."""
        return self.exit_price is not None


@dataclass
class ManualTrade:
    """A manually tracked trade (actual or paper)."""
    trade_id: str
    strategy_name: str
    underlying: str
    expiry: date
    entry_time: datetime
    legs: list[TradeLeg]
    
    # Link to recommendation (if from signal)
    linked_recommendation_id: str | None = None
    
    # Entry conditions
    regime_at_entry: str = ""
    signal_scores_at_entry: dict = field(default_factory=dict)
    dte_at_entry: int = 0
    
    # P&L limits
    max_profit: float = 0.0
    max_loss: float = 0.0
    stop_loss_inr: float = 0.0
    take_profit_inr: float = 0.0
    
    # State
    status: Literal["PAPER", "LIVE", "CLOSED"] = "PAPER"
    paper_mode: bool = True
    
    # Exit info
    exit_time: datetime | None = None
    exit_reason: str = ""
    
    @property
    def is_open(self) -> bool:
        """Check if trade is open."""
        return self.status != "CLOSED"
    
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
    
    @property
    def net_delta(self) -> float:
        """Net portfolio delta."""
        return sum(leg.adjusted_delta for leg in self.legs)
    
    @property
    def net_gamma(self) -> float:
        """Net portfolio gamma."""
        return sum(leg.adjusted_gamma for leg in self.legs)
    
    @property
    def net_theta(self) -> float:
        """Net portfolio theta."""
        return sum(leg.adjusted_theta for leg in self.legs)
    
    @property
    def net_vega(self) -> float:
        """Net portfolio vega."""
        return sum(leg.adjusted_vega for leg in self.legs)
    
    @property
    def dte(self) -> int:
        """Current days to expiry."""
        return (self.expiry - date.today()).days
    
    def pnl_percentage(self) -> float:
        """P&L as percentage of max loss."""
        if self.max_loss > 0:
            return (self.unrealized_pnl / self.max_loss) * 100
        return 0.0
    
    def profit_percentage(self) -> float:
        """Profit as percentage of max profit."""
        if self.max_profit > 0:
            return (self.unrealized_pnl / self.max_profit) * 100
        return 0.0
