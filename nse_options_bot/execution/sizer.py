"""Position sizer using Kelly criterion.

Options-adjusted Kelly sizing with max-loss capping per leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from nse_options_bot.market.regime import MarketRegime
from nse_options_bot.strategies.base_strategy import StrategyType

logger = structlog.get_logger(__name__)


@dataclass
class SizingResult:
    """Position sizing result."""

    lots: int
    total_quantity: int
    capital_used: Decimal
    max_loss: Decimal
    margin_required: Decimal
    kelly_fraction: float
    applied_fraction: float
    adjustments: list[str]


class PositionSizer:
    """Kelly-based position sizing for options.

    Implements:
    - Kelly criterion for optimal sizing
    - Max loss capping per trade
    - Regime-based adjustments
    - VIX-based adjustments
    """

    # Kelly scaling
    KELLY_FRACTION = 0.25  # Use 25% of full Kelly (quarter Kelly)
    MAX_KELLY = 0.5  # Cap Kelly at 50%
    MIN_LOTS = 1

    # Default probabilities (from backtesting)
    DEFAULT_WIN_RATES = {
        StrategyType.SHORT_STRADDLE: 0.65,
        StrategyType.IRON_CONDOR: 0.70,
        StrategyType.BULL_CALL_SPREAD: 0.50,
        StrategyType.BEAR_PUT_SPREAD: 0.50,
        StrategyType.LONG_STRADDLE: 0.35,
    }

    # Default reward/risk ratios
    DEFAULT_RR_RATIOS = {
        StrategyType.SHORT_STRADDLE: 0.8,  # Risk 1 to make 0.8
        StrategyType.IRON_CONDOR: 0.5,  # Risk 1 to make 0.5
        StrategyType.BULL_CALL_SPREAD: 1.5,  # Risk 1 to make 1.5
        StrategyType.BEAR_PUT_SPREAD: 1.5,
        StrategyType.LONG_STRADDLE: 2.0,  # Risk 1 to make 2
    }

    def __init__(
        self,
        capital: Decimal,
        max_loss_pct_per_trade: float = 2.0,
        max_capital_pct_per_trade: float = 10.0,
    ) -> None:
        """Initialize sizer.

        Args:
            capital: Total trading capital
            max_loss_pct_per_trade: Max loss as % of capital per trade
            max_capital_pct_per_trade: Max capital deployment per trade
        """
        self._capital = capital
        self._max_loss_pct = max_loss_pct_per_trade
        self._max_capital_pct = max_capital_pct_per_trade

    def calculate_kelly(
        self,
        win_rate: float,
        reward_risk_ratio: float,
    ) -> float:
        """Calculate Kelly fraction.

        Kelly formula: f* = (p * b - q) / b
        where p = win probability, q = loss probability, b = win/loss ratio

        Args:
            win_rate: Win probability (0-1)
            reward_risk_ratio: Expected win / Expected loss

        Returns:
            Kelly fraction (0-1)
        """
        if win_rate <= 0 or win_rate >= 1:
            return 0.0

        if reward_risk_ratio <= 0:
            return 0.0

        p = win_rate
        q = 1 - p
        b = reward_risk_ratio

        kelly = (p * b - q) / b

        # Kelly can be negative if edge is negative
        if kelly <= 0:
            return 0.0

        # Apply fraction and cap
        kelly *= self.KELLY_FRACTION
        kelly = min(kelly, self.MAX_KELLY)

        return kelly

    def size_position(
        self,
        strategy_type: StrategyType,
        max_loss_per_lot: Decimal,
        margin_per_lot: Decimal,
        lot_size: int,
        win_rate: float | None = None,
        reward_risk_ratio: float | None = None,
        regime: MarketRegime | None = None,
        vix: float = 0.0,
        days_to_expiry: float = 7.0,
    ) -> SizingResult:
        """Calculate optimal position size.

        Args:
            strategy_type: Strategy type
            max_loss_per_lot: Max loss per lot
            margin_per_lot: Margin required per lot
            lot_size: Lot size
            win_rate: Win probability (uses default if None)
            reward_risk_ratio: RR ratio (uses default if None)
            regime: Current market regime
            vix: Current VIX level
            days_to_expiry: DTE

        Returns:
            SizingResult
        """
        adjustments = []

        # Use defaults if not provided
        if win_rate is None:
            win_rate = self.DEFAULT_WIN_RATES.get(strategy_type, 0.5)
        if reward_risk_ratio is None:
            reward_risk_ratio = self.DEFAULT_RR_RATIOS.get(strategy_type, 1.0)

        # Calculate Kelly fraction
        kelly = self.calculate_kelly(win_rate, reward_risk_ratio)

        # Apply regime adjustment
        regime_mult = 1.0
        if regime:
            regime_mult = self._get_regime_multiplier(regime)
            if regime_mult != 1.0:
                adjustments.append(f"Regime ({regime.name}): {regime_mult:.1f}x")

        # Apply VIX adjustment
        vix_mult = 1.0
        if vix > 0:
            vix_mult = self._get_vix_multiplier(vix)
            if vix_mult != 1.0:
                adjustments.append(f"VIX ({vix:.1f}): {vix_mult:.1f}x")

        # Apply DTE adjustment
        dte_mult = 1.0
        if days_to_expiry < 2:
            dte_mult = 0.5
            adjustments.append(f"DTE ({days_to_expiry:.1f}): {dte_mult:.1f}x")

        # Apply all multipliers
        applied_fraction = kelly * regime_mult * vix_mult * dte_mult

        # Calculate max lots from Kelly
        kelly_capital = self._capital * Decimal(str(applied_fraction))
        kelly_lots = int(kelly_capital / margin_per_lot) if margin_per_lot > 0 else 0

        # Calculate max lots from loss limit
        max_loss_budget = self._capital * Decimal(str(self._max_loss_pct / 100))
        loss_limit_lots = int(max_loss_budget / max_loss_per_lot) if max_loss_per_lot > 0 else 0

        # Calculate max lots from capital limit
        max_capital = self._capital * Decimal(str(self._max_capital_pct / 100))
        capital_limit_lots = int(max_capital / margin_per_lot) if margin_per_lot > 0 else 0

        # Take minimum of all limits
        lots = min(kelly_lots, loss_limit_lots, capital_limit_lots)
        lots = max(lots, self.MIN_LOTS)  # At least 1 lot

        # Log which limit was binding
        if lots == kelly_lots:
            adjustments.append("Kelly limited")
        elif lots == loss_limit_lots:
            adjustments.append("Loss limit capped")
        elif lots == capital_limit_lots:
            adjustments.append("Capital limit capped")

        # Calculate final values
        total_quantity = lots * lot_size
        margin_required = margin_per_lot * Decimal(str(lots))
        max_loss = max_loss_per_lot * Decimal(str(lots))
        capital_used = margin_required

        logger.info(
            "position_sized",
            strategy=strategy_type.value,
            lots=lots,
            kelly=kelly,
            applied_fraction=applied_fraction,
            margin=float(margin_required),
            max_loss=float(max_loss),
            adjustments=adjustments,
        )

        return SizingResult(
            lots=lots,
            total_quantity=total_quantity,
            capital_used=capital_used,
            max_loss=max_loss,
            margin_required=margin_required,
            kelly_fraction=kelly,
            applied_fraction=applied_fraction,
            adjustments=adjustments,
        )

    def _get_regime_multiplier(self, regime: MarketRegime) -> float:
        """Get position size multiplier for regime.

        Args:
            regime: Market regime

        Returns:
            Multiplier
        """
        multipliers = {
            MarketRegime.RANGE_BOUND: 1.0,
            MarketRegime.TRENDING_UP: 0.7,
            MarketRegime.TRENDING_DOWN: 0.7,
            MarketRegime.HIGH_VOLATILITY: 0.4,
        }
        return multipliers.get(regime, 1.0)

    def _get_vix_multiplier(self, vix: float) -> float:
        """Get position size multiplier for VIX.

        Args:
            vix: VIX level

        Returns:
            Multiplier
        """
        if vix > 30:
            return 0.3
        elif vix > 25:
            return 0.5
        elif vix > 20:
            return 0.7
        elif vix > 18:
            return 0.85
        return 1.0

    def adjust_for_event(
        self,
        sizing: SizingResult,
        event_impact: str,
        hours_to_event: float,
    ) -> SizingResult:
        """Adjust sizing for upcoming event.

        Args:
            sizing: Original sizing
            event_impact: "HIGH", "MEDIUM", "LOW"
            hours_to_event: Hours until event

        Returns:
            Adjusted SizingResult
        """
        if event_impact != "HIGH":
            return sizing

        if hours_to_event > 24:
            return sizing

        # Reduce size for high-impact events
        multiplier = 0.5 if hours_to_event < 6 else 0.7

        adjusted_lots = max(1, int(sizing.lots * multiplier))

        adjustments = sizing.adjustments + [
            f"Event in {hours_to_event:.0f}h: {multiplier:.1f}x"
        ]

        return SizingResult(
            lots=adjusted_lots,
            total_quantity=adjusted_lots * (sizing.total_quantity // sizing.lots),
            capital_used=sizing.capital_used * Decimal(str(adjusted_lots / sizing.lots)),
            max_loss=sizing.max_loss * Decimal(str(adjusted_lots / sizing.lots)),
            margin_required=sizing.margin_required * Decimal(str(adjusted_lots / sizing.lots)),
            kelly_fraction=sizing.kelly_fraction,
            applied_fraction=sizing.applied_fraction * multiplier,
            adjustments=adjustments,
        )


def quick_size(
    capital: Decimal,
    strategy_type: StrategyType,
    max_loss_per_lot: Decimal,
    margin_per_lot: Decimal,
    lot_size: int,
) -> int:
    """Quick sizing function.

    Args:
        capital: Trading capital
        strategy_type: Strategy type
        max_loss_per_lot: Max loss per lot
        margin_per_lot: Margin per lot
        lot_size: Lot size

    Returns:
        Number of lots
    """
    sizer = PositionSizer(capital)
    result = sizer.size_position(
        strategy_type=strategy_type,
        max_loss_per_lot=max_loss_per_lot,
        margin_per_lot=margin_per_lot,
        lot_size=lot_size,
    )
    return result.lots
