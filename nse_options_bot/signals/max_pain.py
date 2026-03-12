"""Max Pain & GEX Analysis (Signal 3).

Max Pain = strike minimizing total option writers' loss.
Near expiry (DTE<2), index gravitates to max pain.
GEX = Σ(CE_gamma × CE_OI − PE_gamma × PE_OI) × spot × lot_size
Positive GEX → range-bound. Negative → trending/volatile.
GEX sign flip → volatility expansion signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from nse_options_bot.market.option_chain import OptionChainSnapshot
from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)


@dataclass
class MaxPainAnalysis:
    """Max pain analysis result."""

    max_pain_strike: Decimal
    distance_from_spot_pct: float
    attraction_score: float  # How strongly spot is attracted to max pain
    dte: int


@dataclass
class GEXAnalysis:
    """Gamma Exposure analysis result."""

    total_gex: float
    gex_normalized: float  # Per unit spot
    is_positive: bool
    gex_flip_detected: bool
    flip_direction: str  # "positive_to_negative" or "negative_to_positive"
    high_gamma_strikes: list[Decimal]


class MaxPainGEXAnalyzer:
    """Max Pain and Gamma Exposure analyzer."""

    # Max pain attraction thresholds
    MAX_PAIN_STRONG_ATTRACTION_DTE = 2  # Strong attraction when DTE < 2
    MAX_PAIN_MODERATE_ATTRACTION_DTE = 3
    MAX_PAIN_DISTANCE_THRESHOLD = 1.0  # 1% from spot = strong signal

    # GEX thresholds
    GEX_HIGH_THRESHOLD = 1e9  # High gamma exposure
    GEX_LOW_THRESHOLD = -1e9  # High negative gamma
    GEX_FLIP_THRESHOLD = 0.1  # 10% of magnitude change indicates flip

    def __init__(self) -> None:
        """Initialize analyzer."""
        self._prev_gex: float | None = None
        self._prev_gex_sign: bool | None = None

    def analyze(
        self,
        chain: OptionChainSnapshot,
        dte: int,
    ) -> Signal:
        """Analyze max pain and GEX.

        Args:
            chain: Option chain snapshot
            dte: Days to expiry

        Returns:
            Max pain & GEX signal
        """
        spot = float(chain.spot_price)

        # Max pain analysis
        max_pain = self._analyze_max_pain(chain, dte, spot)

        # GEX analysis
        gex = self._analyze_gex(chain, spot)

        # Calculate composite score
        score, confidence, reason = self._calculate_score(max_pain, gex, spot)

        return create_signal(
            signal_type=SignalType.MAX_PAIN_GEX,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "max_pain_strike": float(max_pain.max_pain_strike),
                "max_pain_distance_pct": max_pain.distance_from_spot_pct,
                "max_pain_attraction": max_pain.attraction_score,
                "gex": gex.total_gex,
                "gex_positive": gex.is_positive,
                "gex_flip": gex.gex_flip_detected,
                "dte": dte,
            },
        )

    def _analyze_max_pain(
        self, chain: OptionChainSnapshot, dte: int, spot: float
    ) -> MaxPainAnalysis:
        """Analyze max pain level.

        Args:
            chain: Option chain
            dte: Days to expiry
            spot: Spot price

        Returns:
            MaxPainAnalysis
        """
        max_pain_strike = chain.get_max_pain()
        distance_pct = ((float(max_pain_strike) - spot) / spot) * 100

        # Calculate attraction score based on DTE and distance
        if dte <= self.MAX_PAIN_STRONG_ATTRACTION_DTE:
            # Strong attraction near expiry
            if abs(distance_pct) <= self.MAX_PAIN_DISTANCE_THRESHOLD:
                attraction = 0.8
            elif abs(distance_pct) <= 2.0:
                attraction = 0.5
            else:
                attraction = 0.3
        elif dte <= self.MAX_PAIN_MODERATE_ATTRACTION_DTE:
            attraction = 0.3 if abs(distance_pct) <= 2.0 else 0.1
        else:
            attraction = 0.1  # Low attraction far from expiry

        return MaxPainAnalysis(
            max_pain_strike=max_pain_strike,
            distance_from_spot_pct=distance_pct,
            attraction_score=attraction,
            dte=dte,
        )

    def _analyze_gex(
        self, chain: OptionChainSnapshot, spot: float
    ) -> GEXAnalysis:
        """Analyze Gamma Exposure.

        Args:
            chain: Option chain
            spot: Spot price

        Returns:
            GEXAnalysis
        """
        gex = chain.get_gex()
        is_positive = gex > 0

        # Check for GEX flip
        gex_flip = False
        flip_direction = ""

        if self._prev_gex_sign is not None:
            if self._prev_gex_sign and not is_positive:
                gex_flip = True
                flip_direction = "positive_to_negative"
            elif not self._prev_gex_sign and is_positive:
                gex_flip = True
                flip_direction = "negative_to_positive"

        # Update previous GEX
        self._prev_gex = gex
        self._prev_gex_sign = is_positive

        # Find high gamma strikes
        high_gamma_strikes = []
        for strike, strike_data in chain._chain.items():
            strike_gex = 0.0
            if strike_data.ce and strike_data.ce.oi > 0:
                strike_gex += strike_data.ce.greeks.gamma * strike_data.ce.oi
            if strike_data.pe and strike_data.pe.oi > 0:
                strike_gex -= strike_data.pe.greeks.gamma * strike_data.pe.oi

            if abs(strike_gex) > abs(gex) * 0.1:  # >10% of total GEX
                high_gamma_strikes.append(strike)

        return GEXAnalysis(
            total_gex=gex,
            gex_normalized=gex / spot if spot > 0 else 0,
            is_positive=is_positive,
            gex_flip_detected=gex_flip,
            flip_direction=flip_direction,
            high_gamma_strikes=sorted(high_gamma_strikes),
        )

    def _calculate_score(
        self,
        max_pain: MaxPainAnalysis,
        gex: GEXAnalysis,
        spot: float,
    ) -> tuple[float, float, str]:
        """Calculate composite score.

        Args:
            max_pain: Max pain analysis
            gex: GEX analysis
            spot: Spot price

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # Max pain direction signal
        if max_pain.dte <= self.MAX_PAIN_STRONG_ATTRACTION_DTE:
            if max_pain.distance_from_spot_pct > 0.5:
                # Max pain above spot - bullish pull
                score += max_pain.attraction_score * 0.5
                reasons.append(
                    f"Max pain {float(max_pain.max_pain_strike):.0f} ({max_pain.distance_from_spot_pct:+.1f}% above) → Bullish pull"
                )
            elif max_pain.distance_from_spot_pct < -0.5:
                # Max pain below spot - bearish pull
                score -= max_pain.attraction_score * 0.5
                reasons.append(
                    f"Max pain {float(max_pain.max_pain_strike):.0f} ({max_pain.distance_from_spot_pct:.1f}% below) → Bearish pull"
                )
            else:
                reasons.append(
                    f"Max pain {float(max_pain.max_pain_strike):.0f} near spot → Neutral"
                )

            confidence += max_pain.attraction_score * 0.2

        # GEX regime signal
        if gex.is_positive:
            # Positive GEX - range-bound expectation
            score *= 0.7  # Reduce directional conviction
            reasons.append("Positive GEX → Range-bound expected")
        else:
            # Negative GEX - trending expected
            score *= 1.3  # Increase directional conviction
            reasons.append("Negative GEX → Trending expected")
            confidence += 0.1

        # GEX flip signal
        if gex.gex_flip_detected:
            if gex.flip_direction == "positive_to_negative":
                # Transitioning to trending
                score += 0.2 if score > 0 else -0.2  # Amplify direction
                reasons.append("GEX flip P→N → Volatility expansion")
                confidence += 0.15
            else:
                # Transitioning to range-bound
                score *= 0.8  # Dampen
                reasons.append("GEX flip N→P → Volatility contraction")
                confidence += 0.1

        reason = " | ".join(reasons) if reasons else "No significant max pain/GEX signal"

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def get_max_pain_levels(
        self, chain: OptionChainSnapshot
    ) -> dict[Decimal, float]:
        """Get pain values at each strike.

        Args:
            chain: Option chain

        Returns:
            Dict of strike to total pain
        """
        pain_levels = {}
        strikes = sorted(chain._chain.keys())

        for test_strike in strikes:
            total_pain = 0.0

            for strike, strike_data in chain._chain.items():
                # CE writer pain
                if strike_data.ce and float(test_strike) > float(strike):
                    ce_pain = (float(test_strike) - float(strike)) * strike_data.ce.oi
                    total_pain += ce_pain

                # PE writer pain
                if strike_data.pe and float(test_strike) < float(strike):
                    pe_pain = (float(strike) - float(test_strike)) * strike_data.pe.oi
                    total_pain += pe_pain

            pain_levels[test_strike] = total_pain

        return pain_levels

    def get_gex_profile(self, chain: OptionChainSnapshot) -> dict[str, Any]:
        """Get GEX profile across strikes.

        Args:
            chain: Option chain

        Returns:
            GEX profile dict
        """
        profile = {}

        for strike, strike_data in sorted(chain._chain.items()):
            ce_gex = 0.0
            pe_gex = 0.0

            if strike_data.ce:
                ce_gex = (
                    strike_data.ce.greeks.gamma
                    * strike_data.ce.oi
                    * strike_data.ce.lot_size
                    * float(chain.spot_price)
                )

            if strike_data.pe:
                pe_gex = (
                    strike_data.pe.greeks.gamma
                    * strike_data.pe.oi
                    * strike_data.pe.lot_size
                    * float(chain.spot_price)
                )

            profile[float(strike)] = {
                "ce_gex": ce_gex,
                "pe_gex": pe_gex,
                "net_gex": ce_gex - pe_gex,
            }

        return profile
