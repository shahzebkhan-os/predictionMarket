"""Greek P&L attribution.

Splits P&L into Delta/Theta/Vega/Gamma contributions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytz
import structlog

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class GreekSnapshot:
    """Greeks at a point in time."""

    timestamp: datetime
    spot: Decimal
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


@dataclass
class GreekPnLBreakdown:
    """P&L breakdown by Greeks."""

    # P&L components
    delta_pnl: Decimal = Decimal("0")
    gamma_pnl: Decimal = Decimal("0")
    theta_pnl: Decimal = Decimal("0")
    vega_pnl: Decimal = Decimal("0")

    # Higher-order effects
    charm_pnl: Decimal = Decimal("0")  # Delta decay
    vanna_pnl: Decimal = Decimal("0")  # Delta/vol interaction
    volga_pnl: Decimal = Decimal("0")  # Vega convexity

    # Residual
    unexplained_pnl: Decimal = Decimal("0")

    @property
    def explained_pnl(self) -> Decimal:
        """Total explained P&L."""
        return (
            self.delta_pnl +
            self.gamma_pnl +
            self.theta_pnl +
            self.vega_pnl +
            self.charm_pnl +
            self.vanna_pnl +
            self.volga_pnl
        )

    @property
    def total_pnl(self) -> Decimal:
        """Total P&L."""
        return self.explained_pnl + self.unexplained_pnl

    @property
    def explanation_ratio(self) -> float:
        """Percentage of P&L explained."""
        total = abs(self.total_pnl)
        if total == 0:
            return 100.0
        return float(abs(self.explained_pnl) / total * 100)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "delta_pnl": float(self.delta_pnl),
            "gamma_pnl": float(self.gamma_pnl),
            "theta_pnl": float(self.theta_pnl),
            "vega_pnl": float(self.vega_pnl),
            "charm_pnl": float(self.charm_pnl),
            "vanna_pnl": float(self.vanna_pnl),
            "volga_pnl": float(self.volga_pnl),
            "unexplained_pnl": float(self.unexplained_pnl),
            "total_pnl": float(self.total_pnl),
            "explanation_ratio": self.explanation_ratio,
        }


class GreekPnLCalculator:
    """Calculate P&L attribution to Greeks.

    Uses finite difference method to attribute P&L:
    - Delta P&L = Delta × ΔS
    - Gamma P&L = 0.5 × Gamma × (ΔS)²
    - Theta P&L = Theta × Δt
    - Vega P&L = Vega × ΔIV

    Also calculates higher-order effects when data is available.
    """

    def __init__(self) -> None:
        """Initialize calculator."""
        self._snapshots: list[GreekSnapshot] = []

    def add_snapshot(
        self,
        timestamp: datetime,
        spot: Decimal,
        delta: float,
        gamma: float,
        theta: float,
        vega: float,
        iv: float,
    ) -> None:
        """Add a Greeks snapshot.

        Args:
            timestamp: Snapshot time
            spot: Spot price
            delta: Net delta
            gamma: Net gamma
            theta: Net theta
            vega: Net vega
            iv: Average IV
        """
        self._snapshots.append(
            GreekSnapshot(
                timestamp=timestamp,
                spot=spot,
                delta=delta,
                gamma=gamma,
                theta=theta,
                vega=vega,
                iv=iv,
            )
        )

    def calculate_attribution(
        self,
        actual_pnl: Decimal,
    ) -> GreekPnLBreakdown:
        """Calculate P&L attribution.

        Args:
            actual_pnl: Actual realized P&L

        Returns:
            GreekPnLBreakdown
        """
        if len(self._snapshots) < 2:
            return GreekPnLBreakdown(unexplained_pnl=actual_pnl)

        first = self._snapshots[0]
        last = self._snapshots[-1]

        # Calculate changes
        spot_change = float(last.spot - first.spot)
        time_change = (last.timestamp - first.timestamp).total_seconds() / 86400  # Days
        iv_change = last.iv - first.iv

        # Delta P&L
        avg_delta = (first.delta + last.delta) / 2
        delta_pnl = Decimal(str(avg_delta * spot_change))

        # Gamma P&L (second order)
        avg_gamma = (first.gamma + last.gamma) / 2
        gamma_pnl = Decimal(str(0.5 * avg_gamma * spot_change ** 2))

        # Theta P&L
        avg_theta = (first.theta + last.theta) / 2
        theta_pnl = Decimal(str(avg_theta * time_change))

        # Vega P&L
        avg_vega = (first.vega + last.vega) / 2
        vega_pnl = Decimal(str(avg_vega * iv_change * 100))  # IV in % terms

        # Higher order (simplified estimates)
        # Charm: how much delta changed due to time
        charm_pnl = Decimal("0")
        if len(self._snapshots) > 2:
            delta_change = last.delta - first.delta
            expected_delta_change = spot_change * first.gamma
            charm_effect = delta_change - expected_delta_change
            charm_pnl = Decimal(str(charm_effect * spot_change * 0.5))

        # Unexplained
        explained = delta_pnl + gamma_pnl + theta_pnl + vega_pnl + charm_pnl
        unexplained = actual_pnl - explained

        breakdown = GreekPnLBreakdown(
            delta_pnl=delta_pnl,
            gamma_pnl=gamma_pnl,
            theta_pnl=theta_pnl,
            vega_pnl=vega_pnl,
            charm_pnl=charm_pnl,
            unexplained_pnl=unexplained,
        )

        logger.info(
            "greek_pnl_calculated",
            actual_pnl=float(actual_pnl),
            delta_pnl=float(delta_pnl),
            theta_pnl=float(theta_pnl),
            explanation_ratio=breakdown.explanation_ratio,
        )

        return breakdown

    def calculate_incremental(self) -> list[GreekPnLBreakdown]:
        """Calculate incremental P&L between each snapshot.

        Returns:
            List of breakdowns for each interval
        """
        breakdowns = []

        for i in range(1, len(self._snapshots)):
            prev = self._snapshots[i - 1]
            curr = self._snapshots[i]

            spot_change = float(curr.spot - prev.spot)
            time_change = (curr.timestamp - prev.timestamp).total_seconds() / 86400
            iv_change = curr.iv - prev.iv

            delta_pnl = Decimal(str(prev.delta * spot_change))
            gamma_pnl = Decimal(str(0.5 * prev.gamma * spot_change ** 2))
            theta_pnl = Decimal(str(prev.theta * time_change))
            vega_pnl = Decimal(str(prev.vega * iv_change * 100))

            breakdowns.append(
                GreekPnLBreakdown(
                    delta_pnl=delta_pnl,
                    gamma_pnl=gamma_pnl,
                    theta_pnl=theta_pnl,
                    vega_pnl=vega_pnl,
                )
            )

        return breakdowns

    def get_greek_contribution_pct(
        self,
        breakdown: GreekPnLBreakdown,
    ) -> dict[str, float]:
        """Get percentage contribution of each Greek.

        Args:
            breakdown: P&L breakdown

        Returns:
            Percentage contribution dict
        """
        total = abs(float(breakdown.total_pnl))
        if total == 0:
            return {
                "delta": 0.0,
                "gamma": 0.0,
                "theta": 0.0,
                "vega": 0.0,
                "other": 0.0,
            }

        return {
            "delta": abs(float(breakdown.delta_pnl)) / total * 100,
            "gamma": abs(float(breakdown.gamma_pnl)) / total * 100,
            "theta": abs(float(breakdown.theta_pnl)) / total * 100,
            "vega": abs(float(breakdown.vega_pnl)) / total * 100,
            "other": abs(float(breakdown.unexplained_pnl + breakdown.charm_pnl)) / total * 100,
        }

    def clear(self) -> None:
        """Clear snapshots."""
        self._snapshots.clear()


def quick_greek_attribution(
    entry_spot: Decimal,
    exit_spot: Decimal,
    entry_iv: float,
    exit_iv: float,
    entry_delta: float,
    entry_gamma: float,
    entry_theta: float,
    entry_vega: float,
    days_held: float,
    actual_pnl: Decimal,
) -> GreekPnLBreakdown:
    """Quick Greek P&L attribution without full snapshots.

    Args:
        entry_spot: Spot at entry
        exit_spot: Spot at exit
        entry_iv: IV at entry
        exit_iv: IV at exit
        entry_delta: Delta at entry
        entry_gamma: Gamma at entry
        entry_theta: Theta at entry
        entry_vega: Vega at entry
        days_held: Days position held
        actual_pnl: Actual P&L

    Returns:
        GreekPnLBreakdown
    """
    spot_change = float(exit_spot - entry_spot)
    iv_change = exit_iv - entry_iv

    delta_pnl = Decimal(str(entry_delta * spot_change))
    gamma_pnl = Decimal(str(0.5 * entry_gamma * spot_change ** 2))
    theta_pnl = Decimal(str(entry_theta * days_held))
    vega_pnl = Decimal(str(entry_vega * iv_change * 100))

    explained = delta_pnl + gamma_pnl + theta_pnl + vega_pnl
    unexplained = actual_pnl - explained

    return GreekPnLBreakdown(
        delta_pnl=delta_pnl,
        gamma_pnl=gamma_pnl,
        theta_pnl=theta_pnl,
        vega_pnl=vega_pnl,
        unexplained_pnl=unexplained,
    )
