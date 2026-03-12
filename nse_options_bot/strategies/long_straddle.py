"""Long Straddle Strategy.

Buy ATM CE + ATM PE.
Best for: Pre-event volatility play, low IVR.
Max Profit: Unlimited.
Max Loss: Net debit paid.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nse_options_bot.brokers.base import OptionType, TransactionType
from nse_options_bot.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyType,
)


class LongStraddle(BaseStrategy):
    """Long Straddle strategy implementation.

    Buy ATM CE and ATM PE.
    Profits when market moves significantly in either direction.
    """

    @property
    def strategy_type(self) -> StrategyType:
        """Get strategy type."""
        return StrategyType.LONG_STRADDLE

    @property
    def is_credit_strategy(self) -> bool:
        """This is a debit strategy."""
        return False

    @property
    def num_legs(self) -> int:
        """Two legs: long CE + long PE."""
        return 2

    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        atm_strike: Decimal | None = None,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build long straddle legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            atm_strike: ATM strike (auto-calculated if not provided)
            **kwargs: Additional parameters

        Returns:
            List of two legs (long CE, long PE)
        """
        # Calculate ATM strike if not provided
        if atm_strike is None:
            strike_step = Decimal("50") if "NIFTY" in self.underlying else Decimal("100")
            atm_strike = (spot_price / strike_step).quantize(Decimal("1")) * strike_step

        # Long Call
        long_ce = StrategyLeg(
            tradingsymbol=self.build_symbol(atm_strike, OptionType.CE),
            exchange="NFO",
            strike=atm_strike,
            option_type=OptionType.CE,
            transaction_type=TransactionType.BUY,
            quantity=quantity,
            lot_size=self.lot_size,
            expiry=self.expiry,
        )

        # Long Put
        long_pe = StrategyLeg(
            tradingsymbol=self.build_symbol(atm_strike, OptionType.PE),
            exchange="NFO",
            strike=atm_strike,
            option_type=OptionType.PE,
            transaction_type=TransactionType.BUY,
            quantity=quantity,
            lot_size=self.lot_size,
            expiry=self.expiry,
        )

        return [long_ce, long_pe]

    def calculate_max_profit(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum profit.

        Max profit = unlimited (theoretical).
        We return a large number for practical purposes.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit (practical cap)
        """
        if not legs:
            return Decimal("0")

        strike = legs[0].strike
        # Practical max: assume 20% move
        max_move = strike * Decimal("0.20")
        total_debit = self.calculate_max_loss(legs)

        return max_move * Decimal(str(legs[0].total_quantity)) - total_debit

    def calculate_max_loss(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum loss.

        Max loss = total premium paid.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss
        """
        total_premium = Decimal("0")
        for leg in legs:
            if leg.entry_price:
                # Long legs pay premium
                total_premium += leg.entry_price * Decimal(str(leg.total_quantity))

        return total_premium

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

    def calculate_required_move(
        self,
        legs: list[StrategyLeg],
    ) -> dict[str, Any]:
        """Calculate required move for profitability.

        Args:
            legs: Strategy legs

        Returns:
            Required move details
        """
        if not legs:
            return {}

        lower_be, upper_be = self.calculate_breakevens(legs)
        strike = legs[0].strike
        max_loss = self.calculate_max_loss(legs)

        if not lower_be or not upper_be:
            return {}

        required_move_pct = float(
            (upper_be - strike) / strike * 100
        )

        return {
            "atm_strike": float(strike),
            "lower_breakeven": float(lower_be),
            "upper_breakeven": float(upper_be),
            "required_move_pct": required_move_pct,
            "max_loss": float(max_loss),
            "theta_decay_exposure": "HIGH",
            "best_before_days": 5,  # Exit before last 5 days due to theta
        }

    def should_exit(
        self,
        legs: list[StrategyLeg],
        days_to_expiry: int,
        current_pnl_pct: float,
    ) -> tuple[bool, str]:
        """Check if position should be exited.

        Args:
            legs: Strategy legs with current prices
            days_to_expiry: Days to expiry
            current_pnl_pct: Current P&L as percentage of max loss

        Returns:
            Tuple of (should_exit, reason)
        """
        # Exit if profitable
        if current_pnl_pct >= 50:  # 50% of max profit potential
            return True, f"Target reached: {current_pnl_pct:.0f}% profit"

        # Exit if time decay becomes significant
        if days_to_expiry <= 3:
            return True, f"Theta decay critical: {days_to_expiry} DTE"

        # Exit if loss exceeds threshold
        if current_pnl_pct <= -50:  # Lost 50% of premium
            return True, f"Stop loss: {current_pnl_pct:.0f}% loss"

        return False, ""
