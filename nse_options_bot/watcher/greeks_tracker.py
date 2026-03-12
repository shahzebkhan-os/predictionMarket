"""Greeks tracker for real-time Greeks monitoring.

Tracks theta decay, delta drift, vega exposure for live positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import Any

import pytz
import structlog

from nse_options_bot.watcher.state import OptionsTradeState, TradeLegState

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class GreeksSnapshot:
    """Point-in-time Greeks snapshot."""

    timestamp: datetime
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    spot_price: Decimal
    total_pnl: Decimal

    # Per-leg data
    leg_greeks: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class ThetaDecayRecord:
    """Theta decay tracking record."""

    date: date
    expected_theta: Decimal  # Expected theta decay
    actual_decay: Decimal  # Actual premium decay
    efficiency: float  # actual/expected ratio


@dataclass
class DeltaDriftRecord:
    """Delta drift tracking record."""

    timestamp: datetime
    initial_delta: float
    current_delta: float
    drift: float
    spot_move_pct: float


class GreeksTracker:
    """Real-time Greeks tracking and analysis.

    Monitors:
    - Theta decay vs expected
    - Delta drift over time
    - Vega exposure changes
    - Gamma risk near strikes
    """

    # Alert thresholds
    DELTA_DRIFT_ALERT = 30  # Delta drift > 30 triggers alert
    VEGA_EXPOSURE_ALERT = 50  # Net vega > 50 per lot
    GAMMA_RISK_ZONE = 50  # Points from ATM for gamma risk

    def __init__(self) -> None:
        """Initialize tracker."""
        self._snapshots: list[GreeksSnapshot] = []
        self._theta_records: list[ThetaDecayRecord] = []
        self._delta_drifts: list[DeltaDriftRecord] = []
        self._initial_greeks: dict[str, dict[str, float]] = {}

    def take_snapshot(
        self,
        trade: OptionsTradeState,
    ) -> GreeksSnapshot:
        """Take a Greeks snapshot.

        Args:
            trade: Trade state

        Returns:
            GreeksSnapshot
        """
        leg_greeks = {}
        for leg in trade.legs:
            leg_greeks[leg.tradingsymbol] = {
                "delta": leg.delta,
                "gamma": leg.gamma,
                "theta": leg.theta,
                "vega": leg.vega,
                "iv": leg.iv,
            }

        snapshot = GreeksSnapshot(
            timestamp=datetime.now(IST),
            net_delta=trade.net_delta,
            net_gamma=trade.net_gamma,
            net_theta=trade.net_theta,
            net_vega=trade.net_vega,
            spot_price=trade.current_spot_price,
            total_pnl=trade.total_pnl,
            leg_greeks=leg_greeks,
        )

        self._snapshots.append(snapshot)

        # Record initial Greeks if first snapshot
        if len(self._snapshots) == 1:
            self._initial_greeks = leg_greeks.copy()

        return snapshot

    def record_initial_greeks(
        self,
        trade: OptionsTradeState,
    ) -> None:
        """Record initial Greeks at entry.

        Args:
            trade: Trade state
        """
        for leg in trade.legs:
            self._initial_greeks[leg.tradingsymbol] = {
                "delta": leg.delta,
                "gamma": leg.gamma,
                "theta": leg.theta,
                "vega": leg.vega,
                "iv": leg.iv,
            }

    def calculate_theta_decay(
        self,
        trade: OptionsTradeState,
        trading_hours_elapsed: float,
    ) -> ThetaDecayRecord:
        """Calculate theta decay efficiency.

        Args:
            trade: Trade state
            trading_hours_elapsed: Hours since market open

        Returns:
            ThetaDecayRecord
        """
        # Total trading hours = 6.25 (09:15 to 15:30)
        TOTAL_TRADING_HOURS = 6.25

        # Expected theta is proportional to hours elapsed
        hour_fraction = min(trading_hours_elapsed / TOTAL_TRADING_HOURS, 1.0)
        expected_theta = Decimal(str(abs(trade.net_theta))) * Decimal(str(hour_fraction))

        # Actual decay = change in position value due to time
        # For short theta positions, positive decay is good
        actual_decay = trade.actual_theta_today

        efficiency = (
            float(actual_decay / expected_theta) if expected_theta > 0 else 0.0
        )

        record = ThetaDecayRecord(
            date=datetime.now(IST).date(),
            expected_theta=expected_theta,
            actual_decay=actual_decay,
            efficiency=efficiency,
        )

        self._theta_records.append(record)
        return record

    def calculate_delta_drift(
        self,
        trade: OptionsTradeState,
    ) -> DeltaDriftRecord:
        """Calculate delta drift from initial.

        Args:
            trade: Trade state

        Returns:
            DeltaDriftRecord
        """
        initial_delta = sum(
            g.get("delta", 0) for g in self._initial_greeks.values()
        )
        current_delta = trade.net_delta

        drift = current_delta - initial_delta

        record = DeltaDriftRecord(
            timestamp=datetime.now(IST),
            initial_delta=initial_delta,
            current_delta=current_delta,
            drift=drift,
            spot_move_pct=trade.spot_move_pct,
        )

        self._delta_drifts.append(record)
        return record

    def check_gamma_risk(
        self,
        trade: OptionsTradeState,
    ) -> tuple[bool, list[str]]:
        """Check if any legs are in gamma risk zone.

        Legs near ATM have high gamma risk on expiry day.

        Args:
            trade: Trade state

        Returns:
            Tuple of (has_risk, list of risky legs)
        """
        spot = float(trade.current_spot_price)
        risky_legs = []

        for leg in trade.legs:
            strike = float(leg.strike)
            distance = abs(strike - spot)

            if distance <= self.GAMMA_RISK_ZONE:
                risky_legs.append(
                    f"{leg.tradingsymbol}: {distance:.0f}pts from spot"
                )

        return len(risky_legs) > 0, risky_legs

    def check_vega_exposure(
        self,
        trade: OptionsTradeState,
        lots: int,
    ) -> tuple[bool, str]:
        """Check vega exposure level.

        Args:
            trade: Trade state
            lots: Number of lots

        Returns:
            Tuple of (has_alert, description)
        """
        vega_per_lot = abs(trade.net_vega) / lots if lots > 0 else 0

        if vega_per_lot > self.VEGA_EXPOSURE_ALERT:
            return True, f"High vega: {vega_per_lot:.1f} per lot"

        return False, ""

    def get_charm_adjustment(
        self,
        trade: OptionsTradeState,
        dte: float,
    ) -> dict[str, Any]:
        """Calculate charm (delta decay) adjustment.

        Charm = rate at which delta changes as time passes.
        Important for OTM options in last 2 DTE.

        Args:
            trade: Trade state
            dte: Days to expiry

        Returns:
            Charm analysis dict
        """
        result = {
            "warning": False,
            "message": "",
            "affected_legs": [],
        }

        if dte > 2:
            return result

        # Check OTM long legs
        spot = float(trade.current_spot_price)

        for leg in trade.legs:
            strike = float(leg.strike)
            is_otm = (
                (leg.option_type == "CE" and strike > spot)
                or (leg.option_type == "PE" and strike < spot)
            )

            if is_otm and leg.is_long:
                result["affected_legs"].append({
                    "symbol": leg.tradingsymbol,
                    "delta": leg.delta,
                    "warning": "OTM long option, charm decay accelerating",
                })

        if result["affected_legs"]:
            result["warning"] = True
            result["message"] = (
                f"DTE={dte:.1f}: OTM long options losing delta rapidly"
            )

        return result

    def analyze_pnl_attribution(
        self,
        trade: OptionsTradeState,
    ) -> dict[str, Decimal]:
        """Attribute P&L to Greeks.

        Estimates P&L contribution from each Greek.

        Args:
            trade: Trade state

        Returns:
            Dict with P&L attribution
        """
        if len(self._snapshots) < 2:
            return {
                "delta_pnl": Decimal("0"),
                "gamma_pnl": Decimal("0"),
                "theta_pnl": Decimal("0"),
                "vega_pnl": Decimal("0"),
                "unexplained": Decimal("0"),
            }

        first = self._snapshots[0]
        last = self._snapshots[-1]

        spot_change = float(last.spot_price - first.spot_price)

        # Approximate P&L attribution
        delta_pnl = Decimal(str(first.net_delta * spot_change))
        gamma_pnl = Decimal(str(0.5 * first.net_gamma * spot_change ** 2))
        theta_pnl = trade.actual_theta_today

        # Vega P&L requires IV change
        vega_pnl = Decimal("0")  # Need IV history for this

        total_attributed = delta_pnl + gamma_pnl + theta_pnl + vega_pnl
        unexplained = trade.total_pnl - total_attributed

        return {
            "delta_pnl": delta_pnl,
            "gamma_pnl": gamma_pnl,
            "theta_pnl": theta_pnl,
            "vega_pnl": vega_pnl,
            "unexplained": unexplained,
        }

    def get_alerts(
        self,
        trade: OptionsTradeState,
        dte: float,
    ) -> list[dict[str, Any]]:
        """Get all Greeks-related alerts.

        Args:
            trade: Trade state
            dte: Days to expiry

        Returns:
            List of alert dicts
        """
        alerts = []

        # Delta drift alert
        if self._delta_drifts:
            last_drift = self._delta_drifts[-1]
            if abs(last_drift.drift) > self.DELTA_DRIFT_ALERT:
                alerts.append({
                    "type": "DELTA_DRIFT",
                    "severity": "WARNING",
                    "message": f"Delta drifted {last_drift.drift:.0f} from entry",
                    "value": last_drift.drift,
                })

        # Gamma risk alert
        has_gamma_risk, risky_legs = self.check_gamma_risk(trade)
        if has_gamma_risk and dte <= 1:
            alerts.append({
                "type": "GAMMA_RISK",
                "severity": "HIGH",
                "message": f"Legs in gamma zone: {', '.join(risky_legs)}",
                "legs": risky_legs,
            })

        # Vega exposure alert
        lots = sum(leg.lots for leg in trade.legs) // 2  # Approximate
        has_vega_alert, vega_msg = self.check_vega_exposure(trade, max(lots, 1))
        if has_vega_alert:
            alerts.append({
                "type": "VEGA_EXPOSURE",
                "severity": "WARNING",
                "message": vega_msg,
            })

        # Charm warning
        charm = self.get_charm_adjustment(trade, dte)
        if charm["warning"]:
            alerts.append({
                "type": "CHARM",
                "severity": "INFO",
                "message": charm["message"],
                "affected_legs": charm["affected_legs"],
            })

        return alerts

    def get_summary(
        self,
        trade: OptionsTradeState,
    ) -> dict[str, Any]:
        """Get Greeks tracking summary.

        Args:
            trade: Trade state

        Returns:
            Summary dict
        """
        return {
            "net_delta": trade.net_delta,
            "net_gamma": trade.net_gamma,
            "net_theta": trade.net_theta,
            "net_vega": trade.net_vega,
            "snapshot_count": len(self._snapshots),
            "theta_efficiency": (
                self._theta_records[-1].efficiency
                if self._theta_records
                else None
            ),
            "delta_drift": (
                self._delta_drifts[-1].drift
                if self._delta_drifts
                else None
            ),
            "pnl_attribution": self.analyze_pnl_attribution(trade),
        }

    def clear(self) -> None:
        """Clear all tracking data."""
        self._snapshots.clear()
        self._theta_records.clear()
        self._delta_drifts.clear()
        self._initial_greeks.clear()
