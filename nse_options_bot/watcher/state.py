"""Trade state dataclasses for the watcher.

OptionsTradeState and TradeLeg represent live trade tracking state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import pytz

IST = pytz.timezone("Asia/Kolkata")


class TradeStatus(str, Enum):
    """Trade status enumeration."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    STOPPED = "STOPPED"


class ExitReason(str, Enum):
    """Exit reason enumeration."""

    TARGET_HIT = "TARGET_HIT"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_STOP = "TIME_STOP"
    EXPIRY_EXIT = "EXPIRY_EXIT"
    MANUAL = "MANUAL"
    KILL_SWITCH = "KILL_SWITCH"
    DELTA_BREACH = "DELTA_BREACH"
    THETA_TARGET = "THETA_TARGET"
    IV_CRUSH = "IV_CRUSH"
    REGIME_CHANGE = "REGIME_CHANGE"
    MAX_LOSS_HIT = "MAX_LOSS_HIT"
    EOD_SQUARE_OFF = "EOD_SQUARE_OFF"


@dataclass
class TradeLegState:
    """State of a single trade leg."""

    leg_id: str
    tradingsymbol: str
    exchange: str = "NFO"

    # Position details
    strike: Decimal = Decimal("0")
    option_type: str = "CE"  # CE or PE
    is_long: bool = True
    quantity: int = 0  # Total quantity
    lot_size: int = 1

    # Prices
    entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    high_price: Decimal = Decimal("0")
    low_price: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")

    # Greeks (tracked in real-time)
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0

    # Order tracking
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    sl_order_id: str | None = None

    # Status
    status: TradeStatus = TradeStatus.PENDING
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    exit_price: Decimal | None = None

    @property
    def lots(self) -> int:
        """Number of lots."""
        return self.quantity // self.lot_size if self.lot_size > 0 else 0

    @property
    def pnl(self) -> Decimal:
        """Current P&L for this leg."""
        if self.status == TradeStatus.PENDING:
            return Decimal("0")

        diff = self.current_price - self.entry_price
        if not self.is_long:
            diff = -diff

        return diff * Decimal(str(self.quantity))

    @property
    def pnl_pct(self) -> float:
        """P&L as percentage of entry value."""
        entry_value = self.entry_price * Decimal(str(self.quantity))
        if entry_value == 0:
            return 0.0
        return float(self.pnl / entry_value * 100)

    def update_price(self, price: Decimal) -> None:
        """Update current price and track high/low.

        Args:
            price: New price
        """
        self.current_price = price
        if price > self.high_price:
            self.high_price = price
        if self.low_price == 0 or price < self.low_price:
            self.low_price = price

    def update_greeks(
        self,
        delta: float,
        gamma: float,
        theta: float,
        vega: float,
        iv: float,
    ) -> None:
        """Update Greeks.

        Args:
            delta: Delta
            gamma: Gamma
            theta: Theta
            vega: Vega
            iv: Implied volatility
        """
        self.delta = delta
        self.gamma = gamma
        self.theta = theta
        self.vega = vega
        self.iv = iv


@dataclass
class OptionsTradeState:
    """Complete state of an options trade (strategy position).

    Tracks all legs, aggregate Greeks, P&L, and exit conditions.
    """

    trade_id: str
    strategy_type: str
    underlying: str
    expiry_date: str

    # Legs
    legs: list[TradeLegState] = field(default_factory=list)

    # Entry info
    entry_time: datetime | None = None
    entry_spot_price: Decimal = Decimal("0")
    entry_signals: dict[str, Any] = field(default_factory=dict)
    entry_regime: str = ""

    # Position sizing
    capital_allocated: Decimal = Decimal("0")
    max_loss_amount: Decimal = Decimal("0")
    target_profit_amount: Decimal = Decimal("0")

    # Live tracking
    current_spot_price: Decimal = Decimal("0")
    last_update: datetime | None = None

    # Aggregate Greeks
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0

    # Theta decay tracking
    theta_collected: Decimal = Decimal("0")
    expected_theta_today: Decimal = Decimal("0")
    actual_theta_today: Decimal = Decimal("0")

    # Exit tracking
    status: TradeStatus = TradeStatus.PENDING
    exit_time: datetime | None = None
    exit_reason: ExitReason | None = None
    exit_notes: str = ""

    # Stop loss tracking
    stop_loss_pct: float = 50.0  # Default 50% of max loss
    trailing_stop_pct: float = 0.0  # 0 = disabled
    peak_profit: Decimal = Decimal("0")
    time_stop_minutes: int = 0  # 0 = disabled

    # Flags
    is_hedged: bool = False
    needs_attention: bool = False
    attention_reason: str = ""

    @property
    def total_pnl(self) -> Decimal:
        """Total P&L across all legs."""
        return sum(leg.pnl for leg in self.legs)

    @property
    def total_pnl_pct(self) -> float:
        """Total P&L as percentage of allocated capital."""
        if self.capital_allocated == 0:
            return 0.0
        return float(self.total_pnl / self.capital_allocated * 100)

    @property
    def is_in_profit(self) -> bool:
        """Check if trade is currently profitable."""
        return self.total_pnl > 0

    @property
    def is_open(self) -> bool:
        """Check if trade is still open."""
        return self.status in (TradeStatus.OPEN, TradeStatus.PARTIAL)

    @property
    def time_in_trade_minutes(self) -> float:
        """Minutes since trade entry."""
        if not self.entry_time:
            return 0.0

        now = datetime.now(IST)
        if self.entry_time.tzinfo is None:
            entry = IST.localize(self.entry_time)
        else:
            entry = self.entry_time

        return (now - entry).total_seconds() / 60

    @property
    def spot_move_pct(self) -> float:
        """Spot price movement since entry."""
        if self.entry_spot_price == 0:
            return 0.0
        return float(
            (self.current_spot_price - self.entry_spot_price)
            / self.entry_spot_price
            * 100
        )

    def update_aggregate_greeks(self) -> None:
        """Update aggregate Greeks from legs."""
        self.net_delta = sum(
            leg.delta * (1 if leg.is_long else -1) * leg.quantity
            for leg in self.legs
        )
        self.net_gamma = sum(
            leg.gamma * (1 if leg.is_long else -1) * leg.quantity
            for leg in self.legs
        )
        self.net_theta = sum(
            leg.theta * (1 if leg.is_long else -1) * leg.quantity
            for leg in self.legs
        )
        self.net_vega = sum(
            leg.vega * (1 if leg.is_long else -1) * leg.quantity
            for leg in self.legs
        )

    def update_peak_profit(self) -> None:
        """Update peak profit for trailing stop."""
        current = self.total_pnl
        if current > self.peak_profit:
            self.peak_profit = current

    def add_leg(self, leg: TradeLegState) -> None:
        """Add a leg to the trade.

        Args:
            leg: Trade leg state
        """
        self.legs.append(leg)

    def get_leg(self, tradingsymbol: str) -> TradeLegState | None:
        """Get leg by trading symbol.

        Args:
            tradingsymbol: Trading symbol

        Returns:
            TradeLegState or None
        """
        for leg in self.legs:
            if leg.tradingsymbol == tradingsymbol:
                return leg
        return None

    def mark_open(self) -> None:
        """Mark trade as open."""
        self.status = TradeStatus.OPEN
        self.entry_time = datetime.now(IST)

    def mark_closed(self, reason: ExitReason, notes: str = "") -> None:
        """Mark trade as closed.

        Args:
            reason: Exit reason
            notes: Additional notes
        """
        self.status = TradeStatus.CLOSED
        self.exit_time = datetime.now(IST)
        self.exit_reason = reason
        self.exit_notes = notes

    def flag_attention(self, reason: str) -> None:
        """Flag trade for attention.

        Args:
            reason: Attention reason
        """
        self.needs_attention = True
        self.attention_reason = reason

    def clear_attention(self) -> None:
        """Clear attention flag."""
        self.needs_attention = False
        self.attention_reason = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Dict representation
        """
        return {
            "trade_id": self.trade_id,
            "strategy_type": self.strategy_type,
            "underlying": self.underlying,
            "expiry_date": self.expiry_date,
            "status": self.status.value,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "total_pnl": float(self.total_pnl),
            "total_pnl_pct": self.total_pnl_pct,
            "net_delta": self.net_delta,
            "net_theta": self.net_theta,
            "net_vega": self.net_vega,
            "spot_move_pct": self.spot_move_pct,
            "time_in_trade_minutes": self.time_in_trade_minutes,
            "num_legs": len(self.legs),
            "needs_attention": self.needs_attention,
            "attention_reason": self.attention_reason,
        }
