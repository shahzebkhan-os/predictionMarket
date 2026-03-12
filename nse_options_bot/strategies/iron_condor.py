"""Iron Condor Strategy.

Sell OTM CE + OTM PE, Buy further OTM CE + PE for protection.
Best for: Range-bound markets, moderate IV.
Max Profit: Net premium received.
Max Loss: Width of wing - net premium.
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


class IronCondor(BaseStrategy):
    """Iron Condor strategy implementation.

    Sell OTM Call + OTM Put, Buy further OTM Call + Put for protection.
    """

    @property
    def strategy_type(self) -> StrategyType:
        """Get strategy type."""
        return StrategyType.IRON_CONDOR

    @property
    def is_credit_strategy(self) -> bool:
        """This is a credit strategy."""
        return True

    @property
    def num_legs(self) -> int:
        """Four legs."""
        return 4

    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        short_ce_strike: Decimal | None = None,
        short_pe_strike: Decimal | None = None,
        wing_width: int = 100,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build iron condor legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            short_ce_strike: Strike for short call (default: spot + 2%)
            short_pe_strike: Strike for short put (default: spot - 2%)
            wing_width: Width between short and long strikes
            **kwargs: Additional parameters

        Returns:
            List of four legs
        """
        strike_step = Decimal("50") if "NIFTY" in self.underlying else Decimal("100")
        wing_decimal = Decimal(str(wing_width))

        # Default OTM strikes at ~2% from spot
        if short_ce_strike is None:
            short_ce_strike = ((spot_price * Decimal("1.02")) / strike_step).quantize(
                Decimal("1")
            ) * strike_step

        if short_pe_strike is None:
            short_pe_strike = ((spot_price * Decimal("0.98")) / strike_step).quantize(
                Decimal("1")
            ) * strike_step

        long_ce_strike = short_ce_strike + wing_decimal
        long_pe_strike = short_pe_strike - wing_decimal

        legs = [
            # Short Call (sell)
            StrategyLeg(
                tradingsymbol=self.build_symbol(short_ce_strike, OptionType.CE),
                exchange="NFO",
                strike=short_ce_strike,
                option_type=OptionType.CE,
                transaction_type=TransactionType.SELL,
                quantity=quantity,
                lot_size=self.lot_size,
                expiry=self.expiry,
            ),
            # Long Call (buy for protection)
            StrategyLeg(
                tradingsymbol=self.build_symbol(long_ce_strike, OptionType.CE),
                exchange="NFO",
                strike=long_ce_strike,
                option_type=OptionType.CE,
                transaction_type=TransactionType.BUY,
                quantity=quantity,
                lot_size=self.lot_size,
                expiry=self.expiry,
            ),
            # Short Put (sell)
            StrategyLeg(
                tradingsymbol=self.build_symbol(short_pe_strike, OptionType.PE),
                exchange="NFO",
                strike=short_pe_strike,
                option_type=OptionType.PE,
                transaction_type=TransactionType.SELL,
                quantity=quantity,
                lot_size=self.lot_size,
                expiry=self.expiry,
            ),
            # Long Put (buy for protection)
            StrategyLeg(
                tradingsymbol=self.build_symbol(long_pe_strike, OptionType.PE),
                exchange="NFO",
                strike=long_pe_strike,
                option_type=OptionType.PE,
                transaction_type=TransactionType.BUY,
                quantity=quantity,
                lot_size=self.lot_size,
                expiry=self.expiry,
            ),
        ]

        return legs

    def calculate_max_profit(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum profit.

        Max profit = net premium received.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit
        """
        net_premium = Decimal("0")
        for leg in legs:
            if leg.entry_price:
                if leg.is_short:
                    net_premium += leg.entry_price * Decimal(str(leg.total_quantity))
                else:
                    net_premium -= leg.entry_price * Decimal(str(leg.total_quantity))

        return max(Decimal("0"), net_premium)

    def calculate_max_loss(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum loss.

        Max loss = wing width - net premium.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss
        """
        # Find call spread width
        call_legs = [leg for leg in legs if leg.option_type == OptionType.CE]
        put_legs = [leg for leg in legs if leg.option_type == OptionType.PE]

        if len(call_legs) < 2 or len(put_legs) < 2:
            return Decimal("0")

        # Width is the same for both sides in a standard iron condor
        call_strikes = sorted([leg.strike for leg in call_legs])
        wing_width = call_strikes[1] - call_strikes[0]

        net_premium = self.calculate_max_profit(legs)
        total_qty = legs[0].total_quantity

        return (wing_width * Decimal(str(total_qty))) - net_premium

    def calculate_breakevens(
        self,
        legs: list[StrategyLeg],
    ) -> tuple[Decimal | None, Decimal | None]:
        """Calculate breakeven points.

        Upper BE = short call strike + net premium per share
        Lower BE = short put strike - net premium per share

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Tuple of (lower_breakeven, upper_breakeven)
        """
        short_ce = None
        short_pe = None

        for leg in legs:
            if leg.is_short and leg.option_type == OptionType.CE:
                short_ce = leg
            elif leg.is_short and leg.option_type == OptionType.PE:
                short_pe = leg

        if not short_ce or not short_pe:
            return None, None

        # Net premium per share
        net_premium_total = Decimal("0")
        for leg in legs:
            if leg.entry_price:
                if leg.is_short:
                    net_premium_total += leg.entry_price
                else:
                    net_premium_total -= leg.entry_price

        upper_be = short_ce.strike + net_premium_total
        lower_be = short_pe.strike - net_premium_total

        return lower_be, upper_be

    def get_profit_zones(
        self,
        legs: list[StrategyLeg],
    ) -> dict[str, Any]:
        """Get profit zone information.

        Args:
            legs: Strategy legs

        Returns:
            Profit zone details
        """
        lower_be, upper_be = self.calculate_breakevens(legs)
        max_profit = self.calculate_max_profit(legs)
        max_loss = self.calculate_max_loss(legs)

        short_ce = None
        short_pe = None
        long_ce = None
        long_pe = None

        for leg in legs:
            if leg.option_type == OptionType.CE:
                if leg.is_short:
                    short_ce = leg
                else:
                    long_ce = leg
            else:
                if leg.is_short:
                    short_pe = leg
                else:
                    long_pe = leg

        return {
            "profit_zone": {
                "lower": lower_be,
                "upper": upper_be,
            },
            "max_profit_zone": {
                "lower": short_pe.strike if short_pe else None,
                "upper": short_ce.strike if short_ce else None,
            },
            "max_loss_above": long_ce.strike if long_ce else None,
            "max_loss_below": long_pe.strike if long_pe else None,
            "max_profit": float(max_profit),
            "max_loss": float(max_loss),
            "risk_reward": float(max_loss / max_profit) if max_profit > 0 else None,
        }
