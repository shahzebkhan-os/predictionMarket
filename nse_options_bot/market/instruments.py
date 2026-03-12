"""Instrument master with lot sizes and token mapping.

Lot sizes: Always fetch from kite.instruments("NFO") — do not hardcode.
Provides caching and lookup for instruments.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytz
import structlog

from nse_options_bot.brokers.base import BaseBroker, Exchange, Instrument, OptionType
from nse_options_bot.market.nse_calendar import ExpiryType, NseCalendar

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class OptionInstrument:
    """Option instrument with parsed details."""

    instrument: Instrument
    underlying: str
    strike: Decimal
    option_type: OptionType
    expiry: date
    expiry_type: ExpiryType
    lot_size: int
    is_weekly: bool
    is_monthly: bool


@dataclass
class FutureInstrument:
    """Future instrument with parsed details."""

    instrument: Instrument
    underlying: str
    expiry: date
    lot_size: int


class InstrumentMaster:
    """Instrument master for NSE F&O instruments.

    Handles:
    - Fetching instruments from broker
    - Caching and lookup by various keys
    - Parsing option symbols
    - Lot size management
    """

    # Supported underlyings
    SUPPORTED_UNDERLYINGS = {
        "NIFTY": "NIFTY 50",
        "BANKNIFTY": "NIFTY BANK",
        "FINNIFTY": "NIFTY FIN SERVICE",
        "MIDCPNIFTY": "NIFTY MID SELECT",
        "RELIANCE": "RELIANCE",
        "TCS": "TCS",
        "HDFCBANK": "HDFC BANK",
        "INFY": "INFOSYS",
    }

    # Symbol pattern for options: NIFTY24D1925500CE
    OPTION_SYMBOL_PATTERN = re.compile(
        r"^(?P<underlying>[A-Z]+)"
        r"(?P<year>\d{2})"
        r"(?P<month>[A-Z0-9]+)"
        r"(?P<day>\d{2})?"
        r"(?P<strike>\d+)"
        r"(?P<type>CE|PE)$"
    )

    # Month codes for options
    MONTH_CODES = {
        "JAN": 1, "1": 1, "A": 1,
        "FEB": 2, "2": 2, "B": 2,
        "MAR": 3, "3": 3, "C": 3,
        "APR": 4, "4": 4, "D": 4,
        "MAY": 5, "5": 5, "E": 5,
        "JUN": 6, "6": 6, "F": 6,
        "JUL": 7, "7": 7, "G": 7,
        "AUG": 8, "8": 8, "H": 8,
        "SEP": 9, "9": 9, "I": 9,
        "OCT": 10, "10": 10, "O": 10,
        "NOV": 11, "11": 11, "N": 11,
        "DEC": 12, "12": 12, "D": 12,
    }

    def __init__(self, calendar: NseCalendar | None = None) -> None:
        """Initialize instrument master.

        Args:
            calendar: NSE calendar instance
        """
        self._calendar = calendar or NseCalendar()
        self._instruments: dict[int, Instrument] = {}  # token -> instrument
        self._symbol_map: dict[str, Instrument] = {}  # exchange:symbol -> instrument
        self._options: dict[str, list[OptionInstrument]] = {}  # underlying -> options
        self._futures: dict[str, list[FutureInstrument]] = {}  # underlying -> futures
        self._lot_sizes: dict[str, int] = {}  # symbol -> lot size
        self._last_refresh: datetime | None = None

    @property
    def is_loaded(self) -> bool:
        """Check if instruments are loaded."""
        return len(self._instruments) > 0

    async def load_instruments(self, broker: BaseBroker) -> int:
        """Load instruments from broker.

        Args:
            broker: Broker instance

        Returns:
            Number of instruments loaded
        """
        logger.info("loading_instruments")

        # Fetch NFO instruments
        instruments = await broker.get_instruments(Exchange.NFO)

        self._instruments.clear()
        self._symbol_map.clear()
        self._options.clear()
        self._futures.clear()
        self._lot_sizes.clear()

        for instrument in instruments:
            # Store by token
            self._instruments[instrument.instrument_token] = instrument

            # Store by exchange:symbol
            key = f"{instrument.exchange.value}:{instrument.tradingsymbol}"
            self._symbol_map[key] = instrument

            # Store lot size
            self._lot_sizes[instrument.tradingsymbol] = instrument.lot_size

            # Parse and categorize
            if instrument.is_option:
                parsed = self._parse_option_symbol(instrument)
                if parsed:
                    underlying = parsed.underlying
                    if underlying not in self._options:
                        self._options[underlying] = []
                    self._options[underlying].append(parsed)

            elif instrument.is_future:
                parsed = self._parse_future_symbol(instrument)
                if parsed:
                    underlying = parsed.underlying
                    if underlying not in self._futures:
                        self._futures[underlying] = []
                    self._futures[underlying].append(parsed)

        self._last_refresh = datetime.now(IST)

        logger.info(
            "instruments_loaded",
            total=len(instruments),
            options=sum(len(opts) for opts in self._options.values()),
            futures=sum(len(futs) for futs in self._futures.values()),
        )

        return len(instruments)

    def _parse_option_symbol(self, instrument: Instrument) -> OptionInstrument | None:
        """Parse option symbol.

        Args:
            instrument: Instrument object

        Returns:
            Parsed OptionInstrument or None
        """
        symbol = instrument.tradingsymbol
        match = self.OPTION_SYMBOL_PATTERN.match(symbol)

        if not match:
            # Try simpler parsing
            for underlying in self.SUPPORTED_UNDERLYINGS:
                if symbol.startswith(underlying):
                    try:
                        rest = symbol[len(underlying) :]
                        option_type = OptionType.CE if rest.endswith("CE") else OptionType.PE
                        strike_str = rest[:-2]
                        # Extract numeric strike
                        strike_match = re.search(r"(\d+)$", strike_str)
                        if strike_match:
                            strike = Decimal(strike_match.group(1))
                            return OptionInstrument(
                                instrument=instrument,
                                underlying=underlying,
                                strike=strike,
                                option_type=option_type,
                                expiry=instrument.expiry.date()
                                if instrument.expiry
                                else date.today(),
                                expiry_type=ExpiryType.WEEKLY,  # Assume weekly
                                lot_size=instrument.lot_size,
                                is_weekly=True,
                                is_monthly=False,
                            )
                    except (ValueError, AttributeError):
                        continue
            return None

        underlying = match.group("underlying")
        if underlying not in self.SUPPORTED_UNDERLYINGS:
            return None

        year = 2000 + int(match.group("year"))
        month_code = match.group("month")
        day_str = match.group("day")
        strike = Decimal(match.group("strike"))
        option_type = OptionType(match.group("type"))

        # Parse month
        month = self.MONTH_CODES.get(month_code.upper(), 1)

        # Determine expiry
        if day_str:
            # Weekly expiry: NIFTY24D1925500CE
            day = int(day_str)
            expiry = date(year, month, day)
            is_weekly = True
        else:
            # Monthly expiry: NIFTY24DEC25500CE
            expiry = instrument.expiry.date() if instrument.expiry else date(year, month, 1)
            is_weekly = False

        # Check if monthly expiry
        monthly_expiry = self._calendar.get_expiry_date(
            underlying, ExpiryType.MONTHLY, expiry
        )
        is_monthly = expiry == monthly_expiry

        return OptionInstrument(
            instrument=instrument,
            underlying=underlying,
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            expiry_type=ExpiryType.MONTHLY if is_monthly else ExpiryType.WEEKLY,
            lot_size=instrument.lot_size,
            is_weekly=is_weekly,
            is_monthly=is_monthly,
        )

    def _parse_future_symbol(self, instrument: Instrument) -> FutureInstrument | None:
        """Parse future symbol.

        Args:
            instrument: Instrument object

        Returns:
            Parsed FutureInstrument or None
        """
        symbol = instrument.tradingsymbol

        for underlying in self.SUPPORTED_UNDERLYINGS:
            if symbol.startswith(underlying) and "FUT" in symbol:
                return FutureInstrument(
                    instrument=instrument,
                    underlying=underlying,
                    expiry=instrument.expiry.date()
                    if instrument.expiry
                    else date.today(),
                    lot_size=instrument.lot_size,
                )

        return None

    def get_by_token(self, token: int) -> Instrument | None:
        """Get instrument by token.

        Args:
            token: Instrument token

        Returns:
            Instrument or None
        """
        return self._instruments.get(token)

    def get_by_symbol(
        self, tradingsymbol: str, exchange: Exchange = Exchange.NFO
    ) -> Instrument | None:
        """Get instrument by symbol.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange

        Returns:
            Instrument or None
        """
        key = f"{exchange.value}:{tradingsymbol}"
        return self._symbol_map.get(key)

    def get_lot_size(self, tradingsymbol: str) -> int:
        """Get lot size for a symbol.

        Args:
            tradingsymbol: Trading symbol

        Returns:
            Lot size (default 1)
        """
        return self._lot_sizes.get(tradingsymbol, 1)

    def get_options_for_underlying(
        self,
        underlying: str,
        expiry: date | None = None,
        option_type: OptionType | None = None,
        strike_range: tuple[Decimal, Decimal] | None = None,
    ) -> list[OptionInstrument]:
        """Get options for an underlying.

        Args:
            underlying: Underlying symbol
            expiry: Filter by expiry date
            option_type: Filter by CE/PE
            strike_range: Filter by strike range (min, max)

        Returns:
            List of matching options
        """
        options = self._options.get(underlying.upper(), [])

        if expiry:
            options = [o for o in options if o.expiry == expiry]

        if option_type:
            options = [o for o in options if o.option_type == option_type]

        if strike_range:
            min_strike, max_strike = strike_range
            options = [o for o in options if min_strike <= o.strike <= max_strike]

        return sorted(options, key=lambda x: (x.expiry, x.strike, x.option_type.value))

    def get_strikes_for_expiry(
        self, underlying: str, expiry: date
    ) -> list[Decimal]:
        """Get all available strikes for an expiry.

        Args:
            underlying: Underlying symbol
            expiry: Expiry date

        Returns:
            Sorted list of strikes
        """
        options = self.get_options_for_underlying(underlying, expiry=expiry)
        strikes = {o.strike for o in options}
        return sorted(strikes)

    def get_expiries_for_underlying(self, underlying: str) -> list[date]:
        """Get all available expiries for an underlying.

        Args:
            underlying: Underlying symbol

        Returns:
            Sorted list of expiry dates
        """
        options = self._options.get(underlying.upper(), [])
        expiries = {o.expiry for o in options}
        return sorted(expiries)

    def get_atm_strike(
        self, underlying: str, spot_price: Decimal, expiry: date
    ) -> Decimal:
        """Get ATM strike for spot price.

        Args:
            underlying: Underlying symbol
            spot_price: Current spot price
            expiry: Expiry date

        Returns:
            ATM strike
        """
        strikes = self.get_strikes_for_expiry(underlying, expiry)
        if not strikes:
            # Default strike step
            step = Decimal("50") if underlying in ("NIFTY", "FINNIFTY") else Decimal("100")
            return (spot_price / step).quantize(Decimal("1")) * step

        # Find closest strike
        closest = min(strikes, key=lambda x: abs(x - spot_price))
        return closest

    def get_strike_step(self, underlying: str) -> Decimal:
        """Get strike step for underlying.

        Args:
            underlying: Underlying symbol

        Returns:
            Strike step
        """
        strike_steps = {
            "NIFTY": Decimal("50"),
            "BANKNIFTY": Decimal("100"),
            "FINNIFTY": Decimal("50"),
            "MIDCPNIFTY": Decimal("25"),
        }
        return strike_steps.get(underlying.upper(), Decimal("50"))

    def build_option_symbol(
        self,
        underlying: str,
        expiry: date,
        strike: Decimal,
        option_type: OptionType,
    ) -> str:
        """Build option symbol from components.

        Args:
            underlying: Underlying symbol
            expiry: Expiry date
            strike: Strike price
            option_type: CE or PE

        Returns:
            Trading symbol
        """
        # Format: NIFTY24D1925500CE
        year = expiry.year % 100
        month = expiry.month
        day = expiry.day

        # Use month letter for weeklies
        month_letters = "ABCDEFGHIJKL"
        month_letter = month_letters[month - 1] if month <= 12 else str(month)

        # Check if monthly (last Thursday/Wednesday)
        monthly_expiry = self._calendar.get_expiry_date(
            underlying, ExpiryType.MONTHLY, expiry
        )
        is_monthly = expiry == monthly_expiry

        if is_monthly:
            # Monthly: NIFTY24DEC25500CE
            month_names = [
                "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
            ]
            month_str = month_names[month - 1]
            return f"{underlying}{year}{month_str}{int(strike)}{option_type.value}"
        else:
            # Weekly: NIFTY24D1925500CE
            return f"{underlying}{year}{month_letter}{day:02d}{int(strike)}{option_type.value}"

    def get_option_chain_instruments(
        self, underlying: str, expiry: date, strikes_around_atm: int = 20
    ) -> dict[str, dict[Decimal, OptionInstrument]]:
        """Get option chain instruments around ATM.

        Args:
            underlying: Underlying symbol
            expiry: Expiry date
            strikes_around_atm: Number of strikes on each side

        Returns:
            Dict with 'CE' and 'PE' keys mapping strike to OptionInstrument
        """
        options = self.get_options_for_underlying(underlying, expiry=expiry)

        result: dict[str, dict[Decimal, OptionInstrument]] = {"CE": {}, "PE": {}}

        for option in options:
            opt_type = option.option_type.value
            result[opt_type][option.strike] = option

        return result

    def get_futures_for_underlying(
        self, underlying: str, expiry: date | None = None
    ) -> list[FutureInstrument]:
        """Get futures for an underlying.

        Args:
            underlying: Underlying symbol
            expiry: Optional expiry filter

        Returns:
            List of futures
        """
        futures = self._futures.get(underlying.upper(), [])

        if expiry:
            futures = [f for f in futures if f.expiry == expiry]

        return sorted(futures, key=lambda x: x.expiry)

    def get_current_month_future(self, underlying: str) -> FutureInstrument | None:
        """Get current month future.

        Args:
            underlying: Underlying symbol

        Returns:
            Current month future or None
        """
        futures = self.get_futures_for_underlying(underlying)
        if not futures:
            return None

        today = datetime.now(IST).date()
        for future in futures:
            if future.expiry >= today:
                return future

        return futures[-1] if futures else None
