"""Base strategy abstract class.

Defines the interface for all option strategies.
Each strategy must implement: build_legs(), max_profit, max_loss.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.brokers.base import OptionType, ProductType, TransactionType

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class StrategyType(str, Enum):
    """Strategy type enumeration."""

    SHORT_STRADDLE = "SHORT_STRADDLE"
    LONG_STRADDLE = "LONG_STRADDLE"
    IRON_CONDOR = "IRON_CONDOR"
    BULL_CALL_SPREAD = "BULL_CALL_SPREAD"
    BEAR_PUT_SPREAD = "BEAR_PUT_SPREAD"
    BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
    BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"
    STRANGLE = "STRANGLE"
    BUTTERFLY = "BUTTERFLY"
    CALENDAR_SPREAD = "CALENDAR_SPREAD"


@dataclass
class StrategyLeg:
    """Single leg of a strategy."""

    tradingsymbol: str
    exchange: str
    strike: Decimal
    option_type: OptionType
    transaction_type: TransactionType
    quantity: int  # Number of lots
    lot_size: int
    entry_price: Decimal | None = None
    current_price: Decimal | None = None
    expiry: date | None = None
    product: ProductType = ProductType.NRML
    order_id: str | None = None

    @property
    def total_quantity(self) -> int:
        """Total quantity including lot size."""
        return self.quantity * self.lot_size

    @property
    def is_long(self) -> bool:
        """Check if leg is long."""
        return self.transaction_type == TransactionType.BUY

    @property
    def is_short(self) -> bool:
        """Check if leg is short."""
        return self.transaction_type == TransactionType.SELL

    @property
    def premium_paid(self) -> Decimal:
        """Premium paid (positive) or received (negative) for this leg."""
        if self.entry_price is None:
            return Decimal("0")

        premium = self.entry_price * Decimal(str(self.total_quantity))
        if self.is_long:
            return premium  # Paid
        else:
            return -premium  # Received

    @property
    def current_value(self) -> Decimal:
        """Current value of the leg."""
        if self.current_price is None:
            return Decimal("0")

        value = self.current_price * Decimal(str(self.total_quantity))
        if self.is_long:
            return value
        else:
            return -value  # Short position has negative value

    @property
    def pnl(self) -> Decimal:
        """P&L for this leg."""
        if self.entry_price is None or self.current_price is None:
            return Decimal("0")

        if self.is_long:
            return (self.current_price - self.entry_price) * Decimal(str(self.total_quantity))
        else:
            return (self.entry_price - self.current_price) * Decimal(str(self.total_quantity))


@dataclass
class StrategyPosition:
    """Complete strategy position."""

    strategy_type: StrategyType
    underlying: str
    expiry: date
    legs: list[StrategyLeg]
    entry_time: datetime
    tag: str = ""
    notes: str = ""

    # Calculated at entry
    max_profit: Decimal = Decimal("0")
    max_loss: Decimal = Decimal("0")
    breakeven_upper: Decimal | None = None
    breakeven_lower: Decimal | None = None

    # Tracking
    realized_pnl: Decimal = Decimal("0")
    status: str = "OPEN"  # OPEN, PARTIAL, CLOSED, EXPIRED

    @property
    def total_premium(self) -> Decimal:
        """Total net premium (positive = debit, negative = credit)."""
        return sum(leg.premium_paid for leg in self.legs)

    @property
    def current_pnl(self) -> Decimal:
        """Current unrealized P&L."""
        return sum(leg.pnl for leg in self.legs)

    @property
    def is_credit_strategy(self) -> bool:
        """Check if strategy received net credit."""
        return self.total_premium < 0

    @property
    def is_debit_strategy(self) -> bool:
        """Check if strategy paid net debit."""
        return self.total_premium > 0

    def update_prices(self, prices: dict[str, Decimal]) -> None:
        """Update current prices for all legs.

        Args:
            prices: Dict mapping tradingsymbol to current price
        """
        for leg in self.legs:
            if leg.tradingsymbol in prices:
                leg.current_price = prices[leg.tradingsymbol]


class BaseStrategy(ABC):
    """Abstract base class for option strategies.

    All strategies must implement:
    - build_legs(): Create strategy legs
    - calculate_max_profit(): Maximum profit potential
    - calculate_max_loss(): Maximum loss potential
    - calculate_breakevens(): Breakeven points
    """

    def __init__(
        self,
        underlying: str,
        expiry: date,
        lot_size: int,
    ) -> None:
        """Initialize strategy.

        Args:
            underlying: Underlying symbol
            expiry: Expiry date
            lot_size: Lot size for the underlying
        """
        self.underlying = underlying
        self.expiry = expiry
        self.lot_size = lot_size

    @property
    @abstractmethod
    def strategy_type(self) -> StrategyType:
        """Get strategy type."""
        ...

    @property
    @abstractmethod
    def is_credit_strategy(self) -> bool:
        """Check if this is a credit strategy (receives premium)."""
        ...

    @property
    @abstractmethod
    def num_legs(self) -> int:
        """Number of legs in the strategy."""
        ...

    @abstractmethod
    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build strategy legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            **kwargs: Strategy-specific parameters

        Returns:
            List of strategy legs
        """
        ...

    @abstractmethod
    def calculate_max_profit(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum profit potential.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit in INR
        """
        ...

    @abstractmethod
    def calculate_max_loss(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum loss potential.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss in INR (positive value)
        """
        ...

    @abstractmethod
    def calculate_breakevens(
        self,
        legs: list[StrategyLeg],
    ) -> tuple[Decimal | None, Decimal | None]:
        """Calculate breakeven points.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Tuple of (lower_breakeven, upper_breakeven)
        """
        ...

    def create_position(
        self,
        spot_price: Decimal,
        quantity: int,
        prices: dict[str, Decimal],
        tag: str = "",
        **kwargs: Any,
    ) -> StrategyPosition:
        """Create a strategy position.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            prices: Dict mapping tradingsymbol to entry price
            tag: Position tag
            **kwargs: Strategy-specific parameters

        Returns:
            StrategyPosition object
        """
        legs = self.build_legs(spot_price, quantity, **kwargs)

        # Set entry prices
        for leg in legs:
            if leg.tradingsymbol in prices:
                leg.entry_price = prices[leg.tradingsymbol]
                leg.current_price = prices[leg.tradingsymbol]

        max_profit = self.calculate_max_profit(legs)
        max_loss = self.calculate_max_loss(legs)
        be_lower, be_upper = self.calculate_breakevens(legs)

        return StrategyPosition(
            strategy_type=self.strategy_type,
            underlying=self.underlying,
            expiry=self.expiry,
            legs=legs,
            entry_time=datetime.now(IST),
            tag=tag,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_upper=be_upper,
            breakeven_lower=be_lower,
        )

    def get_required_margin(
        self,
        legs: list[StrategyLeg],
        margin_pct: Decimal = Decimal("0.15"),
    ) -> Decimal:
        """Estimate required margin.

        Args:
            legs: Strategy legs
            margin_pct: Margin percentage for short options

        Returns:
            Estimated margin requirement
        """
        total_margin = Decimal("0")

        for leg in legs:
            if leg.is_short and leg.entry_price:
                # Short option requires margin
                leg_value = leg.entry_price * Decimal(str(leg.total_quantity))
                total_margin += leg_value * margin_pct

            elif leg.is_long and leg.entry_price:
                # Long option requires full premium
                total_margin += leg.entry_price * Decimal(str(leg.total_quantity))

        return total_margin

    def validate_legs(self, legs: list[StrategyLeg]) -> tuple[bool, str]:
        """Validate strategy legs.

        Args:
            legs: Strategy legs

        Returns:
            Tuple of (is_valid, error_message)
        """
        if len(legs) != self.num_legs:
            return False, f"Expected {self.num_legs} legs, got {len(legs)}"

        # Check all legs have same expiry
        expiries = {leg.expiry for leg in legs if leg.expiry}
        if len(expiries) > 1:
            return False, "All legs must have same expiry"

        return True, ""

    def build_symbol(
        self,
        strike: Decimal,
        option_type: OptionType,
    ) -> str:
        """Build trading symbol for an option.

        Args:
            strike: Strike price
            option_type: CE or PE

        Returns:
            Trading symbol
        """
        # Format: NIFTY24D1925500CE
        year = self.expiry.year % 100
        month = self.expiry.month
        day = self.expiry.day

        month_letters = "ABCDEFGHIJKL"
        month_letter = month_letters[month - 1] if month <= 12 else str(month)

        return f"{self.underlying}{year}{month_letter}{day:02d}{int(strike)}{option_type.value}"
