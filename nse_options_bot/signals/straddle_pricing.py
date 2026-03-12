"""Straddle Pricing Signal (Signal 9).

ATM straddle = ATM CE LTP + ATM PE LTP.
Expected move% = straddle/spot × 100.
If implied_move > HV20 × sqrt(DTE/252) → options overpriced → sell straddle.
Upper/lower breakeven = ATM ± straddle price → magnet levels during expiry week.
Straddle should decay ~20-25%/day on theta (DTE 3→0).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import structlog

from nse_options_bot.market.option_chain import OptionChainSnapshot
from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)


@dataclass
class StraddleMetrics:
    """Straddle pricing metrics."""

    straddle_price: Decimal
    expected_move_pct: float
    upper_breakeven: Decimal
    lower_breakeven: Decimal
    implied_vs_hv_ratio: float
    is_overpriced: bool
    expected_theta_decay_pct: float
    current_theta_decay_pct: float | None


class StraddlePricingAnalyzer:
    """Straddle pricing and expected move analyzer."""

    # Thresholds
    OVERPRICED_RATIO = 1.2  # Implied > 1.2x HV = overpriced
    UNDERPRICED_RATIO = 0.8  # Implied < 0.8x HV = underpriced

    # Expected theta decay rates by DTE
    THETA_DECAY_RATES = {
        0: 1.0,  # Expiry day - full decay
        1: 0.35,  # 1 DTE - 35% decay
        2: 0.25,  # 2 DTE - 25% decay
        3: 0.20,  # 3 DTE - 20% decay
    }

    def __init__(self) -> None:
        """Initialize analyzer."""
        self._prev_straddle_price: Decimal | None = None
        self._prev_dte: int | None = None

    def analyze(
        self,
        chain: OptionChainSnapshot,
        dte: int,
        hv20: float | None = None,
    ) -> Signal:
        """Analyze straddle pricing.

        Args:
            chain: Option chain snapshot
            dte: Days to expiry
            hv20: 20-day historical volatility (annualized)

        Returns:
            Straddle pricing signal
        """
        spot = float(chain.spot_price)
        straddle_price = chain.get_straddle_price()
        expected_move_pct = float(straddle_price) / spot * 100

        # Calculate breakevens
        atm_strike = chain.get_atm_strike()
        upper_be = atm_strike + straddle_price
        lower_be = atm_strike - straddle_price

        # Calculate implied vs HV ratio
        implied_vs_hv = 1.0
        is_overpriced = False

        if hv20 and hv20 > 0:
            # Expected move based on HV
            hv_expected_move = hv20 * np.sqrt(dte / 252) * 100
            implied_vs_hv = expected_move_pct / hv_expected_move if hv_expected_move > 0 else 1.0
            is_overpriced = implied_vs_hv >= self.OVERPRICED_RATIO

        # Calculate theta decay
        expected_decay = self.THETA_DECAY_RATES.get(min(dte, 3), 0.15)
        current_decay = self._calculate_actual_decay(straddle_price, dte)

        metrics = StraddleMetrics(
            straddle_price=straddle_price,
            expected_move_pct=expected_move_pct,
            upper_breakeven=upper_be,
            lower_breakeven=lower_be,
            implied_vs_hv_ratio=implied_vs_hv,
            is_overpriced=is_overpriced,
            expected_theta_decay_pct=expected_decay * 100,
            current_theta_decay_pct=current_decay * 100 if current_decay else None,
        )

        # Update tracking
        self._prev_straddle_price = straddle_price
        self._prev_dte = dte

        # Calculate score
        score, confidence, reason = self._calculate_score(metrics, spot, dte, hv20)

        return create_signal(
            signal_type=SignalType.STRADDLE_PRICING,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "straddle_price": float(straddle_price),
                "expected_move_pct": expected_move_pct,
                "upper_breakeven": float(upper_be),
                "lower_breakeven": float(lower_be),
                "implied_vs_hv": implied_vs_hv,
                "is_overpriced": is_overpriced,
                "expected_decay_pct": expected_decay * 100,
                "dte": dte,
            },
        )

    def _calculate_actual_decay(
        self, current_price: Decimal, current_dte: int
    ) -> float | None:
        """Calculate actual theta decay from previous day.

        Args:
            current_price: Current straddle price
            current_dte: Current DTE

        Returns:
            Decay percentage or None
        """
        if (
            self._prev_straddle_price is None
            or self._prev_dte is None
            or self._prev_dte <= current_dte  # No day passed
        ):
            return None

        if self._prev_straddle_price == 0:
            return None

        decay = float(
            (self._prev_straddle_price - current_price) / self._prev_straddle_price
        )
        return decay

    def _calculate_score(
        self,
        metrics: StraddleMetrics,
        spot: float,
        dte: int,
        hv20: float | None,
    ) -> tuple[float, float, str]:
        """Calculate straddle pricing score.

        This is primarily a premium selling/buying signal.
        Positive score = sell premium opportunity.
        Negative score = buy premium opportunity.

        Args:
            metrics: Straddle metrics
            spot: Spot price
            dte: Days to expiry
            hv20: Historical volatility

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # Overpriced/underpriced analysis
        if metrics.is_overpriced:
            # Options overpriced - sell straddle opportunity
            score -= 0.4  # Negative for premium selling
            reasons.append(
                f"Options overpriced (IV/HV: {metrics.implied_vs_hv_ratio:.2f}x) → Sell straddle"
            )
            confidence += 0.15

        elif metrics.implied_vs_hv_ratio <= self.UNDERPRICED_RATIO:
            # Options underpriced - buy straddle opportunity
            score += 0.4  # Positive for premium buying
            reasons.append(
                f"Options underpriced (IV/HV: {metrics.implied_vs_hv_ratio:.2f}x) → Buy straddle"
            )
            confidence += 0.1

        # Expected move analysis
        reasons.append(
            f"Expected move: {metrics.expected_move_pct:.1f}% "
            f"({float(metrics.lower_breakeven):.0f}-{float(metrics.upper_breakeven):.0f})"
        )

        # Theta decay analysis
        if metrics.current_theta_decay_pct is not None:
            if metrics.current_theta_decay_pct > metrics.expected_theta_decay_pct * 1.2:
                # Faster than expected decay - IV crush
                score -= 0.2
                reasons.append(
                    f"IV crush detected (decay: {metrics.current_theta_decay_pct:.0f}% vs expected {metrics.expected_theta_decay_pct:.0f}%)"
                )
            elif metrics.current_theta_decay_pct < metrics.expected_theta_decay_pct * 0.5:
                # Slower decay - vol expansion
                score += 0.1
                reasons.append("Slow theta decay → Vol expansion")

        # DTE-based adjustments
        if dte <= 2:
            # Near expiry - straddle is magnet for price
            confidence += 0.1
            reasons.append(f"DTE {dte} - Breakevens are magnets")

        reason = " | ".join(reasons)

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def get_breakeven_levels(
        self, chain: OptionChainSnapshot
    ) -> dict[str, Any]:
        """Get breakeven levels for current straddle.

        Args:
            chain: Option chain snapshot

        Returns:
            Breakeven levels dict
        """
        spot = float(chain.spot_price)
        straddle = float(chain.get_straddle_price())
        atm = float(chain.get_atm_strike())

        upper_be = atm + straddle
        lower_be = atm - straddle

        return {
            "spot": spot,
            "atm_strike": atm,
            "straddle_price": straddle,
            "upper_breakeven": upper_be,
            "lower_breakeven": lower_be,
            "upper_distance_pct": (upper_be - spot) / spot * 100,
            "lower_distance_pct": (spot - lower_be) / spot * 100,
            "expected_range_pct": straddle / spot * 100,
        }

    def is_spot_near_breakeven(
        self, spot: float, upper_be: float, lower_be: float, threshold_pct: float = 0.3
    ) -> tuple[bool, str]:
        """Check if spot is near breakeven levels.

        Args:
            spot: Current spot
            upper_be: Upper breakeven
            lower_be: Lower breakeven
            threshold_pct: Proximity threshold

        Returns:
            Tuple of (is_near, which_level)
        """
        upper_distance_pct = abs(upper_be - spot) / spot * 100
        lower_distance_pct = abs(spot - lower_be) / spot * 100

        if upper_distance_pct <= threshold_pct:
            return True, "upper"
        elif lower_distance_pct <= threshold_pct:
            return True, "lower"

        return False, "none"
