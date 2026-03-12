"""Short Straddle Strategy.

Sell ATM CE + PE.
Best for: High IVR (>70), Range-bound markets.
Max Profit: Net premium received.
Max Loss: Unlimited.
Breakeven: ATM ± straddle price.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nse_options_bot.brokers.base import OptionType, ProductType, TransactionType
from nse_options_bot.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyType,
)


class ShortStraddle(BaseStrategy):
    """Short Straddle strategy implementation.

    Sell ATM CE and ATM PE.
    Profits when market stays within breakeven range.
    """

    @property
    def strategy_type(self) -> StrategyType:
        """Get strategy type."""
        return StrategyType.SHORT_STRADDLE

    @property
    def is_credit_strategy(self) -> bool:
        """This is a credit strategy."""
        return True

    @property
    def num_legs(self) -> int:
        """Two legs: short CE + short PE."""
        return 2

    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        atm_strike: Decimal | None = None,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build short straddle legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            atm_strike: ATM strike (auto-calculated if not provided)
            **kwargs: Additional parameters

        Returns:
            List of two legs (short CE, short PE)
        """
        # Calculate ATM strike if not provided
        if atm_strike is None:
            # Round to nearest strike (assuming 50 pt intervals for NIFTY)
            strike_step = Decimal("50") if "NIFTY" in self.underlying else Decimal("100")
            atm_strike = (spot_price / strike_step).quantize(Decimal("1")) * strike_step

        # Short Call
        short_ce = StrategyLeg(
            tradingsymbol=self.build_symbol(atm_strike, OptionType.CE),
            exchange="NFO",
            strike=atm_strike,
            option_type=OptionType.CE,
            transaction_type=TransactionType.SELL,
            quantity=quantity,
            lot_size=self.lot_size,
            expiry=self.expiry,
        )

        # Short Put
        short_pe = StrategyLeg(
            tradingsymbol=self.build_symbol(atm_strike, OptionType.PE),
            exchange="NFO",
            strike=atm_strike,
            option_type=OptionType.PE,
            transaction_type=TransactionType.SELL,
            quantity=quantity,
            lot_size=self.lot_size,
            expiry=self.expiry,
        )

        return [short_ce, short_pe]

    def calculate_max_profit(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum profit.

        Max profit = net premium received (when price = ATM at expiry).

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit
        """
        total_premium = Decimal("0")
        for leg in legs:
            if leg.entry_price:
                # Short legs receive premium
                total_premium += leg.entry_price * Decimal(str(leg.total_quantity))

        return total_premium

    def calculate_max_loss(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum loss.

        Max loss = unlimited (capped at some practical level).

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss (practical cap based on spot move)
        """
        # Practical max loss: assume 10% spot move
        if not legs:
            return Decimal("0")

        strike = legs[0].strike
        max_move = strike * Decimal("0.10")  # 10% move
        total_premium = self.calculate_max_profit(legs)

        # Loss = move beyond breakeven
        return max_move * Decimal(str(legs[0].total_quantity)) - total_premium

    def calculate_breakevens(
        self,
        legs: list[StrategyLeg],
    ) -> tuple[Decimal | None, Decimal | None]:
        """Calculate breakeven points.

        Upper BE = ATM + straddle price
        Lower BE = ATM - straddle price

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Tuple of (lower_breakeven, upper_breakeven)
        """
        if not legs:
            return None, None

        strike = legs[0].strike
        total_premium_per_share = Decimal("0")

        for leg in legs:
            if leg.entry_price:
                total_premium_per_share += leg.entry_price

        upper_be = strike + total_premium_per_share
        lower_be = strike - total_premium_per_share

        return lower_be, upper_be

    def get_adjustment_triggers(
        self,
        legs: list[StrategyLeg],
        spot_price: Decimal,
    ) -> dict[str, Any]:
        """Get adjustment trigger levels.

        Args:
            legs: Strategy legs
            spot_price: Current spot price

        Returns:
            Dict with adjustment triggers
        """
        lower_be, upper_be = self.calculate_breakevens(legs)
        strike = legs[0].strike if legs else spot_price

        return {
            "upper_warning": strike + (upper_be - strike) * Decimal("0.7") if upper_be else None,
            "lower_warning": strike - (strike - lower_be) * Decimal("0.7") if lower_be else None,
            "upper_breakeven": upper_be,
            "lower_breakeven": lower_be,
            "atm_strike": strike,
            "current_spot": spot_price,
            "needs_adjustment": (
                spot_price > upper_be if upper_be else False
            ) or (
                spot_price < lower_be if lower_be else False
            ),
        }
