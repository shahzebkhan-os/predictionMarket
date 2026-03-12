"""OI Analysis Signal (Signal 1).

PCR>1.5 rising → contrarian bullish. PCR<0.7 falling → contrarian bearish.
OI buildup: CE OI rising at resistance + price falling → strong wall.
Max OI wall distance from current price → available range.
Score = pcr_score×0.35 + oi_buildup×0.40 + wall_distance×0.25
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from nse_options_bot.market.option_chain import OptionChainSnapshot
from nse_options_bot.signals.engine import Signal, SignalDirection, SignalType, create_signal

logger = structlog.get_logger(__name__)


@dataclass
class OIBuildup:
    """OI buildup analysis."""

    ce_buildup_at_resistance: bool = False
    pe_buildup_at_support: bool = False
    ce_unwinding: bool = False
    pe_unwinding: bool = False
    buildup_score: float = 0.0  # -1 to +1


@dataclass
class OIWalls:
    """OI wall levels."""

    max_ce_oi_strike: Decimal
    max_pe_oi_strike: Decimal
    ce_wall_distance: float  # Distance from spot as %
    pe_wall_distance: float
    available_range: float  # % range between walls


class OIAnalyzer:
    """Open Interest analyzer.

    Analyzes PCR, OI buildup, and OI walls.
    """

    # PCR thresholds
    PCR_BULLISH_THRESHOLD = 1.5  # Contrarian bullish
    PCR_BEARISH_THRESHOLD = 0.7  # Contrarian bearish
    PCR_NEUTRAL_LOW = 0.8
    PCR_NEUTRAL_HIGH = 1.2

    # OI change thresholds
    SIGNIFICANT_OI_CHANGE_PCT = 5.0  # 5% change is significant

    # Weight factors
    PCR_WEIGHT = 0.35
    BUILDUP_WEIGHT = 0.40
    WALL_DISTANCE_WEIGHT = 0.25

    def __init__(self) -> None:
        """Initialize OI analyzer."""
        self._prev_pcr: float | None = None
        self._prev_ce_oi: dict[Decimal, int] = {}
        self._prev_pe_oi: dict[Decimal, int] = {}

    def analyze(
        self,
        chain: OptionChainSnapshot,
        price_change_pct: float = 0.0,
    ) -> Signal:
        """Analyze OI data.

        Args:
            chain: Option chain snapshot
            price_change_pct: Price change since last analysis (%)

        Returns:
            OI analysis signal
        """
        pcr = chain.get_pcr()
        spot = float(chain.spot_price)

        # Analyze PCR
        pcr_score, pcr_reason = self._analyze_pcr(pcr)

        # Analyze OI buildup
        buildup = self._analyze_buildup(chain, price_change_pct)

        # Analyze OI walls
        walls = self._analyze_walls(chain, spot)

        # Calculate wall distance score
        wall_score = self._calculate_wall_score(walls, spot)

        # Composite score
        composite_score = (
            pcr_score * self.PCR_WEIGHT
            + buildup.buildup_score * self.BUILDUP_WEIGHT
            + wall_score * self.WALL_DISTANCE_WEIGHT
        )

        # Calculate confidence
        confidence = self._calculate_confidence(pcr, buildup, walls)

        # Generate reason
        reasons = [pcr_reason]
        if abs(buildup.buildup_score) > 0.3:
            if buildup.ce_buildup_at_resistance:
                reasons.append("CE OI buildup at resistance (bearish)")
            if buildup.pe_buildup_at_support:
                reasons.append("PE OI buildup at support (bullish)")
            if buildup.ce_unwinding:
                reasons.append("CE unwinding (bullish)")
            if buildup.pe_unwinding:
                reasons.append("PE unwinding (bearish)")

        reasons.append(f"Range: {walls.available_range:.1f}% between walls")

        # Store current values for next comparison
        self._prev_pcr = pcr

        return create_signal(
            signal_type=SignalType.OI_ANALYSIS,
            score=composite_score,
            confidence=confidence,
            reason=" | ".join(reasons),
            components={
                "pcr": pcr,
                "pcr_score": pcr_score,
                "buildup_score": buildup.buildup_score,
                "wall_score": wall_score,
                "ce_wall_distance": walls.ce_wall_distance,
                "pe_wall_distance": walls.pe_wall_distance,
                "available_range": walls.available_range,
            },
        )

    def _analyze_pcr(self, pcr: float) -> tuple[float, str]:
        """Analyze Put-Call Ratio.

        Args:
            pcr: Current PCR

        Returns:
            Tuple of (score, reason)
        """
        # Check PCR trend if we have previous value
        pcr_rising = False
        pcr_falling = False
        if self._prev_pcr is not None:
            pcr_rising = pcr > self._prev_pcr * 1.02  # 2% rise
            pcr_falling = pcr < self._prev_pcr * 0.98  # 2% fall

        if pcr >= self.PCR_BULLISH_THRESHOLD:
            if pcr_rising:
                return 0.8, f"PCR {pcr:.2f} > 1.5, rising → Strong contrarian bullish"
            return 0.5, f"PCR {pcr:.2f} > 1.5 → Contrarian bullish"

        elif pcr <= self.PCR_BEARISH_THRESHOLD:
            if pcr_falling:
                return -0.8, f"PCR {pcr:.2f} < 0.7, falling → Strong contrarian bearish"
            return -0.5, f"PCR {pcr:.2f} < 0.7 → Contrarian bearish"

        elif self.PCR_NEUTRAL_LOW <= pcr <= self.PCR_NEUTRAL_HIGH:
            return 0.0, f"PCR {pcr:.2f} → Neutral"

        elif pcr > self.PCR_NEUTRAL_HIGH:
            return 0.25, f"PCR {pcr:.2f} → Slightly bullish"

        else:
            return -0.25, f"PCR {pcr:.2f} → Slightly bearish"

    def _analyze_buildup(
        self, chain: OptionChainSnapshot, price_change_pct: float
    ) -> OIBuildup:
        """Analyze OI buildup patterns.

        Args:
            chain: Option chain snapshot
            price_change_pct: Price change %

        Returns:
            OIBuildup analysis
        """
        buildup = OIBuildup()
        atm = chain.get_atm_strike()
        spot = float(chain.spot_price)

        # Get max OI strikes (resistance/support)
        max_ce_strike, max_pe_strike = chain.get_max_oi_strikes()

        # Analyze OI changes at key strikes
        ce_oi_change = 0
        pe_oi_change = 0
        total_ce_oi = 0
        total_pe_oi = 0

        for strike, strike_data in chain.iter_strikes():
            if strike_data.ce:
                total_ce_oi += strike_data.ce.oi
                ce_oi_change += strike_data.ce.oi_change

                # Check CE buildup at resistance (above spot)
                if strike == max_ce_strike and strike > atm:
                    if strike_data.ce.oi_change > 0:
                        buildup.ce_buildup_at_resistance = True

            if strike_data.pe:
                total_pe_oi += strike_data.pe.oi
                pe_oi_change += strike_data.pe.oi_change

                # Check PE buildup at support (below spot)
                if strike == max_pe_strike and strike < atm:
                    if strike_data.pe.oi_change > 0:
                        buildup.pe_buildup_at_support = True

        # Check for unwinding
        if total_ce_oi > 0:
            ce_change_pct = (ce_oi_change / total_ce_oi) * 100
            if ce_change_pct < -self.SIGNIFICANT_OI_CHANGE_PCT:
                buildup.ce_unwinding = True

        if total_pe_oi > 0:
            pe_change_pct = (pe_oi_change / total_pe_oi) * 100
            if pe_change_pct < -self.SIGNIFICANT_OI_CHANGE_PCT:
                buildup.pe_unwinding = True

        # Calculate buildup score
        score = 0.0

        # CE buildup at resistance + price falling = bearish signal
        if buildup.ce_buildup_at_resistance and price_change_pct < 0:
            score -= 0.5

        # PE buildup at support + price rising = bullish signal
        if buildup.pe_buildup_at_support and price_change_pct > 0:
            score += 0.5

        # CE unwinding = bullish
        if buildup.ce_unwinding:
            score += 0.3

        # PE unwinding = bearish
        if buildup.pe_unwinding:
            score -= 0.3

        buildup.buildup_score = max(-1.0, min(1.0, score))
        return buildup

    def _analyze_walls(
        self, chain: OptionChainSnapshot, spot: float
    ) -> OIWalls:
        """Analyze OI walls.

        Args:
            chain: Option chain snapshot
            spot: Current spot price

        Returns:
            OIWalls analysis
        """
        max_ce_strike, max_pe_strike = chain.get_max_oi_strikes()

        ce_wall_distance = (float(max_ce_strike) - spot) / spot * 100
        pe_wall_distance = (spot - float(max_pe_strike)) / spot * 100
        available_range = ce_wall_distance + pe_wall_distance

        return OIWalls(
            max_ce_oi_strike=max_ce_strike,
            max_pe_oi_strike=max_pe_strike,
            ce_wall_distance=ce_wall_distance,
            pe_wall_distance=pe_wall_distance,
            available_range=available_range,
        )

    def _calculate_wall_score(self, walls: OIWalls, spot: float) -> float:
        """Calculate score from wall distances.

        Args:
            walls: OI walls analysis
            spot: Current spot

        Returns:
            Score -1 to +1
        """
        # If closer to CE wall, bearish bias
        # If closer to PE wall, bullish bias
        if walls.available_range == 0:
            return 0.0

        # Relative position between walls
        ce_distance = walls.ce_wall_distance
        pe_distance = walls.pe_wall_distance

        # Score based on position
        # Closer to CE wall (resistance) = more bearish pressure
        # Closer to PE wall (support) = more bullish support
        relative_pos = (pe_distance - ce_distance) / (walls.available_range)

        return relative_pos * 0.5  # Scale to ±0.5 max

    def _calculate_confidence(
        self, pcr: float, buildup: OIBuildup, walls: OIWalls
    ) -> float:
        """Calculate confidence level.

        Args:
            pcr: Current PCR
            buildup: OI buildup analysis
            walls: OI walls analysis

        Returns:
            Confidence 0-1
        """
        confidence = 0.5  # Base confidence

        # Strong PCR signals increase confidence
        if pcr >= self.PCR_BULLISH_THRESHOLD or pcr <= self.PCR_BEARISH_THRESHOLD:
            confidence += 0.2

        # Clear buildup patterns increase confidence
        if abs(buildup.buildup_score) > 0.5:
            confidence += 0.15

        # Wide range between walls increases confidence
        if walls.available_range > 3.0:  # >3% range
            confidence += 0.1

        # Agreement between signals increases confidence
        if (buildup.buildup_score > 0.3 and pcr > 1.2) or (
            buildup.buildup_score < -0.3 and pcr < 0.8
        ):
            confidence += 0.1

        return min(1.0, confidence)

    def get_oi_summary(self, chain: OptionChainSnapshot) -> dict[str, Any]:
        """Get OI summary.

        Args:
            chain: Option chain snapshot

        Returns:
            Summary dict
        """
        pcr = chain.get_pcr()
        max_ce_strike, max_pe_strike = chain.get_max_oi_strikes()
        ce_oi, pe_oi = chain.get_oi_at_strike(max_ce_strike)
        _, pe_oi_at_support = chain.get_oi_at_strike(max_pe_strike)

        return {
            "pcr": pcr,
            "max_ce_oi_strike": float(max_ce_strike),
            "max_ce_oi": ce_oi,
            "max_pe_oi_strike": float(max_pe_strike),
            "max_pe_oi": pe_oi_at_support,
            "spot_price": float(chain.spot_price),
            "atm_strike": float(chain.get_atm_strike()),
        }
