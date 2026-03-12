"""Bear Put Spread Strategy.

Directional bearish strategy: Buy higher strike PE, Sell lower strike PE.
Limited risk, limited profit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import structlog

from nse_options_bot.brokers.base import OptionType, TransactionType
from nse_options_bot.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyType,
)

logger = structlog.get_logger(__name__)


class BearPutSpread(BaseStrategy):
    """Bear Put Spread Strategy.

    Structure:
    - Buy 1 ATM/ITM PE (higher strike)
    - Sell 1 OTM PE (lower strike)

    Profit: Limited to (higher_strike - lower_strike - net_debit)
    Loss: Limited to net_debit paid
    Best for: Moderately bearish outlook
    """

    # Default spread width in points
    DEFAULT_SPREAD_WIDTH = {
        "NIFTY": 100,
        "BANKNIFTY": 200,
        "FINNIFTY": 100,
        "MIDCPNIFTY": 50,
    }

    def __init__(
        self,
        underlying: str,
        expiry: date,
        lot_size: int,
        spread_width: int | None = None,
    ) -> None:
        """Initialize strategy.

        Args:
            underlying: Underlying symbol
            expiry: Expiry date
            lot_size: Lot size
            spread_width: Spread width in points
        """
        super().__init__(underlying, expiry, lot_size)
        self._spread_width = spread_width or self.DEFAULT_SPREAD_WIDTH.get(
            underlying, 100
        )

    @property
    def strategy_type(self) -> StrategyType:
        """Get strategy type."""
        return StrategyType.BEAR_PUT_SPREAD

    @property
    def is_credit_strategy(self) -> bool:
        """This is a debit strategy."""
        return False

    @property
    def num_legs(self) -> int:
        """Number of legs."""
        return 2

    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        long_strike: Decimal | None = None,
        short_strike: Decimal | None = None,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build strategy legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            long_strike: Strike for long PE (higher)
            short_strike: Strike for short PE (lower)
            **kwargs: Additional arguments

        Returns:
            List of strategy legs
        """
        # Calculate strikes if not provided
        if long_strike is None:
            # Long strike at ATM
            long_strike = self._round_strike(spot_price)

        if short_strike is None:
            # Short strike below long strike
            short_strike = long_strike - Decimal(str(self._spread_width))

        # Build long PE leg (buy higher strike)
        long_pe = StrategyLeg(
            tradingsymbol=self.build_symbol(long_strike, OptionType.PE),
            exchange="NFO",
            strike=long_strike,
            option_type=OptionType.PE,
            transaction_type=TransactionType.BUY,
            quantity=quantity,
            lot_size=self.lot_size,
            expiry=self.expiry,
        )

        # Build short PE leg (sell lower strike)
        short_pe = StrategyLeg(
            tradingsymbol=self.build_symbol(short_strike, OptionType.PE),
            exchange="NFO",
            strike=short_strike,
            option_type=OptionType.PE,
            transaction_type=TransactionType.SELL,
            quantity=quantity,
            lot_size=self.lot_size,
            expiry=self.expiry,
        )

        return [long_pe, short_pe]

    def calculate_max_profit(self, legs: list[StrategyLeg]) -> Decimal:
        """Calculate maximum profit.

        Max profit = (Long strike - Short strike - Net debit) × Quantity

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit in INR
        """
        if len(legs) != 2:
            return Decimal("0")

        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        spread_width = long_leg.strike - short_leg.strike

        long_premium = long_leg.entry_price or Decimal("0")
        short_premium = short_leg.entry_price or Decimal("0")
        net_debit = long_premium - short_premium

        max_profit_per_share = spread_width - net_debit
        total_quantity = long_leg.total_quantity

        return max_profit_per_share * Decimal(str(total_quantity))

    def calculate_max_loss(self, legs: list[StrategyLeg]) -> Decimal:
        """Calculate maximum loss.

        Max loss = Net debit paid × Quantity

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss in INR (positive value)
        """
        if len(legs) != 2:
            return Decimal("0")

        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        long_premium = long_leg.entry_price or Decimal("0")
        short_premium = short_leg.entry_price or Decimal("0")
        net_debit = long_premium - short_premium

        total_quantity = long_leg.total_quantity

        return net_debit * Decimal(str(total_quantity))

    def calculate_breakevens(
        self,
        legs: list[StrategyLeg],
    ) -> tuple[Decimal | None, Decimal | None]:
        """Calculate breakeven points.

        Breakeven = Long strike - Net debit

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Tuple of (lower_breakeven, upper_breakeven)
            For bear put spread, only lower breakeven is meaningful
        """
        if len(legs) != 2:
            return None, None

        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return None, None

        long_premium = long_leg.entry_price or Decimal("0")
        short_premium = short_leg.entry_price or Decimal("0")
        net_debit = long_premium - short_premium

        breakeven = long_leg.strike - net_debit

        return short_leg.strike, breakeven  # Max profit below lower, breakeven at calculated point

    def _round_strike(self, price: Decimal) -> Decimal:
        """Round price to nearest strike.

        Args:
            price: Price to round

        Returns:
            Rounded strike price
        """
        strike_interval = self.DEFAULT_SPREAD_WIDTH.get(self.underlying, 100)
        return Decimal(str(round(float(price) / strike_interval) * strike_interval))

    def get_payoff_at_expiry(
        self,
        legs: list[StrategyLeg],
        spot_at_expiry: Decimal,
    ) -> Decimal:
        """Calculate payoff at expiry.

        Args:
            legs: Strategy legs
            spot_at_expiry: Spot price at expiry

        Returns:
            Payoff in INR
        """
        if len(legs) != 2:
            return Decimal("0")

        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        # Long PE payoff
        long_intrinsic = max(Decimal("0"), long_leg.strike - spot_at_expiry)
        long_payoff = (long_intrinsic - (long_leg.entry_price or Decimal("0"))) * Decimal(
            str(long_leg.total_quantity)
        )

        # Short PE payoff
        short_intrinsic = max(Decimal("0"), short_leg.strike - spot_at_expiry)
        short_payoff = ((short_leg.entry_price or Decimal("0")) - short_intrinsic) * Decimal(
            str(short_leg.total_quantity)
        )

        return long_payoff + short_payoff
