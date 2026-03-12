"""Slippage model for paper trading.

Simulates realistic slippage based on:
- ATM options: 0.5-1.5 pts
- OTM (>200pts away): 1.5-4 pts
- Market orders: 2x slippage
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class OptionMoneyness(str, Enum):
    """Option moneyness classification."""

    ITM = "ITM"  # In the Money
    ATM = "ATM"  # At the Money
    OTM = "OTM"  # Out of the Money
    DEEP_OTM = "DEEP_OTM"  # Deep Out of the Money (>200pts)


@dataclass
class SlippageParams:
    """Slippage model parameters."""

    # Base slippage in points for different moneyness
    atm_base_min: Decimal = Decimal("0.5")
    atm_base_max: Decimal = Decimal("1.5")
    otm_base_min: Decimal = Decimal("1.0")
    otm_base_max: Decimal = Decimal("2.5")
    deep_otm_base_min: Decimal = Decimal("1.5")
    deep_otm_base_max: Decimal = Decimal("4.0")
    itm_base_min: Decimal = Decimal("0.75")
    itm_base_max: Decimal = Decimal("2.0")

    # Market order multiplier
    market_order_multiplier: Decimal = Decimal("2.0")

    # Size impact (slippage increases with size relative to avg volume)
    size_impact_threshold: Decimal = Decimal("0.01")  # 1% of avg daily volume
    size_impact_factor: Decimal = Decimal("0.5")  # Additional slippage per % above threshold

    # OTM distance threshold in points
    deep_otm_threshold: int = 200

    # ATM range (±points from spot to be considered ATM)
    atm_range: int = 50


class SlippageModel:
    """Simulates realistic slippage for paper trading."""

    def __init__(self, params: SlippageParams | None = None) -> None:
        """Initialize slippage model.

        Args:
            params: Slippage parameters
        """
        self._params = params or SlippageParams()
        self._avg_volumes: dict[str, int] = {}

    def set_avg_volume(self, symbol: str, avg_volume: int) -> None:
        """Set average daily volume for a symbol.

        Args:
            symbol: Trading symbol
            avg_volume: Average daily volume
        """
        self._avg_volumes[symbol] = avg_volume

    def classify_moneyness(
        self,
        spot_price: Decimal,
        strike_price: Decimal,
        is_call: bool,
    ) -> OptionMoneyness:
        """Classify option moneyness.

        Args:
            spot_price: Current spot price
            strike_price: Option strike price
            is_call: True if call option

        Returns:
            Option moneyness classification
        """
        distance = abs(strike_price - spot_price)
        atm_range = Decimal(self._params.atm_range)
        deep_otm_threshold = Decimal(self._params.deep_otm_threshold)

        # Check if ATM
        if distance <= atm_range:
            return OptionMoneyness.ATM

        # Determine ITM/OTM based on option type
        if is_call:
            is_itm = strike_price < spot_price
        else:
            is_itm = strike_price > spot_price

        if is_itm:
            return OptionMoneyness.ITM

        # OTM - check if deep OTM
        if distance > deep_otm_threshold:
            return OptionMoneyness.DEEP_OTM

        return OptionMoneyness.OTM

    def get_base_slippage_range(
        self, moneyness: OptionMoneyness
    ) -> tuple[Decimal, Decimal]:
        """Get base slippage range for moneyness.

        Args:
            moneyness: Option moneyness

        Returns:
            Tuple of (min_slippage, max_slippage) in points
        """
        params = self._params

        if moneyness == OptionMoneyness.ATM:
            return params.atm_base_min, params.atm_base_max
        elif moneyness == OptionMoneyness.OTM:
            return params.otm_base_min, params.otm_base_max
        elif moneyness == OptionMoneyness.DEEP_OTM:
            return params.deep_otm_base_min, params.deep_otm_base_max
        else:  # ITM
            return params.itm_base_min, params.itm_base_max

    def calculate_size_impact(
        self,
        symbol: str,
        quantity: int,
        lot_size: int,
    ) -> Decimal:
        """Calculate additional slippage from order size.

        Args:
            symbol: Trading symbol
            quantity: Order quantity (in lots)
            lot_size: Lot size

        Returns:
            Additional slippage in points
        """
        avg_volume = self._avg_volumes.get(symbol, 0)
        if avg_volume == 0:
            return Decimal("0")

        total_quantity = quantity * lot_size
        volume_pct = Decimal(str(total_quantity)) / Decimal(str(avg_volume))

        if volume_pct <= self._params.size_impact_threshold:
            return Decimal("0")

        excess_pct = volume_pct - self._params.size_impact_threshold
        return excess_pct * self._params.size_impact_factor * Decimal("100")

    def calculate(
        self,
        symbol: str,
        quantity: int,
        lot_size: int,
        is_market_order: bool,
        spot_price: Decimal,
        strike_price: Decimal | None = None,
        is_call: bool | None = None,
        is_buy: bool = True,
    ) -> Decimal:
        """Calculate slippage for an order.

        Args:
            symbol: Trading symbol
            quantity: Order quantity (in lots)
            lot_size: Lot size
            is_market_order: True if market order
            spot_price: Current spot price
            strike_price: Option strike price (if option)
            is_call: True if call option (if option)
            is_buy: True if buy order

        Returns:
            Slippage in points (positive = adverse price impact)
        """
        # Determine moneyness if option
        if strike_price is not None and is_call is not None:
            moneyness = self.classify_moneyness(spot_price, strike_price, is_call)
        else:
            # Assume ATM for futures/index
            moneyness = OptionMoneyness.ATM

        # Get base slippage range
        min_slip, max_slip = self.get_base_slippage_range(moneyness)

        # Random slippage within range
        base_slippage = Decimal(str(random.uniform(float(min_slip), float(max_slip))))

        # Apply market order multiplier
        if is_market_order:
            base_slippage *= self._params.market_order_multiplier

        # Add size impact
        size_impact = self.calculate_size_impact(symbol, quantity, lot_size)
        total_slippage = base_slippage + size_impact

        # Round to tick size (0.05)
        total_slippage = (total_slippage / Decimal("0.05")).quantize(
            Decimal("1")
        ) * Decimal("0.05")

        logger.debug(
            "slippage_calculated",
            symbol=symbol,
            moneyness=moneyness.value,
            base_slippage=str(base_slippage),
            size_impact=str(size_impact),
            total_slippage=str(total_slippage),
            is_market_order=is_market_order,
        )

        return total_slippage

    def apply_slippage(
        self,
        market_price: Decimal,
        slippage: Decimal,
        is_buy: bool,
    ) -> Decimal:
        """Apply slippage to market price.

        Args:
            market_price: Current market price
            slippage: Slippage in points
            is_buy: True if buy order

        Returns:
            Fill price after slippage
        """
        if is_buy:
            # Buy order gets worse (higher) price
            return market_price + slippage
        else:
            # Sell order gets worse (lower) price
            return market_price - slippage
