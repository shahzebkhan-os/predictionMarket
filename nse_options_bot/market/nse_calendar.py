"""NSE trading calendar with expiry dates and event tracking.

Handles:
- Trading days (Mon-Fri, excluding NSE holidays)
- Expiry dates (NIFTY every Thursday, BANKNIFTY every Wednesday)
- Event blackouts (RBI MPC, Budget, US Fed, India CPI/IIP)
- Market hours: 09:15-15:30 IST
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any

import pytz
import structlog

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class EventImpact(str, Enum):
    """Event impact level."""

    HIGH = "HIGH"  # RBI MPC, Budget, US Fed
    MEDIUM = "MEDIUM"  # India CPI/IIP, earnings
    LOW = "LOW"  # Other announcements


class ExpiryType(str, Enum):
    """Expiry type."""

    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


@dataclass
class MarketEvent:
    """Market event with impact level."""

    name: str
    event_date: date
    event_time: time | None
    impact: EventImpact
    description: str = ""
    blackout_start: datetime | None = None
    blackout_end: datetime | None = None


@dataclass
class ExpiryInfo:
    """Expiry information."""

    expiry_date: date
    symbol: str
    expiry_type: ExpiryType
    days_to_expiry: int


class NseCalendar:
    """NSE trading calendar manager.

    Handles trading days, expiries, and event blackouts.
    """

    # Market timing (IST)
    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 30)
    PRE_OPEN_START = time(9, 0)
    PRE_OPEN_END = time(9, 8)

    # NSE holidays 2024-2025 (sample, should be updated annually)
    NSE_HOLIDAYS_2024 = {
        date(2024, 1, 26),  # Republic Day
        date(2024, 3, 8),  # Maha Shivaratri
        date(2024, 3, 25),  # Holi
        date(2024, 3, 29),  # Good Friday
        date(2024, 4, 11),  # Id-ul-Fitr
        date(2024, 4, 14),  # Dr. B.R. Ambedkar Jayanti
        date(2024, 4, 17),  # Ram Navami
        date(2024, 4, 21),  # Mahavir Jayanti
        date(2024, 5, 1),  # May Day
        date(2024, 5, 23),  # Buddha Purnima
        date(2024, 6, 17),  # Id-ul-Adha
        date(2024, 7, 17),  # Muharram
        date(2024, 8, 15),  # Independence Day
        date(2024, 10, 2),  # Mahatma Gandhi Jayanti
        date(2024, 11, 1),  # Diwali - Laxmi Pujan
        date(2024, 11, 15),  # Guru Nanak Jayanti
        date(2024, 12, 25),  # Christmas
    }

    NSE_HOLIDAYS_2025 = {
        date(2025, 1, 26),  # Republic Day
        date(2025, 2, 26),  # Maha Shivaratri
        date(2025, 3, 14),  # Holi
        date(2025, 3, 31),  # Id-ul-Fitr
        date(2025, 4, 10),  # Mahavir Jayanti
        date(2025, 4, 14),  # Dr. B.R. Ambedkar Jayanti
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 1),  # May Day
        date(2025, 8, 15),  # Independence Day
        date(2025, 8, 27),  # Janmashtami
        date(2025, 10, 2),  # Mahatma Gandhi Jayanti
        date(2025, 10, 21),  # Diwali
        date(2025, 11, 5),  # Guru Nanak Jayanti
        date(2025, 12, 25),  # Christmas
    }

    # Expiry day mapping
    EXPIRY_WEEKDAYS = {
        "NIFTY": 3,  # Thursday
        "BANKNIFTY": 2,  # Wednesday
        "FINNIFTY": 1,  # Tuesday
        "MIDCPNIFTY": 0,  # Monday
    }

    def __init__(self) -> None:
        """Initialize NSE calendar."""
        self._holidays = self.NSE_HOLIDAYS_2024 | self.NSE_HOLIDAYS_2025
        self._events: list[MarketEvent] = []
        self._load_default_events()

    def _load_default_events(self) -> None:
        """Load default high-impact events."""
        # RBI MPC meetings 2024
        rbi_mpc_dates = [
            date(2024, 2, 8),
            date(2024, 4, 5),
            date(2024, 6, 7),
            date(2024, 8, 8),
            date(2024, 10, 9),
            date(2024, 12, 6),
        ]

        for event_date in rbi_mpc_dates:
            self._events.append(
                MarketEvent(
                    name="RBI MPC Meeting",
                    event_date=event_date,
                    event_time=time(10, 0),
                    impact=EventImpact.HIGH,
                    description="RBI Monetary Policy Committee decision",
                    blackout_start=datetime.combine(
                        event_date - timedelta(days=1), time(15, 0), IST
                    ),
                    blackout_end=datetime.combine(event_date, time(15, 0), IST),
                )
            )

        # Budget 2024
        self._events.append(
            MarketEvent(
                name="Union Budget 2024",
                event_date=date(2024, 7, 23),
                event_time=time(11, 0),
                impact=EventImpact.HIGH,
                description="Annual Union Budget presentation",
                blackout_start=datetime.combine(date(2024, 7, 22), time(15, 0), IST),
                blackout_end=datetime.combine(date(2024, 7, 23), time(15, 30), IST),
            )
        )

    def add_holiday(self, holiday_date: date) -> None:
        """Add a holiday to the calendar.

        Args:
            holiday_date: Date of the holiday
        """
        self._holidays.add(holiday_date)

    def add_event(self, event: MarketEvent) -> None:
        """Add a market event.

        Args:
            event: Market event
        """
        self._events.append(event)

    def is_trading_day(self, check_date: date | None = None) -> bool:
        """Check if a date is a trading day.

        Args:
            check_date: Date to check (default: today)

        Returns:
            True if trading day
        """
        if check_date is None:
            check_date = datetime.now(IST).date()

        # Weekend check
        if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
            return False

        # Holiday check
        return check_date not in self._holidays

    def is_market_open(self, check_time: datetime | None = None) -> bool:
        """Check if market is currently open.

        Args:
            check_time: Time to check (default: now)

        Returns:
            True if market is open
        """
        if check_time is None:
            check_time = datetime.now(IST)

        if not self.is_trading_day(check_time.date()):
            return False

        current_time = check_time.time()
        return self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE

    def is_pre_open(self, check_time: datetime | None = None) -> bool:
        """Check if in pre-open session.

        Args:
            check_time: Time to check (default: now)

        Returns:
            True if in pre-open session
        """
        if check_time is None:
            check_time = datetime.now(IST)

        if not self.is_trading_day(check_time.date()):
            return False

        current_time = check_time.time()
        return self.PRE_OPEN_START <= current_time <= self.PRE_OPEN_END

    def get_next_trading_day(self, from_date: date | None = None) -> date:
        """Get next trading day.

        Args:
            from_date: Starting date (default: today)

        Returns:
            Next trading day
        """
        if from_date is None:
            from_date = datetime.now(IST).date()

        next_day = from_date + timedelta(days=1)
        while not self.is_trading_day(next_day):
            next_day += timedelta(days=1)

        return next_day

    def get_previous_trading_day(self, from_date: date | None = None) -> date:
        """Get previous trading day.

        Args:
            from_date: Starting date (default: today)

        Returns:
            Previous trading day
        """
        if from_date is None:
            from_date = datetime.now(IST).date()

        prev_day = from_date - timedelta(days=1)
        while not self.is_trading_day(prev_day):
            prev_day -= timedelta(days=1)

        return prev_day

    def _get_expiry_weekday(self, symbol: str) -> int:
        """Get expiry weekday for a symbol.

        Args:
            symbol: Index symbol

        Returns:
            Weekday (0=Monday, 6=Sunday)
        """
        # Normalize symbol
        symbol_upper = symbol.upper()
        for key, weekday in self.EXPIRY_WEEKDAYS.items():
            if key in symbol_upper:
                return weekday

        # Default to Thursday for unknown symbols
        return 3

    def get_expiry_date(
        self, symbol: str, expiry_type: ExpiryType, from_date: date | None = None
    ) -> date:
        """Get next expiry date for a symbol.

        Args:
            symbol: Index symbol (NIFTY, BANKNIFTY)
            expiry_type: WEEKLY or MONTHLY
            from_date: Starting date (default: today)

        Returns:
            Expiry date
        """
        if from_date is None:
            from_date = datetime.now(IST).date()

        expiry_weekday = self._get_expiry_weekday(symbol)

        # Find next occurrence of expiry weekday
        days_until = (expiry_weekday - from_date.weekday()) % 7
        if days_until == 0 and from_date.weekday() == expiry_weekday:
            # Today is expiry day
            if datetime.now(IST).time() > self.MARKET_CLOSE:
                days_until = 7  # Move to next week

        expiry = from_date + timedelta(days=days_until)

        # If expiry is a holiday, move to previous trading day
        while not self.is_trading_day(expiry):
            expiry -= timedelta(days=1)

        if expiry_type == ExpiryType.MONTHLY:
            # Monthly expiry is last expiry of the month
            while True:
                next_expiry = expiry + timedelta(days=7)
                # Adjust for holidays
                while not self.is_trading_day(next_expiry):
                    next_expiry -= timedelta(days=1)

                if next_expiry.month != expiry.month:
                    break
                expiry = next_expiry

        return expiry

    def next_expiry(
        self, symbol: str, expiry_type: ExpiryType = ExpiryType.WEEKLY
    ) -> ExpiryInfo:
        """Get next expiry info.

        Args:
            symbol: Index symbol
            expiry_type: WEEKLY or MONTHLY

        Returns:
            ExpiryInfo object
        """
        today = datetime.now(IST).date()
        expiry = self.get_expiry_date(symbol, expiry_type, today)
        days_to_expiry = self.days_to_expiry(expiry)

        return ExpiryInfo(
            expiry_date=expiry,
            symbol=symbol,
            expiry_type=expiry_type,
            days_to_expiry=days_to_expiry,
        )

    def days_to_expiry(self, expiry_date: date) -> int:
        """Calculate days to expiry.

        Args:
            expiry_date: Expiry date

        Returns:
            Number of trading days to expiry
        """
        today = datetime.now(IST).date()
        if expiry_date <= today:
            return 0

        days = 0
        current = today + timedelta(days=1)
        while current <= expiry_date:
            if self.is_trading_day(current):
                days += 1
            current += timedelta(days=1)

        return days

    def is_expiry_day(self, symbol: str, check_date: date | None = None) -> bool:
        """Check if date is expiry day for symbol.

        Args:
            symbol: Index symbol
            check_date: Date to check (default: today)

        Returns:
            True if expiry day
        """
        if check_date is None:
            check_date = datetime.now(IST).date()

        # Get weekly expiry
        expiry = self.get_expiry_date(symbol, ExpiryType.WEEKLY, check_date)

        # Also check if it was moved from original day due to holiday
        return expiry == check_date

    def is_monthly_expiry(self, symbol: str, check_date: date | None = None) -> bool:
        """Check if date is monthly expiry for symbol.

        Args:
            symbol: Index symbol
            check_date: Date to check (default: today)

        Returns:
            True if monthly expiry
        """
        if check_date is None:
            check_date = datetime.now(IST).date()

        monthly_expiry = self.get_expiry_date(symbol, ExpiryType.MONTHLY, check_date)
        return monthly_expiry == check_date

    def expiry_week_flag(self, symbol: str) -> bool:
        """Check if current week is expiry week.

        Args:
            symbol: Index symbol

        Returns:
            True if expiry week
        """
        today = datetime.now(IST).date()
        expiry = self.next_expiry(symbol, ExpiryType.WEEKLY)

        # Same week if expiry is within 7 days and same calendar week
        days_diff = (expiry.expiry_date - today).days
        return days_diff <= 4 and days_diff >= 0

    def upcoming_events(
        self, days_ahead: int = 7, impact_filter: EventImpact | None = None
    ) -> list[MarketEvent]:
        """Get upcoming events.

        Args:
            days_ahead: Number of days to look ahead
            impact_filter: Filter by impact level

        Returns:
            List of upcoming events
        """
        today = datetime.now(IST).date()
        end_date = today + timedelta(days=days_ahead)

        events = [
            e for e in self._events if today <= e.event_date <= end_date
        ]

        if impact_filter:
            events = [e for e in events if e.impact == impact_filter]

        return sorted(events, key=lambda x: x.event_date)

    def is_event_blackout(self, check_time: datetime | None = None) -> bool:
        """Check if currently in event blackout period.

        Blackout = HIGH-impact event within 24h.

        Args:
            check_time: Time to check (default: now)

        Returns:
            True if in blackout period
        """
        if check_time is None:
            check_time = datetime.now(IST)

        for event in self._events:
            if event.impact != EventImpact.HIGH:
                continue

            if event.blackout_start and event.blackout_end:
                if event.blackout_start <= check_time <= event.blackout_end:
                    logger.info(
                        "event_blackout_active",
                        event=event.name,
                        blackout_end=event.blackout_end.isoformat(),
                    )
                    return True

            # Default 24h blackout for HIGH impact events
            event_datetime = datetime.combine(event.event_date, time(0, 0), IST)
            if event_datetime - timedelta(hours=24) <= check_time <= event_datetime + timedelta(hours=2):
                logger.info(
                    "event_blackout_active",
                    event=event.name,
                    event_date=event.event_date.isoformat(),
                )
                return True

        return False

    def get_trading_days_in_month(
        self, year: int, month: int
    ) -> list[date]:
        """Get all trading days in a month.

        Args:
            year: Year
            month: Month

        Returns:
            List of trading days
        """
        from calendar import monthrange

        _, last_day = monthrange(year, month)
        trading_days = []

        for day in range(1, last_day + 1):
            d = date(year, month, day)
            if self.is_trading_day(d):
                trading_days.append(d)

        return trading_days

    def minutes_to_market_close(self) -> int:
        """Get minutes until market close.

        Returns:
            Minutes to close (0 if market closed)
        """
        now = datetime.now(IST)

        if not self.is_market_open(now):
            return 0

        close_datetime = datetime.combine(now.date(), self.MARKET_CLOSE, IST)
        diff = close_datetime - now
        return int(diff.total_seconds() / 60)

    def minutes_since_market_open(self) -> int:
        """Get minutes since market open.

        Returns:
            Minutes since open (0 if market not open yet)
        """
        now = datetime.now(IST)

        if not self.is_trading_day(now.date()):
            return 0

        open_datetime = datetime.combine(now.date(), self.MARKET_OPEN, IST)

        if now < open_datetime:
            return 0

        diff = now - open_datetime
        return int(diff.total_seconds() / 60)
