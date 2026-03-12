"""Bull Call Spread Strategy.

Buy lower strike CE, Sell higher strike CE.
Best for: Moderately bullish outlook.
Max Profit: (High strike - Low strike) - net debit.
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


class BullCallSpread(BaseStrategy):
    """Bull Call Spread strategy implementation.

    Buy ATM/ITM Call, Sell OTM Call.
    Profits when price moves up but caps profit at short strike.
    """

    @property
    def strategy_type(self) -> StrategyType:
        """Get strategy type."""
        return StrategyType.BULL_CALL_SPREAD

    @property
    def is_credit_strategy(self) -> bool:
        """This is a debit strategy."""
        return False

    @property
    def num_legs(self) -> int:
        """Two legs."""
        return 2

    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        long_strike: Decimal | None = None,
        short_strike: Decimal | None = None,
        spread_width: int = 100,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build bull call spread legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            long_strike: Strike for long call (default: ATM)
            short_strike: Strike for short call (default: ATM + spread_width)
            spread_width: Width between strikes
            **kwargs: Additional parameters

        Returns:
            List of two legs
        """
        strike_step = Decimal("50") if "NIFTY" in self.underlying else Decimal("100")

        # Default: buy ATM, sell OTM
        if long_strike is None:
            long_strike = (spot_price / strike_step).quantize(Decimal("1")) * strike_step

        if short_strike is None:
            short_strike = long_strike + Decimal(str(spread_width))

        legs = [
            # Long Call (buy)
            StrategyLeg(
                tradingsymbol=self.build_symbol(long_strike, OptionType.CE),
                exchange="NFO",
                strike=long_strike,
                option_type=OptionType.CE,
                transaction_type=TransactionType.BUY,
                quantity=quantity,
                lot_size=self.lot_size,
                expiry=self.expiry,
            ),
            # Short Call (sell)
            StrategyLeg(
                tradingsymbol=self.build_symbol(short_strike, OptionType.CE),
                exchange="NFO",
                strike=short_strike,
                option_type=OptionType.CE,
                transaction_type=TransactionType.SELL,
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

        Max profit = spread width - net debit.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit
        """
        if len(legs) < 2:
            return Decimal("0")

        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        spread_width = short_leg.strike - long_leg.strike
        net_debit = Decimal("0")

        if long_leg.entry_price and short_leg.entry_price:
            net_debit = long_leg.entry_price - short_leg.entry_price

        profit_per_share = spread_width - net_debit
        return profit_per_share * Decimal(str(long_leg.total_quantity))

    def calculate_max_loss(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum loss.

        Max loss = net debit paid.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss
        """
        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        if not long_leg.entry_price or not short_leg.entry_price:
            return Decimal("0")

        net_debit = long_leg.entry_price - short_leg.entry_price
        return net_debit * Decimal(str(long_leg.total_quantity))

    def calculate_breakevens(
        self,
        legs: list[StrategyLeg],
    ) -> tuple[Decimal | None, Decimal | None]:
        """Calculate breakeven point.

        BE = long strike + net debit.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Tuple of (lower_breakeven, upper_breakeven)
        """
        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return None, None

        if not long_leg.entry_price or not short_leg.entry_price:
            return None, None

        net_debit = long_leg.entry_price - short_leg.entry_price
        breakeven = long_leg.strike + net_debit

        # Bull call spread has single breakeven
        return breakeven, None


class BearPutSpread(BaseStrategy):
    """Bear Put Spread strategy implementation.

    Buy higher strike PE, Sell lower strike PE.
    Profits when price moves down but caps profit at short strike.
    """

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
        """Two legs."""
        return 2

    def build_legs(
        self,
        spot_price: Decimal,
        quantity: int,
        long_strike: Decimal | None = None,
        short_strike: Decimal | None = None,
        spread_width: int = 100,
        **kwargs: Any,
    ) -> list[StrategyLeg]:
        """Build bear put spread legs.

        Args:
            spot_price: Current spot price
            quantity: Number of lots
            long_strike: Strike for long put (default: ATM)
            short_strike: Strike for short put (default: ATM - spread_width)
            spread_width: Width between strikes
            **kwargs: Additional parameters

        Returns:
            List of two legs
        """
        strike_step = Decimal("50") if "NIFTY" in self.underlying else Decimal("100")

        # Default: buy ATM, sell OTM
        if long_strike is None:
            long_strike = (spot_price / strike_step).quantize(Decimal("1")) * strike_step

        if short_strike is None:
            short_strike = long_strike - Decimal(str(spread_width))

        legs = [
            # Long Put (buy)
            StrategyLeg(
                tradingsymbol=self.build_symbol(long_strike, OptionType.PE),
                exchange="NFO",
                strike=long_strike,
                option_type=OptionType.PE,
                transaction_type=TransactionType.BUY,
                quantity=quantity,
                lot_size=self.lot_size,
                expiry=self.expiry,
            ),
            # Short Put (sell)
            StrategyLeg(
                tradingsymbol=self.build_symbol(short_strike, OptionType.PE),
                exchange="NFO",
                strike=short_strike,
                option_type=OptionType.PE,
                transaction_type=TransactionType.SELL,
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

        Max profit = spread width - net debit.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum profit
        """
        if len(legs) < 2:
            return Decimal("0")

        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        spread_width = long_leg.strike - short_leg.strike  # PE spread
        net_debit = Decimal("0")

        if long_leg.entry_price and short_leg.entry_price:
            net_debit = long_leg.entry_price - short_leg.entry_price

        profit_per_share = spread_width - net_debit
        return profit_per_share * Decimal(str(long_leg.total_quantity))

    def calculate_max_loss(
        self,
        legs: list[StrategyLeg],
    ) -> Decimal:
        """Calculate maximum loss.

        Max loss = net debit paid.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Maximum loss
        """
        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return Decimal("0")

        if not long_leg.entry_price or not short_leg.entry_price:
            return Decimal("0")

        net_debit = long_leg.entry_price - short_leg.entry_price
        return net_debit * Decimal(str(long_leg.total_quantity))

    def calculate_breakevens(
        self,
        legs: list[StrategyLeg],
    ) -> tuple[Decimal | None, Decimal | None]:
        """Calculate breakeven point.

        BE = long strike - net debit.

        Args:
            legs: Strategy legs with entry prices

        Returns:
            Tuple of (lower_breakeven, upper_breakeven)
        """
        long_leg = next((l for l in legs if l.is_long), None)
        short_leg = next((l for l in legs if l.is_short), None)

        if not long_leg or not short_leg:
            return None, None

        if not long_leg.entry_price or not short_leg.entry_price:
            return None, None

        net_debit = long_leg.entry_price - short_leg.entry_price
        breakeven = long_leg.strike - net_debit

        # Bear put spread has single breakeven
        return None, breakeven
