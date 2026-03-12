"""
News & Events Signal.

Signal 10: NSE announcements and economic calendar events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from nse_advisor.market.nse_calendar import EventImpact, MarketEvent, get_nse_calendar
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class NewsEventsMetrics:
    """News and events metrics."""
    high_impact_events: list[MarketEvent]
    medium_impact_events: list[MarketEvent]
    hours_to_next_high_event: float | None
    is_blackout: bool
    post_event_iv_drop: bool
    event_risk_level: str  # "HIGH", "MEDIUM", "LOW"


class NewsEventsAnalyzer:
    """
    Analyzes news and event calendar.
    
    Signal scoring:
    - HIGH event within 24h → Block short premium, flag long straddle
    - Post-event IV drop > 20% → "IV crush - exit long vega now"
    - Index move > 0.8× straddle price → Trend-follow opportunity
    """
    
    # Thresholds
    IV_CRUSH_THRESHOLD = 20.0  # % IV drop after event
    TREND_MOVE_THRESHOLD = 0.8  # × straddle price
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._calendar = get_nse_calendar()
        self._prev_iv: float | None = None
    
    def analyze(
        self,
        current_iv: float = 0.0,
        straddle_move: float = 0.0,
        actual_move: float = 0.0
    ) -> NewsEventsMetrics:
        """
        Analyze news and events.
        
        Args:
            current_iv: Current ATM IV
            straddle_move: ATM straddle expected move
            actual_move: Actual index move
            
        Returns:
            NewsEventsMetrics with analysis
        """
        now = datetime.now(self._ist)
        
        # Get upcoming events
        high_events = self._calendar.upcoming_events(7, EventImpact.HIGH)
        medium_events = self._calendar.upcoming_events(7, EventImpact.MEDIUM)
        
        # Filter to actual high/medium impact
        high_events = [e for e in high_events if e.impact == EventImpact.HIGH]
        medium_events = [e for e in medium_events if e.impact == EventImpact.MEDIUM]
        
        # Hours to next high impact event
        hours_to_event = None
        if high_events:
            next_event = high_events[0]
            event_dt = datetime.combine(next_event.event_date, datetime.min.time())
            event_dt = event_dt.replace(tzinfo=self._ist)
            hours_to_event = (event_dt - now).total_seconds() / 3600
        
        # Check blackout
        is_blackout = self._calendar.is_event_blackout(24)
        
        # Check for IV crush
        post_event_iv_drop = False
        if self._prev_iv and current_iv > 0:
            iv_change = ((current_iv - self._prev_iv) / self._prev_iv) * 100
            if iv_change < -self.IV_CRUSH_THRESHOLD:
                post_event_iv_drop = True
        self._prev_iv = current_iv if current_iv > 0 else self._prev_iv
        
        # Determine risk level
        if is_blackout or (hours_to_event and hours_to_event < 24):
            event_risk_level = "HIGH"
        elif hours_to_event and hours_to_event < 72:
            event_risk_level = "MEDIUM"
        else:
            event_risk_level = "LOW"
        
        return NewsEventsMetrics(
            high_impact_events=high_events,
            medium_impact_events=medium_events,
            hours_to_next_high_event=hours_to_event,
            is_blackout=is_blackout,
            post_event_iv_drop=post_event_iv_drop,
            event_risk_level=event_risk_level,
        )
    
    def compute_signal(
        self,
        current_iv: float = 0.0,
        straddle_move: float = 0.0,
        actual_move: float = 0.0,
        **kwargs
    ) -> SignalResult:
        """
        Compute news & events signal.
        
        Returns:
            SignalResult with score from -1 to +1
            
        Note: This signal is more about risk management than direction.
        Score indicates premium strategy bias, not market direction.
        """
        now = datetime.now(self._ist)
        
        metrics = self.analyze(current_iv, straddle_move, actual_move)
        
        score = 0.0
        reasons = []
        confidence = 0.5
        
        # Event proximity
        if metrics.is_blackout:
            reasons.append("⚠️ EVENT BLACKOUT - No short premium!")
            confidence = 0.9
        elif metrics.hours_to_next_high_event and metrics.hours_to_next_high_event < 24:
            score += 0.3  # Favor long premium (straddle)
            reasons.append(
                f"HIGH event in {metrics.hours_to_next_high_event:.0f}h - "
                "Consider long straddle"
            )
            confidence += 0.2
        
        # Post-event IV crush
        if metrics.post_event_iv_drop:
            reasons.append("⚠️ IV CRUSH detected - Exit long vega positions!")
            score -= 0.4  # Bearish for premium buyers
            confidence = 0.85
        
        # Trend follow opportunity (post large move)
        if straddle_move > 0 and actual_move > 0:
            move_ratio = actual_move / straddle_move
            if move_ratio >= self.TREND_MOVE_THRESHOLD:
                if actual_move > 0:
                    score += 0.25
                    reasons.append(f"Large move ({move_ratio:.1f}× expected) - Trend follow bullish")
                else:
                    score -= 0.25
                    reasons.append(f"Large move ({move_ratio:.1f}× expected) - Trend follow bearish")
                confidence += 0.1
        
        # Add event info
        if metrics.high_impact_events:
            event_names = [e.name for e in metrics.high_impact_events[:2]]
            reasons.append(f"Upcoming: {', '.join(event_names)}")
        
        return SignalResult(
            name="news_events",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "No significant events",
            timestamp=now,
        )


# Global instance
_news_analyzer: NewsEventsAnalyzer | None = None


def get_news_analyzer() -> NewsEventsAnalyzer:
    """Get or create global news analyzer."""
    global _news_analyzer
    if _news_analyzer is None:
        _news_analyzer = NewsEventsAnalyzer()
    return _news_analyzer


async def compute_news_signal(
    current_iv: float = 0.0,
    straddle_move: float = 0.0,
    actual_move: float = 0.0,
    **kwargs
) -> SignalResult:
    """Compute news & events signal (async wrapper)."""
    analyzer = get_news_analyzer()
    return analyzer.compute_signal(current_iv, straddle_move, actual_move, **kwargs)
