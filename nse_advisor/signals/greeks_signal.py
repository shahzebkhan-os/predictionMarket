"""
Greeks Composite Signal.

Signal 12: Portfolio Greeks analysis for delta, theta, vega management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class GreeksMetrics:
    """Portfolio Greeks metrics."""
    portfolio_delta: float
    portfolio_gamma: float
    portfolio_theta: float
    portfolio_vega: float
    delta_neutral: bool
    theta_burn_exceeded: bool
    vega_threshold_exceeded: bool
    charm_risk: bool  # DTE <= 2 and holding OTM
    recommendations: list[str]


class GreeksAnalyzer:
    """
    Analyzes portfolio Greeks.
    
    Signal scoring:
    - Portfolio delta > threshold → Delta hedge recommendation
    - Portfolio vega > threshold → "Reduce long vol"
    - Net theta burn > limit → "Reduce long premium"
    - Charm alert when DTE ≤ 2 (avoid buying OTM)
    
    Greeks sign convention:
    - SELL legs have all Greeks multiplied by -1
    - Buyer perspective from py_vollib
    """
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
    
    def analyze(
        self,
        positions: list[dict],
        dte: int = 5
    ) -> GreeksMetrics:
        """
        Analyze portfolio Greeks.
        
        Args:
            positions: List of position dicts with Greeks
            dte: Days to expiry
            
        Returns:
            GreeksMetrics with analysis
        """
        # Aggregate Greeks
        portfolio_delta = 0.0
        portfolio_gamma = 0.0
        portfolio_theta = 0.0
        portfolio_vega = 0.0
        
        for pos in positions:
            # Get Greeks (already adjusted for SELL legs with -1)
            qty = pos.get("quantity", 0)
            lot_size = pos.get("lot_size", 1)
            total_qty = qty * lot_size
            
            portfolio_delta += pos.get("delta", 0) * total_qty
            portfolio_gamma += pos.get("gamma", 0) * total_qty
            portfolio_theta += pos.get("theta", 0) * total_qty
            portfolio_vega += pos.get("vega", 0) * total_qty
        
        # Check thresholds
        delta_neutral = abs(portfolio_delta) <= self._settings.delta_hedge_threshold
        theta_burn_exceeded = abs(portfolio_theta) > self._settings.theta_burn_limit_inr_per_day
        vega_threshold_exceeded = abs(portfolio_vega) > self._settings.vega_threshold
        
        # Charm risk (DTE <= 2 and significant gamma)
        charm_risk = dte <= 2 and abs(portfolio_gamma) > 10
        
        # Build recommendations
        recommendations = []
        
        if not delta_neutral:
            if portfolio_delta > 0:
                recommendations.append(f"High delta (+{portfolio_delta:.0f}): Consider selling CE or buying PE to hedge")
            else:
                recommendations.append(f"Low delta ({portfolio_delta:.0f}): Consider selling PE or buying CE to hedge")
        
        if theta_burn_exceeded:
            if portfolio_theta < 0:
                recommendations.append(f"Theta burn ₹{abs(portfolio_theta):.0f}/day: Reduce long premium")
            else:
                recommendations.append(f"Earning theta ₹{portfolio_theta:.0f}/day: Monitor short positions")
        
        if vega_threshold_exceeded:
            if portfolio_vega > 0:
                recommendations.append(f"High vega (+{portfolio_vega:.0f}): Consider reducing long vol")
            else:
                recommendations.append(f"Short vega ({portfolio_vega:.0f}): Monitor for IV spike risk")
        
        if charm_risk:
            recommendations.append(f"⚠️ DTE={dte}: Charm risk high - avoid OTM buys, delta decay accelerating")
        
        return GreeksMetrics(
            portfolio_delta=portfolio_delta,
            portfolio_gamma=portfolio_gamma,
            portfolio_theta=portfolio_theta,
            portfolio_vega=portfolio_vega,
            delta_neutral=delta_neutral,
            theta_burn_exceeded=theta_burn_exceeded,
            vega_threshold_exceeded=vega_threshold_exceeded,
            charm_risk=charm_risk,
            recommendations=recommendations,
        )
    
    def compute_signal(
        self,
        positions: list[dict] | None = None,
        dte: int = 5,
        **kwargs
    ) -> SignalResult:
        """
        Compute Greeks composite signal.
        
        Returns:
            SignalResult with score from -1 to +1
            
        Note: This signal is about portfolio risk, not market direction.
        Score indicates premium strategy adjustment needs.
        """
        now = datetime.now(self._ist)
        
        if not positions:
            return SignalResult(
                name="greeks_composite",
                score=0.0,
                confidence=0.5,
                reason="No positions to analyze",
                timestamp=now,
            )
        
        metrics = self.analyze(positions, dte)
        
        score = 0.0
        reasons = []
        confidence = 0.6
        
        # Delta exposure (directional signal)
        if not metrics.delta_neutral:
            delta_score = metrics.portfolio_delta / 100  # Normalize
            delta_score = max(-0.5, min(0.5, delta_score))
            score += delta_score
            
            if metrics.portfolio_delta > 0:
                reasons.append(f"Long delta +{metrics.portfolio_delta:.0f}")
            else:
                reasons.append(f"Short delta {metrics.portfolio_delta:.0f}")
        
        # Theta position (premium strategy signal)
        if metrics.theta_burn_exceeded:
            if metrics.portfolio_theta < 0:
                score += 0.2  # Favor reducing long premium
                reasons.append(f"High theta burn: ₹{abs(metrics.portfolio_theta):.0f}/day")
            else:
                score -= 0.1  # Monitor short positions
                reasons.append(f"Earning theta: ₹{metrics.portfolio_theta:.0f}/day")
        
        # Vega exposure
        if metrics.vega_threshold_exceeded:
            if metrics.portfolio_vega > 0:
                score -= 0.15  # Reduce long vol
                reasons.append(f"High vega: +{metrics.portfolio_vega:.0f}")
            else:
                score += 0.1
                reasons.append(f"Short vega: {metrics.portfolio_vega:.0f}")
        
        # Charm risk
        if metrics.charm_risk:
            reasons.append(f"⚠️ Charm risk (DTE={dte})")
            confidence = min(0.8, confidence + 0.15)
        
        # Add recommendations
        for rec in metrics.recommendations[:2]:
            reasons.append(rec)
        
        return SignalResult(
            name="greeks_composite",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "Greeks balanced",
            timestamp=now,
        )


# Global instance
_greeks_analyzer: GreeksAnalyzer | None = None


def get_greeks_analyzer() -> GreeksAnalyzer:
    """Get or create global Greeks analyzer."""
    global _greeks_analyzer
    if _greeks_analyzer is None:
        _greeks_analyzer = GreeksAnalyzer()
    return _greeks_analyzer


async def compute_greeks_signal(
    positions: list[dict] | None = None,
    dte: int = 5,
    **kwargs
) -> SignalResult:
    """Compute Greeks composite signal (async wrapper)."""
    analyzer = get_greeks_analyzer()
    return analyzer.compute_signal(positions, dte, **kwargs)
