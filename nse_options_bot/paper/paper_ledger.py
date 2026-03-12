"""Paper trading ledger for tracking virtual P&L, margin, and MTM.

Tracks: virtual cash, open positions, realized P&L, unrealized P&L,
daily MTM, margin used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.config import settings

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class LedgerEntryType(str, Enum):
    """Ledger entry types."""

    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRADE = "TRADE"
    BROKERAGE = "BROKERAGE"
    STT = "STT"
    MARGIN_BLOCK = "MARGIN_BLOCK"
    MARGIN_RELEASE = "MARGIN_RELEASE"
    MTM_CREDIT = "MTM_CREDIT"
    MTM_DEBIT = "MTM_DEBIT"


@dataclass
class LedgerEntry:
    """Single ledger entry."""

    entry_id: str
    entry_type: LedgerEntryType
    amount: Decimal
    description: str
    timestamp: datetime
    order_id: str | None = None
    tradingsymbol: str | None = None
    balance_after: Decimal = Decimal("0")
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperPosition:
    """Paper trading position."""

    tradingsymbol: str
    exchange: str
    instrument_token: int
    product: str  # NRML, MIS
    quantity: int  # Positive=long, negative=short
    average_price: Decimal
    last_price: Decimal
    multiplier: int  # Lot size
    margin_blocked: Decimal
    realized_pnl: Decimal = Decimal("0")

    @property
    def unrealized_pnl(self) -> Decimal:
        """Calculate unrealized P&L."""
        if self.quantity == 0:
            return Decimal("0")
        return (self.last_price - self.average_price) * Decimal(
            str(self.quantity * self.multiplier)
        )

    @property
    def value(self) -> Decimal:
        """Get current position value."""
        return self.last_price * Decimal(str(abs(self.quantity) * self.multiplier))

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.quantity < 0


@dataclass
class DailyStatement:
    """Daily P&L statement."""

    date: datetime
    opening_balance: Decimal
    closing_balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_brokerage: Decimal
    total_stt: Decimal
    num_trades: int
    positions: list[dict[str, Any]] = field(default_factory=list)


class PaperLedger:
    """Virtual ledger for paper trading.

    Tracks all financial transactions and position changes.
    """

    def __init__(
        self,
        initial_capital: Decimal | None = None,
        brokerage_per_order: Decimal | None = None,
        stt_rate: Decimal | None = None,
        short_option_margin_pct: Decimal = Decimal("0.15"),
    ) -> None:
        """Initialize paper ledger.

        Args:
            initial_capital: Starting capital
            brokerage_per_order: Brokerage per executed order
            stt_rate: STT rate (0.0625% on sell side)
            short_option_margin_pct: Margin % for short options (flat 15%)
        """
        self._initial_capital = initial_capital or Decimal(
            str(settings.paper_initial_capital)
        )
        self._brokerage_per_order = brokerage_per_order or Decimal(
            str(settings.paper_brokerage_per_order)
        )
        self._stt_rate = stt_rate or Decimal(str(settings.paper_stt_rate))
        self._short_option_margin_pct = short_option_margin_pct

        # Current state
        self._cash_balance = self._initial_capital
        self._positions: dict[str, PaperPosition] = {}
        self._margin_used = Decimal("0")
        self._ledger_entries: list[LedgerEntry] = []
        self._entry_counter = 0

        # Daily tracking
        self._daily_realized_pnl = Decimal("0")
        self._daily_brokerage = Decimal("0")
        self._daily_stt = Decimal("0")
        self._daily_trades = 0
        self._daily_opening_balance = self._initial_capital

        # Record initial deposit
        self._add_entry(
            LedgerEntryType.DEPOSIT,
            self._initial_capital,
            "Initial capital deposit",
        )

    @property
    def cash_balance(self) -> Decimal:
        """Get current cash balance."""
        return self._cash_balance

    @property
    def margin_used(self) -> Decimal:
        """Get total margin used."""
        return self._margin_used

    @property
    def margin_available(self) -> Decimal:
        """Get available margin."""
        return self._cash_balance - self._margin_used

    @property
    def positions(self) -> dict[str, PaperPosition]:
        """Get all positions."""
        return self._positions

    @property
    def total_unrealized_pnl(self) -> Decimal:
        """Get total unrealized P&L across all positions."""
        return sum(pos.unrealized_pnl for pos in self._positions.values())

    @property
    def net_worth(self) -> Decimal:
        """Get total net worth (cash + unrealized P&L)."""
        return self._cash_balance + self.total_unrealized_pnl

    @property
    def daily_pnl(self) -> Decimal:
        """Get today's P&L (realized + unrealized)."""
        return self._daily_realized_pnl + self.total_unrealized_pnl

    def _generate_entry_id(self) -> str:
        """Generate unique entry ID."""
        self._entry_counter += 1
        return f"ENTRY-{self._entry_counter:08d}"

    def _add_entry(
        self,
        entry_type: LedgerEntryType,
        amount: Decimal,
        description: str,
        order_id: str | None = None,
        tradingsymbol: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        """Add entry to ledger.

        Args:
            entry_type: Type of entry
            amount: Amount (positive or negative)
            description: Description
            order_id: Associated order ID
            tradingsymbol: Associated symbol
            metadata: Additional data

        Returns:
            Created ledger entry
        """
        entry = LedgerEntry(
            entry_id=self._generate_entry_id(),
            entry_type=entry_type,
            amount=amount,
            description=description,
            timestamp=datetime.now(IST),
            order_id=order_id,
            tradingsymbol=tradingsymbol,
            balance_after=self._cash_balance,
            metadata=metadata or {},
        )
        self._ledger_entries.append(entry)
        return entry

    def calculate_margin_required(
        self,
        premium: Decimal,
        quantity: int,
        lot_size: int,
        is_short: bool,
        is_option: bool,
    ) -> Decimal:
        """Calculate margin required for a position.

        Args:
            premium: Option/future premium
            quantity: Number of lots
            lot_size: Lot size
            is_short: True if short position
            is_option: True if option

        Returns:
            Margin required
        """
        position_value = premium * Decimal(str(quantity * lot_size))

        if is_option:
            if is_short:
                # Short options: flat 15% margin
                return position_value * self._short_option_margin_pct
            else:
                # Long options: full premium
                return position_value
        else:
            # Futures: same margin logic
            return position_value * self._short_option_margin_pct

    def calculate_stt(self, premium: Decimal, quantity: int, lot_size: int) -> Decimal:
        """Calculate STT for sell side.

        Args:
            premium: Premium price
            quantity: Number of lots
            lot_size: Lot size

        Returns:
            STT amount
        """
        value = premium * Decimal(str(quantity * lot_size))
        return value * self._stt_rate

    def record_trade(
        self,
        order_id: str,
        tradingsymbol: str,
        exchange: str,
        instrument_token: int,
        product: str,
        transaction_type: str,  # BUY or SELL
        quantity: int,  # In lots
        price: Decimal,
        lot_size: int,
        is_option: bool = True,
    ) -> tuple[bool, str]:
        """Record a trade execution.

        Args:
            order_id: Order ID
            tradingsymbol: Trading symbol
            exchange: Exchange
            instrument_token: Instrument token
            product: Product type (NRML, MIS)
            transaction_type: BUY or SELL
            quantity: Quantity in lots
            price: Fill price
            lot_size: Lot size
            is_option: True if option trade

        Returns:
            Tuple of (success, message)
        """
        is_buy = transaction_type == "BUY"
        position_key = f"{exchange}:{tradingsymbol}"
        total_value = price * Decimal(str(quantity * lot_size))

        # Calculate costs
        brokerage = self._brokerage_per_order
        stt = self.calculate_stt(price, quantity, lot_size) if not is_buy else Decimal("0")
        total_costs = brokerage + stt

        # Check if we have an existing position
        existing_position = self._positions.get(position_key)

        if existing_position:
            # Update existing position
            return self._update_position(
                order_id=order_id,
                position=existing_position,
                position_key=position_key,
                is_buy=is_buy,
                quantity=quantity,
                price=price,
                lot_size=lot_size,
                brokerage=brokerage,
                stt=stt,
                is_option=is_option,
            )
        else:
            # Create new position
            return self._create_position(
                order_id=order_id,
                position_key=position_key,
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                instrument_token=instrument_token,
                product=product,
                is_buy=is_buy,
                quantity=quantity,
                price=price,
                lot_size=lot_size,
                brokerage=brokerage,
                stt=stt,
                is_option=is_option,
            )

    def _create_position(
        self,
        order_id: str,
        position_key: str,
        tradingsymbol: str,
        exchange: str,
        instrument_token: int,
        product: str,
        is_buy: bool,
        quantity: int,
        price: Decimal,
        lot_size: int,
        brokerage: Decimal,
        stt: Decimal,
        is_option: bool,
    ) -> tuple[bool, str]:
        """Create new position.

        Args:
            order_id: Order ID
            position_key: Position key
            tradingsymbol: Trading symbol
            exchange: Exchange
            instrument_token: Instrument token
            product: Product type
            is_buy: True if buy
            quantity: Quantity in lots
            price: Fill price
            lot_size: Lot size
            brokerage: Brokerage cost
            stt: STT cost
            is_option: True if option

        Returns:
            Tuple of (success, message)
        """
        signed_quantity = quantity if is_buy else -quantity
        is_short = not is_buy
        total_value = price * Decimal(str(quantity * lot_size))

        # Calculate margin required
        margin_required = self.calculate_margin_required(
            price, quantity, lot_size, is_short, is_option
        )
        total_costs = brokerage + stt

        # Check if we have enough margin
        if is_buy:
            required_cash = total_value + total_costs
        else:
            required_cash = margin_required + total_costs

        if required_cash > self.margin_available:
            return False, f"Insufficient margin: required {required_cash}, available {self.margin_available}"

        # Deduct costs
        if is_buy:
            self._cash_balance -= total_value
        self._cash_balance -= total_costs

        # Block margin
        if is_short:
            self._margin_used += margin_required

        # Create position
        self._positions[position_key] = PaperPosition(
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            instrument_token=instrument_token,
            product=product,
            quantity=signed_quantity,
            average_price=price,
            last_price=price,
            multiplier=lot_size,
            margin_blocked=margin_required if is_short else Decimal("0"),
        )

        # Record ledger entries
        self._add_entry(
            LedgerEntryType.TRADE,
            -total_value if is_buy else Decimal("0"),
            f"{'BUY' if is_buy else 'SELL'} {quantity} lots of {tradingsymbol} @ {price}",
            order_id=order_id,
            tradingsymbol=tradingsymbol,
        )

        self._add_entry(
            LedgerEntryType.BROKERAGE,
            -brokerage,
            f"Brokerage for order {order_id}",
            order_id=order_id,
        )

        if stt > 0:
            self._add_entry(
                LedgerEntryType.STT,
                -stt,
                f"STT for order {order_id}",
                order_id=order_id,
            )

        if is_short:
            self._add_entry(
                LedgerEntryType.MARGIN_BLOCK,
                margin_required,
                f"Margin blocked for short {tradingsymbol}",
                order_id=order_id,
                tradingsymbol=tradingsymbol,
            )

        # Update daily stats
        self._daily_brokerage += brokerage
        self._daily_stt += stt
        self._daily_trades += 1

        logger.info(
            "position_created",
            order_id=order_id,
            tradingsymbol=tradingsymbol,
            quantity=signed_quantity,
            price=str(price),
            margin_blocked=str(margin_required) if is_short else "0",
        )

        return True, f"Position created: {signed_quantity} lots @ {price}"

    def _update_position(
        self,
        order_id: str,
        position: PaperPosition,
        position_key: str,
        is_buy: bool,
        quantity: int,
        price: Decimal,
        lot_size: int,
        brokerage: Decimal,
        stt: Decimal,
        is_option: bool,
    ) -> tuple[bool, str]:
        """Update existing position.

        Args:
            order_id: Order ID
            position: Existing position
            position_key: Position key
            is_buy: True if buy
            quantity: Quantity in lots
            price: Fill price
            lot_size: Lot size
            brokerage: Brokerage cost
            stt: STT cost
            is_option: True if option

        Returns:
            Tuple of (success, message)
        """
        total_value = price * Decimal(str(quantity * lot_size))
        total_costs = brokerage + stt

        # Determine if increasing or reducing position
        if (position.is_long and is_buy) or (position.is_short and not is_buy):
            # Increasing position
            return self._increase_position(
                order_id, position, position_key, is_buy, quantity, price,
                lot_size, brokerage, stt, is_option
            )
        else:
            # Reducing/closing/reversing position
            return self._reduce_position(
                order_id, position, position_key, is_buy, quantity, price,
                lot_size, brokerage, stt, is_option
            )

    def _increase_position(
        self,
        order_id: str,
        position: PaperPosition,
        position_key: str,
        is_buy: bool,
        quantity: int,
        price: Decimal,
        lot_size: int,
        brokerage: Decimal,
        stt: Decimal,
        is_option: bool,
    ) -> tuple[bool, str]:
        """Increase existing position."""
        total_value = price * Decimal(str(quantity * lot_size))
        is_short = not is_buy

        # Calculate additional margin
        additional_margin = self.calculate_margin_required(
            price, quantity, lot_size, is_short, is_option
        )
        total_costs = brokerage + stt

        if is_buy:
            required_cash = total_value + total_costs
        else:
            required_cash = additional_margin + total_costs

        if required_cash > self.margin_available:
            return False, f"Insufficient margin: required {required_cash}, available {self.margin_available}"

        # Update average price
        old_qty = abs(position.quantity)
        new_qty = old_qty + quantity
        old_value = position.average_price * Decimal(str(old_qty * lot_size))
        new_value = price * Decimal(str(quantity * lot_size))
        position.average_price = (old_value + new_value) / Decimal(str(new_qty * lot_size))

        # Update quantity
        if is_buy:
            position.quantity += quantity
            self._cash_balance -= total_value
        else:
            position.quantity -= quantity
            self._margin_used += additional_margin
            position.margin_blocked += additional_margin

        self._cash_balance -= total_costs

        # Record entries
        self._add_entry(
            LedgerEntryType.TRADE,
            -total_value if is_buy else Decimal("0"),
            f"{'BUY' if is_buy else 'SELL'} {quantity} lots of {position.tradingsymbol} @ {price} (increase)",
            order_id=order_id,
            tradingsymbol=position.tradingsymbol,
        )
        self._add_entry(LedgerEntryType.BROKERAGE, -brokerage, f"Brokerage for {order_id}")
        if stt > 0:
            self._add_entry(LedgerEntryType.STT, -stt, f"STT for {order_id}")

        self._daily_brokerage += brokerage
        self._daily_stt += stt
        self._daily_trades += 1

        logger.info(
            "position_increased",
            order_id=order_id,
            tradingsymbol=position.tradingsymbol,
            new_quantity=position.quantity,
            new_avg_price=str(position.average_price),
        )

        return True, f"Position increased to {position.quantity} lots @ avg {position.average_price}"

    def _reduce_position(
        self,
        order_id: str,
        position: PaperPosition,
        position_key: str,
        is_buy: bool,
        quantity: int,
        price: Decimal,
        lot_size: int,
        brokerage: Decimal,
        stt: Decimal,
        is_option: bool,
    ) -> tuple[bool, str]:
        """Reduce/close existing position."""
        abs_position_qty = abs(position.quantity)
        reduce_qty = min(quantity, abs_position_qty)
        total_costs = brokerage + stt

        # Check margin for closing short
        if position.is_short and is_buy:
            close_value = price * Decimal(str(reduce_qty * lot_size))
            if close_value + total_costs > self.margin_available:
                return False, f"Insufficient margin to close: required {close_value + total_costs}"

        # Calculate realized P&L
        pnl_per_unit = price - position.average_price
        if position.is_short:
            pnl_per_unit = -pnl_per_unit
        realized_pnl = pnl_per_unit * Decimal(str(reduce_qty * lot_size))

        # Update position
        if position.is_long:
            position.quantity -= reduce_qty
            self._cash_balance += price * Decimal(str(reduce_qty * lot_size))
        else:
            position.quantity += reduce_qty
            # Release margin proportionally
            margin_to_release = position.margin_blocked * Decimal(str(reduce_qty)) / Decimal(str(abs_position_qty))
            self._margin_used -= margin_to_release
            position.margin_blocked -= margin_to_release
            self._add_entry(
                LedgerEntryType.MARGIN_RELEASE,
                margin_to_release,
                f"Margin released for closing {position.tradingsymbol}",
            )
            # For short close, we need to pay the difference
            close_cost = price * Decimal(str(reduce_qty * lot_size))
            self._cash_balance -= close_cost

        # Update realized P&L
        position.realized_pnl += realized_pnl
        self._daily_realized_pnl += realized_pnl
        self._cash_balance += realized_pnl
        self._cash_balance -= total_costs

        # Record entries
        self._add_entry(
            LedgerEntryType.TRADE,
            realized_pnl,
            f"{'BUY' if is_buy else 'SELL'} {reduce_qty} lots of {position.tradingsymbol} @ {price} (close)",
            order_id=order_id,
            tradingsymbol=position.tradingsymbol,
            metadata={"realized_pnl": str(realized_pnl)},
        )
        self._add_entry(LedgerEntryType.BROKERAGE, -brokerage, f"Brokerage for {order_id}")
        if stt > 0:
            self._add_entry(LedgerEntryType.STT, -stt, f"STT for {order_id}")

        self._daily_brokerage += brokerage
        self._daily_stt += stt
        self._daily_trades += 1

        # Remove position if fully closed
        if position.quantity == 0:
            del self._positions[position_key]
            logger.info(
                "position_closed",
                order_id=order_id,
                tradingsymbol=position.tradingsymbol,
                realized_pnl=str(position.realized_pnl),
            )
            return True, f"Position closed, realized P&L: {realized_pnl}"

        # Handle reversal if quantity exceeds position
        if quantity > abs_position_qty:
            remaining = quantity - abs_position_qty
            # Create new position in opposite direction
            return self._create_position(
                order_id=order_id,
                position_key=position_key,
                tradingsymbol=position.tradingsymbol,
                exchange=position.exchange,
                instrument_token=position.instrument_token,
                product=position.product,
                is_buy=is_buy,
                quantity=remaining,
                price=price,
                lot_size=lot_size,
                brokerage=Decimal("0"),  # Already charged
                stt=Decimal("0"),
                is_option=is_option,
            )

        logger.info(
            "position_reduced",
            order_id=order_id,
            tradingsymbol=position.tradingsymbol,
            new_quantity=position.quantity,
            realized_pnl=str(realized_pnl),
        )

        return True, f"Position reduced to {position.quantity} lots, realized P&L: {realized_pnl}"

    def update_mtm(self, prices: dict[str, Decimal]) -> None:
        """Update mark-to-market for all positions.

        Args:
            prices: Dict mapping position_key to current price
        """
        for key, position in self._positions.items():
            if key in prices:
                position.last_price = prices[key]

        logger.debug(
            "mtm_updated",
            positions=len(self._positions),
            total_unrealized_pnl=str(self.total_unrealized_pnl),
        )

    def reset_daily_stats(self) -> DailyStatement:
        """Reset daily statistics and return statement.

        Returns:
            Daily statement
        """
        statement = DailyStatement(
            date=datetime.now(IST),
            opening_balance=self._daily_opening_balance,
            closing_balance=self._cash_balance,
            realized_pnl=self._daily_realized_pnl,
            unrealized_pnl=self.total_unrealized_pnl,
            total_brokerage=self._daily_brokerage,
            total_stt=self._daily_stt,
            num_trades=self._daily_trades,
            positions=[
                {
                    "symbol": pos.tradingsymbol,
                    "quantity": pos.quantity,
                    "avg_price": str(pos.average_price),
                    "last_price": str(pos.last_price),
                    "unrealized_pnl": str(pos.unrealized_pnl),
                }
                for pos in self._positions.values()
            ],
        )

        # Reset daily counters
        self._daily_realized_pnl = Decimal("0")
        self._daily_brokerage = Decimal("0")
        self._daily_stt = Decimal("0")
        self._daily_trades = 0
        self._daily_opening_balance = self._cash_balance

        return statement

    def get_ledger_entries(
        self,
        entry_type: LedgerEntryType | None = None,
        tradingsymbol: str | None = None,
        limit: int = 100,
    ) -> list[LedgerEntry]:
        """Get ledger entries with optional filters.

        Args:
            entry_type: Filter by entry type
            tradingsymbol: Filter by symbol
            limit: Maximum entries to return

        Returns:
            List of ledger entries
        """
        entries = self._ledger_entries

        if entry_type:
            entries = [e for e in entries if e.entry_type == entry_type]

        if tradingsymbol:
            entries = [e for e in entries if e.tradingsymbol == tradingsymbol]

        return entries[-limit:]
