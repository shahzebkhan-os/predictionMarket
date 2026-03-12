"""
NSE Calendar.

Handles trading days, holidays, expiry dates, and event blackouts.
Fetches holiday data from NSE API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from functools import lru_cache
from typing import Literal

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.data.nse_fetcher import get_nse_fetcher

logger = logging.getLogger(__name__)


class EventImpact(Enum):
    """Impact level of market events."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class MarketEvent:
    """Scheduled market event."""
    name: str
    event_date: date
    event_time: time | None
    impact: EventImpact
    description: str


class NseCalendar:
    """
    NSE trading calendar.
    
    Features:
    - Trading day detection with holiday support
    - Market hours check (09:15-15:30 IST)
    - Expiry date calculation (NIFTY: Thursday, BANKNIFTY: Wednesday)
    - Event blackout detection for high-impact events
    """
    
    # NIFTY expires on Thursday, BANKNIFTY on Wednesday
    EXPIRY_WEEKDAY = {
        "NIFTY": 3,      # Thursday (0=Monday)
        "BANKNIFTY": 2,  # Wednesday
        "FINNIFTY": 1,   # Tuesday
    }
    
    # High-impact events that trigger blackouts
    HIGH_IMPACT_EVENTS = [
        "RBI MPC",
        "Union Budget",
        "US Fed Decision",
        "India CPI",
        "India IIP",
        "India GDP",
        "US Jobs Report",
    ]
    
    # Medium-impact events
    MEDIUM_IMPACT_EVENTS = [
        "F&O Expiry",
        "Major Earnings",
        "US CPI",
        "ECB Decision",
    ]
    
    def __init__(self) -> None:
        """Initialize calendar."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._holidays: set[date] = set()
        self._holidays_loaded = False
        self._events: list[MarketEvent] = []
        self._settings = get_settings()
    
    async def load_holidays(self, year: int | None = None) -> None:
        """
        Load holidays from NSE API.
        
        Args:
            year: Year to load holidays for (defaults to current year)
        """
        if year is None:
            year = datetime.now(self._ist).year
        
        try:
            fetcher = get_nse_fetcher()
            holidays = await fetcher.fetch_holidays(year)
            self._holidays.update(holidays)
            self._holidays_loaded = True
            logger.info(f"Loaded {len(holidays)} holidays for {year}")
        except Exception as e:
            logger.error(f"Failed to load holidays: {e}")
            # Use fallback holidays
            self._load_fallback_holidays(year)
    
    def _load_fallback_holidays(self, year: int) -> None:
        """Load fallback holidays if API fails."""
        # Major Indian market holidays (approximate)
        fallback = [
            date(year, 1, 26),   # Republic Day
            date(year, 3, 8),    # Maha Shivaratri (approx)
            date(year, 3, 25),   # Holi (approx)
            date(year, 4, 14),   # Ambedkar Jayanti
            date(year, 5, 1),    # May Day
            date(year, 8, 15),   # Independence Day
            date(year, 10, 2),   # Gandhi Jayanti
            date(year, 10, 24),  # Dussehra (approx)
            date(year, 11, 12),  # Diwali (approx)
            date(year, 11, 13),  # Diwali (approx)
            date(year, 12, 25),  # Christmas
        ]
        self._holidays.update(fallback)
        logger.warning(f"Using {len(fallback)} fallback holidays for {year}")
    
    def is_holiday(self, check_date: date) -> bool:
        """Check if a date is a holiday."""
        return check_date in self._holidays
    
    def is_weekend(self, check_date: date) -> bool:
        """Check if a date is a weekend."""
        return check_date.weekday() >= 5  # Saturday or Sunday
    
    def is_trading_day(self, check_date: date | None = None) -> bool:
        """
        Check if a date is a trading day.
        
        Args:
            check_date: Date to check (defaults to today IST)
            
        Returns:
            True if it's a trading day
        """
        if check_date is None:
            check_date = datetime.now(self._ist).date()
        
        return not self.is_weekend(check_date) and not self.is_holiday(check_date)
    
    def is_market_open(self, check_time: datetime | None = None) -> bool:
        """
        Check if market is currently open.
        
        Market hours: 09:15-15:30 IST Mon-Fri (excluding holidays)
        
        Args:
            check_time: Time to check (defaults to now IST)
            
        Returns:
            True if market is open
        """
        if check_time is None:
            check_time = datetime.now(self._ist)
        elif check_time.tzinfo is None:
            check_time = check_time.replace(tzinfo=self._ist)
        
        # Check if trading day
        if not self.is_trading_day(check_time.date()):
            return False
        
        # Parse market times
        open_parts = self._settings.market_open_time.split(":")
        close_parts = self._settings.market_close_time.split(":")
        
        market_open = time(int(open_parts[0]), int(open_parts[1]))
        market_close = time(int(close_parts[0]), int(close_parts[1]))
        
        current_time = check_time.time()
        
        return market_open <= current_time <= market_close
    
    def next_trading_day(self, from_date: date | None = None) -> date:
        """
        Get next trading day.
        
        Args:
            from_date: Start date (defaults to today)
            
        Returns:
            Next trading day
        """
        if from_date is None:
            from_date = datetime.now(self._ist).date()
        
        next_day = from_date + timedelta(days=1)
        
        while not self.is_trading_day(next_day):
            next_day += timedelta(days=1)
            if next_day > from_date + timedelta(days=30):
                # Safety limit
                break
        
        return next_day
    
    def previous_trading_day(self, from_date: date | None = None) -> date:
        """Get previous trading day."""
        if from_date is None:
            from_date = datetime.now(self._ist).date()
        
        prev_day = from_date - timedelta(days=1)
        
        while not self.is_trading_day(prev_day):
            prev_day -= timedelta(days=1)
            if prev_day < from_date - timedelta(days=30):
                break
        
        return prev_day
    
    def next_expiry(
        self,
        underlying: str,
        expiry_type: Literal["weekly", "monthly"] = "weekly",
        from_date: date | None = None
    ) -> date:
        """
        Calculate next expiry date.
        
        NIFTY: Every Thursday
        BANKNIFTY: Every Wednesday
        
        Args:
            underlying: Underlying symbol
            expiry_type: weekly or monthly expiry
            from_date: Calculate from this date
            
        Returns:
            Next expiry date
        """
        if from_date is None:
            from_date = datetime.now(self._ist).date()
        
        underlying_upper = underlying.upper()
        expiry_weekday = self.EXPIRY_WEEKDAY.get(underlying_upper, 3)  # Default Thursday
        
        # Find next occurrence of expiry weekday
        days_until = (expiry_weekday - from_date.weekday()) % 7
        
        # If today is expiry day after 15:30, move to next week
        if days_until == 0:
            now = datetime.now(self._ist)
            if now.hour >= 15 and now.minute >= 30:
                days_until = 7
        
        if days_until == 0:
            expiry = from_date
        else:
            expiry = from_date + timedelta(days=days_until)
        
        # If monthly, find last expiry of the month
        if expiry_type == "monthly":
            # Find last Thursday/Wednesday of the month
            next_month = expiry.month + 1 if expiry.month < 12 else 1
            next_year = expiry.year if expiry.month < 12 else expiry.year + 1
            
            # Start from first of next month and go back
            first_of_next = date(next_year, next_month, 1)
            last_of_month = first_of_next - timedelta(days=1)
            
            # Find last occurrence of expiry weekday
            days_back = (last_of_month.weekday() - expiry_weekday) % 7
            monthly_expiry = last_of_month - timedelta(days=days_back)
            
            # If current expiry is before monthly, use the next occurring monthly
            if expiry < monthly_expiry:
                expiry = monthly_expiry
        
        # Adjust if holiday (move to previous trading day)
        while not self.is_trading_day(expiry):
            expiry -= timedelta(days=1)
        
        return expiry
    
    def days_to_expiry(
        self,
        underlying: str,
        from_date: date | None = None
    ) -> int:
        """
        Calculate trading days to expiry.
        
        Args:
            underlying: Underlying symbol
            from_date: Calculate from this date
            
        Returns:
            Number of trading days to expiry
        """
        if from_date is None:
            from_date = datetime.now(self._ist).date()
        
        expiry = self.next_expiry(underlying, "weekly", from_date)
        
        # Count trading days
        days = 0
        current = from_date
        while current < expiry:
            current += timedelta(days=1)
            if self.is_trading_day(current):
                days += 1
        
        return days
    
    def get_active_expiries(
        self,
        underlying: str,
        count: int = 3
    ) -> list[date]:
        """
        Get next N expiry dates.
        
        Args:
            underlying: Underlying symbol
            count: Number of expiries to return
            
        Returns:
            List of expiry dates
        """
        expiries: list[date] = []
        from_date = datetime.now(self._ist).date()
        
        for _ in range(count):
            expiry = self.next_expiry(underlying, "weekly", from_date)
            expiries.append(expiry)
            from_date = expiry + timedelta(days=1)
        
        return expiries
    
    def is_expiry_week(self, underlying: str) -> bool:
        """Check if current week is expiry week."""
        dte = self.days_to_expiry(underlying)
        return dte <= 5  # Within 5 trading days
    
    def is_expiry_day(self, underlying: str) -> bool:
        """Check if today is expiry day."""
        return self.days_to_expiry(underlying) == 0
    
    def add_event(self, event: MarketEvent) -> None:
        """Add a market event."""
        self._events.append(event)
    
    def upcoming_events(
        self,
        days_ahead: int = 7,
        min_impact: EventImpact | None = None
    ) -> list[MarketEvent]:
        """
        Get upcoming market events.
        
        Args:
            days_ahead: Look ahead window in days
            min_impact: Minimum impact level to include
            
        Returns:
            List of upcoming events
        """
        today = datetime.now(self._ist).date()
        cutoff = today + timedelta(days=days_ahead)
        
        events = [
            e for e in self._events
            if today <= e.event_date <= cutoff
        ]
        
        if min_impact:
            impact_order = {EventImpact.HIGH: 0, EventImpact.MEDIUM: 1, EventImpact.LOW: 2}
            min_level = impact_order.get(min_impact, 2)
            events = [
                e for e in events
                if impact_order.get(e.impact, 2) <= min_level
            ]
        
        return sorted(events, key=lambda e: e.event_date)
    
    def is_event_blackout(self, hours: int = 24) -> bool:
        """
        Check if we're in an event blackout period.
        
        Blackout: HIGH-impact event within specified hours.
        
        Args:
            hours: Blackout window in hours
            
        Returns:
            True if in blackout period
        """
        now = datetime.now(self._ist)
        cutoff = now + timedelta(hours=hours)
        
        for event in self._events:
            if event.impact != EventImpact.HIGH:
                continue
            
            event_dt = datetime.combine(
                event.event_date,
                event.event_time or time(9, 0),
                tzinfo=self._ist
            )
            
            if now <= event_dt <= cutoff:
                return True
        
        return False


# Global calendar instance
_nse_calendar: NseCalendar | None = None


def get_nse_calendar() -> NseCalendar:
    """Get or create global NSE calendar instance."""
    global _nse_calendar
    if _nse_calendar is None:
        _nse_calendar = NseCalendar()
    return _nse_calendar


async def init_nse_calendar() -> NseCalendar:
    """Initialize NSE calendar with holiday data."""
    calendar = get_nse_calendar()
    await calendar.load_holidays()
    return calendar
