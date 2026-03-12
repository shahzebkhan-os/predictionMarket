"""Greeks Composite Signal (Signal 12).

Aggregate delta: net CE vs PE delta → market lean direction.
Charm: delta decay rate as DTE decreases.
Portfolio net Greeks: if net vega>threshold → too long vol.
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
class AggregateGreeks:
    """Aggregate Greeks across the chain."""

    total_ce_delta: float
    total_pe_delta: float
    net_delta: float  # Market lean
    total_gamma: float
    total_theta: float
    total_vega: float
    delta_imbalance_pct: float  # CE vs PE delta imbalance


@dataclass
class PortfolioGreeks:
    """Portfolio-level Greeks exposure."""

    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    delta_dollars: float  # Delta exposure in INR
    gamma_dollars: float  # Gamma exposure in INR
    theta_per_day: float  # Daily theta decay
    vega_exposure: float  # 1% IV move impact


class GreeksAnalyzer:
    """Greeks composite analyzer.

    Analyzes aggregate market Greeks and portfolio exposure.
    """

    # Thresholds
    DELTA_IMBALANCE_THRESHOLD = 20.0  # 20% imbalance = significant
    HIGH_VEGA_THRESHOLD = 0.5  # High vega exposure ratio
    HIGH_THETA_THRESHOLD = 0.3  # High theta exposure ratio

    # Charm decay acceleration near expiry
    CHARM_ACCELERATION_DTE = 3  # Charm accelerates within 3 DTE

    def __init__(self) -> None:
        """Initialize Greeks analyzer."""
        self._prev_net_delta: float | None = None

    def analyze_chain(
        self,
        chain: OptionChainSnapshot,
        dte: int,
    ) -> Signal:
        """Analyze aggregate Greeks from option chain.

        Args:
            chain: Option chain snapshot
            dte: Days to expiry

        Returns:
            Greeks signal
        """
        spot = float(chain.spot_price)

        # Calculate aggregate Greeks
        agg = self._calculate_aggregate_greeks(chain, spot)

        # Analyze charm effect
        charm_score = self._analyze_charm(dte, agg.net_delta)

        # Calculate score
        score, confidence, reason = self._calculate_score(agg, charm_score, dte)

        # Update tracking
        self._prev_net_delta = agg.net_delta

        return create_signal(
            signal_type=SignalType.GREEKS_COMPOSITE,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "net_delta": agg.net_delta,
                "total_ce_delta": agg.total_ce_delta,
                "total_pe_delta": agg.total_pe_delta,
                "delta_imbalance_pct": agg.delta_imbalance_pct,
                "total_gamma": agg.total_gamma,
                "total_vega": agg.total_vega,
                "charm_score": charm_score,
                "dte": dte,
            },
        )

    def _calculate_aggregate_greeks(
        self, chain: OptionChainSnapshot, spot: float
    ) -> AggregateGreeks:
        """Calculate aggregate Greeks across chain.

        Args:
            chain: Option chain snapshot
            spot: Spot price

        Returns:
            AggregateGreeks
        """
        total_ce_delta = 0.0
        total_pe_delta = 0.0
        total_gamma = 0.0
        total_theta = 0.0
        total_vega = 0.0

        for strike, strike_data in chain.iter_strikes():
            if strike_data.ce and strike_data.ce.oi > 0:
                weight = strike_data.ce.oi * strike_data.ce.lot_size
                total_ce_delta += strike_data.ce.greeks.delta * weight
                total_gamma += strike_data.ce.greeks.gamma * weight
                total_theta += strike_data.ce.greeks.theta * weight
                total_vega += strike_data.ce.greeks.vega * weight

            if strike_data.pe and strike_data.pe.oi > 0:
                weight = strike_data.pe.oi * strike_data.pe.lot_size
                total_pe_delta += abs(strike_data.pe.greeks.delta) * weight  # PE delta is negative
                total_gamma += strike_data.pe.greeks.gamma * weight
                total_theta += strike_data.pe.greeks.theta * weight
                total_vega += strike_data.pe.greeks.vega * weight

        # Net delta (CE delta is positive, PE delta adds to bearish lean)
        net_delta = total_ce_delta - total_pe_delta

        # Delta imbalance
        total_delta = total_ce_delta + total_pe_delta
        imbalance_pct = (
            ((total_ce_delta - total_pe_delta) / total_delta * 100)
            if total_delta > 0
            else 0.0
        )

        return AggregateGreeks(
            total_ce_delta=total_ce_delta,
            total_pe_delta=total_pe_delta,
            net_delta=net_delta,
            total_gamma=total_gamma,
            total_theta=total_theta,
            total_vega=total_vega,
            delta_imbalance_pct=imbalance_pct,
        )

    def _analyze_charm(self, dte: int, net_delta: float) -> float:
        """Analyze charm (delta decay) effect.

        Args:
            dte: Days to expiry
            net_delta: Current net delta

        Returns:
            Charm score -1 to +1
        """
        if dte > self.CHARM_ACCELERATION_DTE:
            return 0.0

        # Near expiry, OTM options lose delta rapidly
        # This creates pin risk and acceleration of directional moves

        # If we have previous delta, measure the decay
        if self._prev_net_delta is not None:
            delta_change = net_delta - self._prev_net_delta

            # Rapid delta change indicates charm is significant
            if abs(delta_change) > abs(self._prev_net_delta) * 0.1:  # >10% change
                # Charm is accelerating moves
                return 0.3 if delta_change > 0 else -0.3

        # Base charm effect - OTM options become less valuable
        # This is neutral for direction but indicates increased pin risk
        return 0.0

    def _calculate_score(
        self,
        agg: AggregateGreeks,
        charm_score: float,
        dte: int,
    ) -> tuple[float, float, str]:
        """Calculate Greeks composite score.

        Args:
            agg: Aggregate Greeks
            charm_score: Charm score
            dte: Days to expiry

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # Delta imbalance analysis
        if abs(agg.delta_imbalance_pct) >= self.DELTA_IMBALANCE_THRESHOLD:
            if agg.delta_imbalance_pct > 0:
                # More CE delta = market leaning bullish
                score += min(0.4, agg.delta_imbalance_pct / 50)
                reasons.append(f"CE delta dominance ({agg.delta_imbalance_pct:+.0f}%) → Bullish lean")
            else:
                # More PE delta = market leaning bearish
                score -= min(0.4, abs(agg.delta_imbalance_pct) / 50)
                reasons.append(f"PE delta dominance ({agg.delta_imbalance_pct:.0f}%) → Bearish lean")

            confidence += 0.1

        # Charm effect
        if abs(charm_score) > 0:
            score += charm_score
            if charm_score > 0:
                reasons.append("Charm accelerating bullish")
            else:
                reasons.append("Charm accelerating bearish")
            confidence += 0.1

        # Near expiry gamma analysis
        if dte <= 2 and agg.total_gamma > 0:
            # High gamma near expiry = increased volatility potential
            reasons.append(f"High gamma near expiry → Pin risk")
            confidence += 0.1

        if not reasons:
            reasons.append("Greeks balanced")

        reason = " | ".join(reasons)

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def calculate_portfolio_greeks(
        self,
        positions: list[dict[str, Any]],
        chain: OptionChainSnapshot,
    ) -> PortfolioGreeks:
        """Calculate portfolio-level Greeks.

        Args:
            positions: List of position dicts with symbol, quantity
            chain: Option chain for Greeks lookup

        Returns:
            PortfolioGreeks
        """
        net_delta = 0.0
        net_gamma = 0.0
        net_theta = 0.0
        net_vega = 0.0

        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = pos.get("quantity", 0)  # Positive = long, negative = short

            # Find in chain
            for strike, strike_data in chain._chain.items():
                if strike_data.ce and strike_data.ce.tradingsymbol == symbol:
                    multiplier = qty * strike_data.ce.lot_size
                    net_delta += strike_data.ce.greeks.delta * multiplier
                    net_gamma += strike_data.ce.greeks.gamma * multiplier
                    net_theta += strike_data.ce.greeks.theta * multiplier
                    net_vega += strike_data.ce.greeks.vega * multiplier
                    break

                if strike_data.pe and strike_data.pe.tradingsymbol == symbol:
                    multiplier = qty * strike_data.pe.lot_size
                    net_delta += strike_data.pe.greeks.delta * multiplier
                    net_gamma += strike_data.pe.greeks.gamma * multiplier
                    net_theta += strike_data.pe.greeks.theta * multiplier
                    net_vega += strike_data.pe.greeks.vega * multiplier
                    break

        spot = float(chain.spot_price)

        return PortfolioGreeks(
            net_delta=net_delta,
            net_gamma=net_gamma,
            net_theta=net_theta,
            net_vega=net_vega,
            delta_dollars=net_delta * spot,
            gamma_dollars=net_gamma * spot * spot / 100,  # Gamma for 1% move
            theta_per_day=net_theta,
            vega_exposure=net_vega,
        )

    def check_portfolio_risk(
        self, portfolio: PortfolioGreeks, capital: float
    ) -> dict[str, Any]:
        """Check portfolio Greeks risk.

        Args:
            portfolio: Portfolio Greeks
            capital: Trading capital

        Returns:
            Risk assessment dict
        """
        alerts = []

        # Delta risk
        delta_risk_pct = abs(portfolio.delta_dollars) / capital * 100
        if delta_risk_pct > 10:
            alerts.append(f"High delta exposure: {delta_risk_pct:.1f}% of capital")

        # Gamma risk (for 1% move)
        gamma_risk_pct = abs(portfolio.gamma_dollars) / capital * 100
        if gamma_risk_pct > 5:
            alerts.append(f"High gamma risk: {gamma_risk_pct:.1f}% for 1% move")

        # Theta
        theta_pct = abs(portfolio.theta_per_day) / capital * 100
        if theta_pct > 0.5:
            if portfolio.theta_per_day < 0:
                alerts.append(f"High theta bleed: {theta_pct:.2f}%/day")
            else:
                alerts.append(f"High theta income: {theta_pct:.2f}%/day")

        # Vega
        vega_risk_pct = abs(portfolio.vega_exposure) / capital * 100
        if vega_risk_pct > 3:
            direction = "long" if portfolio.vega_exposure > 0 else "short"
            alerts.append(f"High vega ({direction}): {vega_risk_pct:.1f}% per 1% IV")

        return {
            "delta_risk_pct": delta_risk_pct,
            "gamma_risk_pct": gamma_risk_pct,
            "theta_pct": theta_pct,
            "vega_risk_pct": vega_risk_pct,
            "alerts": alerts,
            "risk_level": "HIGH" if len(alerts) >= 2 else "MEDIUM" if alerts else "LOW",
        }
