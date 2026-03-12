"""
Greeks Tracker.

Tracks theta decay, delta drift, and vega exposure per position.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from nse_advisor.tracker.state import ManualTrade

logger = logging.getLogger(__name__)


@dataclass
class GreeksSnapshot:
    """A snapshot of portfolio Greeks."""
    timestamp: datetime
    delta: float
    gamma: float
    theta: float
    vega: float


@dataclass
class GreeksDrift:
    """Analysis of Greeks drift over time."""
    delta_drift: float  # Change in delta
    theta_decay_actual: float  # Actual theta earned/lost
    theta_decay_expected: float  # Expected based on entry theta
    vega_impact: float  # P&L from vega changes
    gamma_impact: float  # P&L from gamma changes


class GreeksTracker:
    """
    Tracks Greeks evolution for positions.
    
    Features:
    - Track daily theta decay vs expected
    - Monitor delta drift from entry
    - Alert on significant vega exposure changes
    """
    
    def __init__(self) -> None:
        """Initialize Greeks tracker."""
        self._ist = ZoneInfo("Asia/Kolkata")
        
        # Historical snapshots per trade
        self._history: dict[str, list[GreeksSnapshot]] = {}
        
        # Entry Greeks per trade
        self._entry_greeks: dict[str, GreeksSnapshot] = {}
    
    def record_entry(self, trade: ManualTrade) -> None:
        """
        Record entry Greeks for a trade.
        
        Args:
            trade: Trade being entered
        """
        now = datetime.now(self._ist)
        
        snapshot = GreeksSnapshot(
            timestamp=now,
            delta=trade.net_delta,
            gamma=trade.net_gamma,
            theta=trade.net_theta,
            vega=trade.net_vega,
        )
        
        self._entry_greeks[trade.trade_id] = snapshot
        self._history[trade.trade_id] = [snapshot]
    
    def record_update(self, trade: ManualTrade) -> None:
        """
        Record current Greeks snapshot.
        
        Args:
            trade: Trade to record
        """
        now = datetime.now(self._ist)
        
        snapshot = GreeksSnapshot(
            timestamp=now,
            delta=trade.net_delta,
            gamma=trade.net_gamma,
            theta=trade.net_theta,
            vega=trade.net_vega,
        )
        
        if trade.trade_id not in self._history:
            self._history[trade.trade_id] = []
        
        self._history[trade.trade_id].append(snapshot)
        
        # Keep last 100 snapshots per trade
        if len(self._history[trade.trade_id]) > 100:
            self._history[trade.trade_id] = self._history[trade.trade_id][-100:]
    
    def get_drift(self, trade: ManualTrade) -> GreeksDrift | None:
        """
        Calculate Greeks drift from entry.
        
        Args:
            trade: Trade to analyze
            
        Returns:
            GreeksDrift analysis or None if no entry data
        """
        entry = self._entry_greeks.get(trade.trade_id)
        if not entry:
            return None
        
        # Delta drift
        delta_drift = trade.net_delta - entry.delta
        
        # Theta decay
        # Time since entry in days
        now = datetime.now(self._ist)
        days_held = (now - entry.timestamp).total_seconds() / 86400
        
        # Expected theta earned (negative theta = earn for short premium)
        theta_decay_expected = -entry.theta * days_held
        
        # Actual will need to be computed from P&L attribution
        # For now, approximate based on current theta
        theta_decay_actual = -trade.net_theta * days_held
        
        # Vega impact (rough estimate)
        vega_impact = 0.0  # Would need IV data to compute
        
        # Gamma impact
        gamma_impact = 0.0  # Would need spot move data
        
        return GreeksDrift(
            delta_drift=delta_drift,
            theta_decay_actual=theta_decay_actual,
            theta_decay_expected=theta_decay_expected,
            vega_impact=vega_impact,
            gamma_impact=gamma_impact,
        )
    
    def get_daily_theta_burn(self, trade: ManualTrade) -> float:
        """
        Get estimated daily theta burn for a trade.
        
        Negative value = losing to theta (long premium)
        Positive value = earning from theta (short premium)
        
        Args:
            trade: Trade to analyze
            
        Returns:
            Daily theta burn in INR
        """
        # Net theta is per-day theta decay
        # Positive theta for short premium, negative for long premium
        # But convention varies - let's return as P&L impact
        return -trade.net_theta
    
    def get_portfolio_theta_burn(self, trades: list[ManualTrade]) -> float:
        """
        Get total daily theta burn across portfolio.
        
        Args:
            trades: List of open trades
            
        Returns:
            Total daily theta burn in INR
        """
        return sum(self.get_daily_theta_burn(t) for t in trades if t.is_open)
    
    def get_history(self, trade_id: str) -> list[GreeksSnapshot]:
        """Get Greeks history for a trade."""
        return self._history.get(trade_id, [])
    
    def cleanup(self, trade_id: str) -> None:
        """Remove tracking data for a closed trade."""
        self._entry_greeks.pop(trade_id, None)
        self._history.pop(trade_id, None)


# Global instance
_greeks_tracker: GreeksTracker | None = None


def get_greeks_tracker() -> GreeksTracker:
    """Get or create global Greeks tracker."""
    global _greeks_tracker
    if _greeks_tracker is None:
        _greeks_tracker = GreeksTracker()
    return _greeks_tracker
