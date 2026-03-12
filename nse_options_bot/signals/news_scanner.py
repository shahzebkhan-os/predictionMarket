"""News & Events Signal (Signal 10).

Sources: NSE corporate actions API, RBI website, economic calendar.
HIGH-impact event within 24h → block premium-selling, flag long straddle.
Post-event within 2h: IV drops >20% → exit all long vega.
Index moves >0.8× straddle → trend-follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.market.nse_calendar import EventImpact, MarketEvent, NseCalendar
from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class EventType(str, Enum):
    """Event type classification."""

    RBI_MPC = "RBI_MPC"
    BUDGET = "BUDGET"
    US_FED = "US_FED"
    INDIA_CPI = "INDIA_CPI"
    INDIA_IIP = "INDIA_IIP"
    EARNINGS = "EARNINGS"
    EXPIRY = "EXPIRY"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    GDP = "GDP"
    TRADE_DATA = "TRADE_DATA"
    OTHER = "OTHER"


@dataclass
class EventAlert:
    """Event alert information."""

    event: MarketEvent
    hours_until: float
    is_blackout: bool
    trading_impact: str  # "block_premium_selling", "consider_long_straddle", "normal"


@dataclass
class PostEventState:
    """Post-event market state."""

    event_ended_at: datetime
    hours_since: float
    iv_change_pct: float
    price_move_pct: float
    expected_move_pct: float
    move_vs_expected: float  # price_move / expected_move


class NewsEventAnalyzer:
    """News and events analyzer.

    Monitors upcoming events and adjusts trading recommendations.
    """

    # Event timing thresholds
    BLACKOUT_HOURS = 24  # Block premium selling 24h before HIGH events
    POST_EVENT_HOURS = 2  # Monitor for 2h after event
    IV_CRUSH_THRESHOLD = -20  # >20% IV drop = crush

    # Move thresholds
    TREND_FOLLOW_RATIO = 0.8  # Move > 0.8× expected = trend follow

    def __init__(self, calendar: NseCalendar | None = None) -> None:
        """Initialize analyzer.

        Args:
            calendar: NSE calendar with events
        """
        self._calendar = calendar or NseCalendar()
        self._pending_events: list[MarketEvent] = []
        self._completed_events: list[tuple[MarketEvent, datetime]] = []
        self._iv_before_event: dict[str, float] = {}

    def add_event(self, event: MarketEvent) -> None:
        """Add custom event.

        Args:
            event: Market event
        """
        self._pending_events.append(event)
        self._calendar.add_event(event)

    def record_iv_before_event(self, event_name: str, iv: float) -> None:
        """Record IV before event for crush detection.

        Args:
            event_name: Event name
            iv: Current IV
        """
        self._iv_before_event[event_name] = iv

    def mark_event_completed(self, event: MarketEvent) -> None:
        """Mark event as completed.

        Args:
            event: Completed event
        """
        self._completed_events.append((event, datetime.now(IST)))

    def analyze(
        self,
        current_iv: float = 0.0,
        current_price: float = 0.0,
        pre_event_price: float | None = None,
        expected_move_pct: float = 0.0,
    ) -> Signal:
        """Analyze news/events impact.

        Args:
            current_iv: Current IV
            current_price: Current price
            pre_event_price: Price before event (for post-event analysis)
            expected_move_pct: Expected move from straddle

        Returns:
            News/events signal
        """
        now = datetime.now(IST)

        # Get upcoming events
        upcoming = self._calendar.upcoming_events(days_ahead=3)
        alerts = self._analyze_upcoming_events(upcoming, now)

        # Check if in blackout
        in_blackout = self._calendar.is_event_blackout(now)

        # Analyze post-event state
        post_event = self._analyze_post_event(
            now, current_iv, current_price, pre_event_price, expected_move_pct
        )

        # Calculate score
        score, confidence, reason = self._calculate_score(
            alerts, in_blackout, post_event
        )

        return create_signal(
            signal_type=SignalType.NEWS_EVENTS,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "in_blackout": in_blackout,
                "upcoming_high_impact": len([a for a in alerts if a.event.impact == EventImpact.HIGH]),
                "post_event_hours": post_event.hours_since if post_event else None,
                "iv_change_pct": post_event.iv_change_pct if post_event else None,
                "move_vs_expected": post_event.move_vs_expected if post_event else None,
            },
        )

    def _analyze_upcoming_events(
        self, events: list[MarketEvent], now: datetime
    ) -> list[EventAlert]:
        """Analyze upcoming events.

        Args:
            events: List of upcoming events
            now: Current time

        Returns:
            List of event alerts
        """
        alerts = []

        for event in events:
            event_dt = datetime.combine(
                event.event_date,
                event.event_time or datetime.min.time(),
            )
            event_dt = IST.localize(event_dt) if event_dt.tzinfo is None else event_dt

            hours_until = (event_dt - now).total_seconds() / 3600

            if hours_until < 0:
                continue  # Event passed

            is_blackout = (
                event.impact == EventImpact.HIGH
                and hours_until <= self.BLACKOUT_HOURS
            )

            if is_blackout:
                trading_impact = "block_premium_selling"
            elif event.impact == EventImpact.HIGH and hours_until <= 48:
                trading_impact = "consider_long_straddle"
            else:
                trading_impact = "normal"

            alerts.append(
                EventAlert(
                    event=event,
                    hours_until=hours_until,
                    is_blackout=is_blackout,
                    trading_impact=trading_impact,
                )
            )

        return sorted(alerts, key=lambda x: x.hours_until)

    def _analyze_post_event(
        self,
        now: datetime,
        current_iv: float,
        current_price: float,
        pre_event_price: float | None,
        expected_move_pct: float,
    ) -> PostEventState | None:
        """Analyze post-event state.

        Args:
            now: Current time
            current_iv: Current IV
            current_price: Current price
            pre_event_price: Price before event
            expected_move_pct: Expected move

        Returns:
            PostEventState or None
        """
        # Find recent completed events
        for event, completed_at in reversed(self._completed_events):
            hours_since = (now - completed_at).total_seconds() / 3600

            if hours_since <= self.POST_EVENT_HOURS:
                # Calculate IV change
                iv_before = self._iv_before_event.get(event.name, current_iv)
                iv_change_pct = (
                    ((current_iv - iv_before) / iv_before * 100)
                    if iv_before > 0
                    else 0.0
                )

                # Calculate price move
                price_move_pct = 0.0
                if pre_event_price and pre_event_price > 0:
                    price_move_pct = abs(
                        (current_price - pre_event_price) / pre_event_price * 100
                    )

                move_vs_expected = (
                    price_move_pct / expected_move_pct
                    if expected_move_pct > 0
                    else 0.0
                )

                return PostEventState(
                    event_ended_at=completed_at,
                    hours_since=hours_since,
                    iv_change_pct=iv_change_pct,
                    price_move_pct=price_move_pct,
                    expected_move_pct=expected_move_pct,
                    move_vs_expected=move_vs_expected,
                )

        return None

    def _calculate_score(
        self,
        alerts: list[EventAlert],
        in_blackout: bool,
        post_event: PostEventState | None,
    ) -> tuple[float, float, str]:
        """Calculate news/events score.

        Args:
            alerts: Event alerts
            in_blackout: Whether in blackout period
            post_event: Post-event state

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # Blackout period
        if in_blackout:
            # During blackout, strongly discourage premium selling
            score -= 0.5  # Negative score indicates avoid selling
            high_events = [a for a in alerts if a.is_blackout]
            if high_events:
                reasons.append(
                    f"BLACKOUT: {high_events[0].event.name} in {high_events[0].hours_until:.0f}h"
                )
            confidence += 0.2

        # Upcoming high-impact events
        high_impact = [
            a for a in alerts
            if a.event.impact == EventImpact.HIGH and not a.is_blackout
        ]
        if high_impact:
            # Flag long straddle opportunity
            score += 0.2  # Positive for buying vol
            reasons.append(
                f"HIGH event: {high_impact[0].event.name} in {high_impact[0].hours_until:.0f}h → Long straddle"
            )

        # Post-event analysis
        if post_event:
            # IV crush detection
            if post_event.iv_change_pct <= self.IV_CRUSH_THRESHOLD:
                score -= 0.3  # Exit long vega
                reasons.append(
                    f"IV crush {post_event.iv_change_pct:.0f}% → Exit long vega"
                )
                confidence += 0.15

            # Trend follow on big move
            if post_event.move_vs_expected >= self.TREND_FOLLOW_RATIO:
                # Big move, trend follow
                score += 0.2  # Directional bias
                reasons.append(
                    f"Move {post_event.price_move_pct:.1f}% > {self.TREND_FOLLOW_RATIO}× expected → Trend follow"
                )
                confidence += 0.1

        if not reasons:
            reasons.append("No significant event impact")

        reason = " | ".join(reasons)

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def get_event_calendar(self, days_ahead: int = 7) -> list[dict[str, Any]]:
        """Get event calendar.

        Args:
            days_ahead: Days to look ahead

        Returns:
            List of event dicts
        """
        events = self._calendar.upcoming_events(days_ahead)

        return [
            {
                "name": e.name,
                "date": e.event_date.isoformat(),
                "time": e.event_time.isoformat() if e.event_time else None,
                "impact": e.impact.value,
                "description": e.description,
            }
            for e in events
        ]

    def should_block_premium_selling(self) -> tuple[bool, str]:
        """Check if premium selling should be blocked.

        Returns:
            Tuple of (should_block, reason)
        """
        if self._calendar.is_event_blackout():
            events = self._calendar.upcoming_events(days_ahead=2, impact_filter=EventImpact.HIGH)
            if events:
                return True, f"Blackout for {events[0].name}"

        return False, ""

    def should_tighten_stops(self) -> tuple[bool, float]:
        """Check if stops should be tightened.

        Returns:
            Tuple of (should_tighten, multiplier)
        """
        events = self._calendar.upcoming_events(days_ahead=1, impact_filter=EventImpact.HIGH)
        if events:
            # Tighten to 30% of max loss
            return True, 0.3

        return False, 1.0
