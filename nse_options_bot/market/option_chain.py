"""Live option chain snapshot with OI, IV, and Greeks.

Refreshes every 5s via KiteTicker.
Per strike: ltp, bid, ask, oi, oi_change, volume, iv, delta, gamma, theta, vega, token.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytz
import structlog
from scipy.stats import norm

from nse_options_bot.brokers.base import BaseBroker, Exchange, OptionType, Quote
from nse_options_bot.market.instruments import InstrumentMaster, OptionInstrument
from nse_options_bot.market.nse_calendar import NseCalendar

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class OptionGreeks:
    """Option Greeks for a single strike."""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


@dataclass
class OptionStrikeData:
    """Data for a single option strike."""

    strike: Decimal
    option_type: OptionType
    tradingsymbol: str
    instrument_token: int
    lot_size: int

    # Price data
    ltp: Decimal = Decimal("0")
    bid: Decimal = Decimal("0")
    ask: Decimal = Decimal("0")
    bid_qty: int = 0
    ask_qty: int = 0

    # Volume & OI
    volume: int = 0
    oi: int = 0
    oi_change: int = 0
    prev_oi: int = 0

    # IV & Greeks
    iv: float = 0.0
    greeks: OptionGreeks = field(default_factory=OptionGreeks)

    # Timestamp
    last_update: datetime = field(default_factory=lambda: datetime.now(IST))


@dataclass
class OptionChainStrike:
    """Combined CE and PE data for a strike."""

    strike: Decimal
    ce: OptionStrikeData | None = None
    pe: OptionStrikeData | None = None

    @property
    def total_oi(self) -> int:
        """Total OI at strike."""
        ce_oi = self.ce.oi if self.ce else 0
        pe_oi = self.pe.oi if self.pe else 0
        return ce_oi + pe_oi

    @property
    def pcr_at_strike(self) -> float:
        """PCR at this strike."""
        ce_oi = self.ce.oi if self.ce else 0
        pe_oi = self.pe.oi if self.pe else 0
        if ce_oi == 0:
            return float("inf") if pe_oi > 0 else 0.0
        return pe_oi / ce_oi


class GreeksCalculator:
    """Black-Scholes Greeks calculator."""

    def __init__(self, risk_free_rate: float = 0.07) -> None:
        """Initialize calculator.

        Args:
            risk_free_rate: Risk-free interest rate (7% for India)
        """
        self.risk_free_rate = risk_free_rate

    def calculate_d1_d2(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
    ) -> tuple[float, float]:
        """Calculate d1 and d2 for Black-Scholes.

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility

        Returns:
            Tuple of (d1, d2)
        """
        if time_to_expiry <= 0 or volatility <= 0:
            return 0.0, 0.0

        sqrt_t = np.sqrt(time_to_expiry)
        d1 = (
            np.log(spot / strike)
            + (self.risk_free_rate + 0.5 * volatility**2) * time_to_expiry
        ) / (volatility * sqrt_t)
        d2 = d1 - volatility * sqrt_t

        return float(d1), float(d2)

    def implied_volatility(
        self,
        option_price: float,
        spot: float,
        strike: float,
        time_to_expiry: float,
        is_call: bool,
        precision: float = 0.0001,
        max_iterations: int = 100,
    ) -> float:
        """Calculate implied volatility using Newton-Raphson.

        Args:
            option_price: Option price
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            is_call: True if call option
            precision: Desired precision
            max_iterations: Maximum iterations

        Returns:
            Implied volatility
        """
        if option_price <= 0 or time_to_expiry <= 0:
            return 0.0

        # Initial guess
        sigma = 0.3

        for _ in range(max_iterations):
            price = self.option_price(spot, strike, time_to_expiry, sigma, is_call)
            vega = self.vega(spot, strike, time_to_expiry, sigma)

            if abs(vega) < 1e-10:
                break

            diff = option_price - price
            if abs(diff) < precision:
                return sigma

            sigma = sigma + diff / vega

            # Bound sigma to reasonable range
            sigma = max(0.01, min(5.0, sigma))

        return sigma

    def option_price(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        is_call: bool,
    ) -> float:
        """Calculate Black-Scholes option price.

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility
            is_call: True if call option

        Returns:
            Option price
        """
        if time_to_expiry <= 0:
            # At expiry
            if is_call:
                return max(0, spot - strike)
            else:
                return max(0, strike - spot)

        d1, d2 = self.calculate_d1_d2(spot, strike, time_to_expiry, volatility)
        discount = np.exp(-self.risk_free_rate * time_to_expiry)

        if is_call:
            return spot * norm.cdf(d1) - strike * discount * norm.cdf(d2)
        else:
            return strike * discount * norm.cdf(-d2) - spot * norm.cdf(-d1)

    def delta(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        is_call: bool,
    ) -> float:
        """Calculate delta.

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility
            is_call: True if call option

        Returns:
            Delta
        """
        if time_to_expiry <= 0:
            if is_call:
                return 1.0 if spot > strike else 0.0
            else:
                return -1.0 if spot < strike else 0.0

        d1, _ = self.calculate_d1_d2(spot, strike, time_to_expiry, volatility)

        if is_call:
            return float(norm.cdf(d1))
        else:
            return float(norm.cdf(d1) - 1)

    def gamma(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
    ) -> float:
        """Calculate gamma.

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility

        Returns:
            Gamma
        """
        if time_to_expiry <= 0 or volatility <= 0:
            return 0.0

        d1, _ = self.calculate_d1_d2(spot, strike, time_to_expiry, volatility)
        return float(norm.pdf(d1) / (spot * volatility * np.sqrt(time_to_expiry)))

    def theta(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        is_call: bool,
    ) -> float:
        """Calculate theta (per day).

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility
            is_call: True if call option

        Returns:
            Theta (per day)
        """
        if time_to_expiry <= 0:
            return 0.0

        d1, d2 = self.calculate_d1_d2(spot, strike, time_to_expiry, volatility)
        sqrt_t = np.sqrt(time_to_expiry)
        discount = np.exp(-self.risk_free_rate * time_to_expiry)

        term1 = -spot * norm.pdf(d1) * volatility / (2 * sqrt_t)

        if is_call:
            term2 = -self.risk_free_rate * strike * discount * norm.cdf(d2)
        else:
            term2 = self.risk_free_rate * strike * discount * norm.cdf(-d2)

        # Convert to daily theta
        return float((term1 + term2) / 365)

    def vega(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
    ) -> float:
        """Calculate vega (per 1% change in volatility).

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility

        Returns:
            Vega
        """
        if time_to_expiry <= 0:
            return 0.0

        d1, _ = self.calculate_d1_d2(spot, strike, time_to_expiry, volatility)
        return float(spot * norm.pdf(d1) * np.sqrt(time_to_expiry) / 100)

    def calculate_greeks(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        is_call: bool,
    ) -> OptionGreeks:
        """Calculate all Greeks.

        Args:
            spot: Spot price
            strike: Strike price
            time_to_expiry: Time to expiry in years
            volatility: Implied volatility
            is_call: True if call option

        Returns:
            OptionGreeks object
        """
        return OptionGreeks(
            delta=self.delta(spot, strike, time_to_expiry, volatility, is_call),
            gamma=self.gamma(spot, strike, time_to_expiry, volatility),
            theta=self.theta(spot, strike, time_to_expiry, volatility, is_call),
            vega=self.vega(spot, strike, time_to_expiry, volatility),
        )


class OptionChainSnapshot:
    """Live option chain snapshot with auto-refresh.

    Provides:
    - Per strike data: ltp, bid, ask, oi, oi_change, volume, iv, delta, gamma, theta, vega
    - Aggregate metrics: PCR, max pain, GEX, IV skew
    """

    def __init__(
        self,
        underlying: str,
        expiry: date,
        spot_price: Decimal,
        instrument_master: InstrumentMaster,
        calendar: NseCalendar | None = None,
    ) -> None:
        """Initialize option chain snapshot.

        Args:
            underlying: Underlying symbol (NIFTY, BANKNIFTY)
            expiry: Expiry date
            spot_price: Current spot price
            instrument_master: Instrument master
            calendar: NSE calendar
        """
        self._underlying = underlying
        self._expiry = expiry
        self._spot_price = spot_price
        self._instrument_master = instrument_master
        self._calendar = calendar or NseCalendar()
        self._greeks_calc = GreeksCalculator()

        # Chain data: strike -> OptionChainStrike
        self._chain: dict[Decimal, OptionChainStrike] = {}

        # Metadata
        self._last_refresh: datetime | None = None
        self._prev_oi_data: dict[str, int] = {}  # symbol -> prev OI

        # Initialize chain structure
        self._initialize_chain()

    def _initialize_chain(self) -> None:
        """Initialize chain structure from instrument master."""
        options = self._instrument_master.get_options_for_underlying(
            self._underlying, expiry=self._expiry
        )

        for option in options:
            strike = option.strike
            if strike not in self._chain:
                self._chain[strike] = OptionChainStrike(strike=strike)

            strike_data = OptionStrikeData(
                strike=strike,
                option_type=option.option_type,
                tradingsymbol=option.instrument.tradingsymbol,
                instrument_token=option.instrument.instrument_token,
                lot_size=option.lot_size,
            )

            if option.option_type == OptionType.CE:
                self._chain[strike].ce = strike_data
            else:
                self._chain[strike].pe = strike_data

    @property
    def underlying(self) -> str:
        """Get underlying symbol."""
        return self._underlying

    @property
    def expiry(self) -> date:
        """Get expiry date."""
        return self._expiry

    @property
    def spot_price(self) -> Decimal:
        """Get spot price."""
        return self._spot_price

    @spot_price.setter
    def spot_price(self, value: Decimal) -> None:
        """Set spot price."""
        self._spot_price = value

    @property
    def time_to_expiry_years(self) -> float:
        """Get time to expiry in years."""
        days = self._calendar.days_to_expiry(self._expiry)
        return days / 365.0

    def get_atm_strike(self) -> Decimal:
        """Get ATM strike."""
        return self._instrument_master.get_atm_strike(
            self._underlying, self._spot_price, self._expiry
        )

    def get_instrument_tokens(self) -> list[int]:
        """Get all instrument tokens for subscription.

        Returns:
            List of instrument tokens
        """
        tokens = []
        for strike_data in self._chain.values():
            if strike_data.ce:
                tokens.append(strike_data.ce.instrument_token)
            if strike_data.pe:
                tokens.append(strike_data.pe.instrument_token)
        return tokens

    def update_from_quotes(self, quotes: dict[str, Quote]) -> None:
        """Update chain from quote data.

        Args:
            quotes: Dict of quotes keyed by exchange:symbol
        """
        for strike_data in self._chain.values():
            for option in [strike_data.ce, strike_data.pe]:
                if not option:
                    continue

                key = f"{Exchange.NFO.value}:{option.tradingsymbol}"
                quote = quotes.get(key)

                if quote:
                    # Store previous OI for change calculation
                    if option.tradingsymbol in self._prev_oi_data:
                        option.prev_oi = self._prev_oi_data[option.tradingsymbol]
                    self._prev_oi_data[option.tradingsymbol] = quote.oi

                    # Update price data
                    option.ltp = quote.last_price
                    option.volume = quote.volume
                    option.oi = quote.oi
                    option.oi_change = option.oi - option.prev_oi

                    # Extract bid/ask from depth
                    if quote.depth:
                        buy_depth = quote.depth.get("buy", [])
                        sell_depth = quote.depth.get("sell", [])
                        if buy_depth:
                            option.bid = Decimal(str(buy_depth[0].get("price", 0)))
                            option.bid_qty = buy_depth[0].get("quantity", 0)
                        if sell_depth:
                            option.ask = Decimal(str(sell_depth[0].get("price", 0)))
                            option.ask_qty = sell_depth[0].get("quantity", 0)

                    option.last_update = datetime.now(IST)

        self._last_refresh = datetime.now(IST)
        self._calculate_greeks()

    def update_from_ticks(self, ticks: list[dict[str, Any]]) -> None:
        """Update chain from tick data.

        Args:
            ticks: List of tick dicts from KiteTicker
        """
        token_map: dict[int, OptionStrikeData] = {}
        for strike_data in self._chain.values():
            if strike_data.ce:
                token_map[strike_data.ce.instrument_token] = strike_data.ce
            if strike_data.pe:
                token_map[strike_data.pe.instrument_token] = strike_data.pe

        for tick in ticks:
            token = tick.get("instrument_token")
            option = token_map.get(token)

            if option:
                option.ltp = Decimal(str(tick.get("last_price", 0)))
                option.volume = tick.get("volume_traded", 0)

                if "oi" in tick:
                    if option.tradingsymbol in self._prev_oi_data:
                        option.prev_oi = self._prev_oi_data[option.tradingsymbol]
                    self._prev_oi_data[option.tradingsymbol] = tick["oi"]
                    option.oi = tick["oi"]
                    option.oi_change = option.oi - option.prev_oi

                depth = tick.get("depth", {})
                if depth:
                    buy = depth.get("buy", [])
                    sell = depth.get("sell", [])
                    if buy:
                        option.bid = Decimal(str(buy[0].get("price", 0)))
                        option.bid_qty = buy[0].get("quantity", 0)
                    if sell:
                        option.ask = Decimal(str(sell[0].get("price", 0)))
                        option.ask_qty = sell[0].get("quantity", 0)

                option.last_update = datetime.now(IST)

        self._last_refresh = datetime.now(IST)
        self._calculate_greeks()

    def _calculate_greeks(self) -> None:
        """Calculate IV and Greeks for all strikes."""
        spot = float(self._spot_price)
        tte = self.time_to_expiry_years

        for strike_data in self._chain.values():
            strike = float(strike_data.strike)

            for option, is_call in [(strike_data.ce, True), (strike_data.pe, False)]:
                if not option or float(option.ltp) <= 0:
                    continue

                # Calculate IV
                option.iv = self._greeks_calc.implied_volatility(
                    float(option.ltp), spot, strike, tte, is_call
                )

                # Calculate Greeks
                if option.iv > 0:
                    option.greeks = self._greeks_calc.calculate_greeks(
                        spot, strike, tte, option.iv, is_call
                    )

    def get_pcr(self) -> float:
        """Get Put-Call Ratio based on OI.

        Returns:
            PCR value
        """
        total_pe_oi = 0
        total_ce_oi = 0

        for strike_data in self._chain.values():
            if strike_data.pe:
                total_pe_oi += strike_data.pe.oi
            if strike_data.ce:
                total_ce_oi += strike_data.ce.oi

        if total_ce_oi == 0:
            return 0.0

        return total_pe_oi / total_ce_oi

    def get_iv_skew(self) -> float:
        """Get IV skew (25-delta PE IV - 25-delta CE IV).

        Returns:
            IV skew in percentage points
        """
        # Find 25-delta options
        ce_25_delta: OptionStrikeData | None = None
        pe_25_delta: OptionStrikeData | None = None

        for strike_data in self._chain.values():
            if strike_data.ce and 0.20 <= strike_data.ce.greeks.delta <= 0.30:
                if ce_25_delta is None or abs(strike_data.ce.greeks.delta - 0.25) < abs(
                    ce_25_delta.greeks.delta - 0.25
                ):
                    ce_25_delta = strike_data.ce

            if strike_data.pe and -0.30 <= strike_data.pe.greeks.delta <= -0.20:
                if pe_25_delta is None or abs(
                    strike_data.pe.greeks.delta + 0.25
                ) < abs(pe_25_delta.greeks.delta + 0.25):
                    pe_25_delta = strike_data.pe

        if ce_25_delta and pe_25_delta and ce_25_delta.iv > 0 and pe_25_delta.iv > 0:
            return (pe_25_delta.iv - ce_25_delta.iv) * 100

        return 0.0

    def get_max_pain(self) -> Decimal:
        """Calculate max pain strike.

        Max pain = strike minimizing total option writers' loss.

        Returns:
            Max pain strike
        """
        if not self._chain:
            return self._spot_price

        strikes = sorted(self._chain.keys())
        min_pain = float("inf")
        max_pain_strike = strikes[len(strikes) // 2]

        for test_strike in strikes:
            total_pain = Decimal("0")

            for strike, strike_data in self._chain.items():
                # CE writer pain if price > strike
                if strike_data.ce and test_strike > strike:
                    ce_pain = (test_strike - strike) * Decimal(
                        str(strike_data.ce.oi * strike_data.ce.lot_size)
                    )
                    total_pain += ce_pain

                # PE writer pain if price < strike
                if strike_data.pe and test_strike < strike:
                    pe_pain = (strike - test_strike) * Decimal(
                        str(strike_data.pe.oi * strike_data.pe.lot_size)
                    )
                    total_pain += pe_pain

            if float(total_pain) < min_pain:
                min_pain = float(total_pain)
                max_pain_strike = test_strike

        return max_pain_strike

    def get_gex(self) -> float:
        """Calculate Gamma Exposure (GEX).

        GEX = Σ(CE_gamma × CE_OI − PE_gamma × PE_OI) × spot × lot_size

        Returns:
            GEX value (positive = range-bound, negative = trending)
        """
        spot = float(self._spot_price)
        total_gex = 0.0

        for strike_data in self._chain.values():
            if strike_data.ce and strike_data.ce.oi > 0:
                ce_gex = (
                    strike_data.ce.greeks.gamma
                    * strike_data.ce.oi
                    * strike_data.ce.lot_size
                    * spot
                )
                total_gex += ce_gex

            if strike_data.pe and strike_data.pe.oi > 0:
                pe_gex = (
                    strike_data.pe.greeks.gamma
                    * strike_data.pe.oi
                    * strike_data.pe.lot_size
                    * spot
                )
                total_gex -= pe_gex

        return total_gex

    def get_oi_at_strike(self, strike: Decimal) -> tuple[int, int]:
        """Get OI at a specific strike.

        Args:
            strike: Strike price

        Returns:
            Tuple of (CE OI, PE OI)
        """
        strike_data = self._chain.get(strike)
        if not strike_data:
            return 0, 0

        ce_oi = strike_data.ce.oi if strike_data.ce else 0
        pe_oi = strike_data.pe.oi if strike_data.pe else 0
        return ce_oi, pe_oi

    def get_max_oi_strikes(self) -> tuple[Decimal, Decimal]:
        """Get strikes with maximum CE and PE OI.

        Returns:
            Tuple of (max CE OI strike, max PE OI strike)
        """
        max_ce_oi = 0
        max_ce_strike = self._spot_price
        max_pe_oi = 0
        max_pe_strike = self._spot_price

        for strike, strike_data in self._chain.items():
            if strike_data.ce and strike_data.ce.oi > max_ce_oi:
                max_ce_oi = strike_data.ce.oi
                max_ce_strike = strike

            if strike_data.pe and strike_data.pe.oi > max_pe_oi:
                max_pe_oi = strike_data.pe.oi
                max_pe_strike = strike

        return max_ce_strike, max_pe_strike

    def get_straddle_price(self, strike: Decimal | None = None) -> Decimal:
        """Get straddle price at a strike.

        Args:
            strike: Strike price (default: ATM)

        Returns:
            Straddle price (CE + PE LTP)
        """
        if strike is None:
            strike = self.get_atm_strike()

        strike_data = self._chain.get(strike)
        if not strike_data:
            return Decimal("0")

        ce_price = strike_data.ce.ltp if strike_data.ce else Decimal("0")
        pe_price = strike_data.pe.ltp if strike_data.pe else Decimal("0")

        return ce_price + pe_price

    def snapshot_to_df(self) -> pd.DataFrame:
        """Convert snapshot to pandas DataFrame.

        Returns:
            DataFrame with chain data
        """
        rows = []

        for strike, strike_data in sorted(self._chain.items()):
            row = {"strike": float(strike)}

            for prefix, option in [("ce_", strike_data.ce), ("pe_", strike_data.pe)]:
                if option:
                    row[f"{prefix}ltp"] = float(option.ltp)
                    row[f"{prefix}bid"] = float(option.bid)
                    row[f"{prefix}ask"] = float(option.ask)
                    row[f"{prefix}oi"] = option.oi
                    row[f"{prefix}oi_change"] = option.oi_change
                    row[f"{prefix}volume"] = option.volume
                    row[f"{prefix}iv"] = option.iv * 100  # Convert to percentage
                    row[f"{prefix}delta"] = option.greeks.delta
                    row[f"{prefix}gamma"] = option.greeks.gamma
                    row[f"{prefix}theta"] = option.greeks.theta
                    row[f"{prefix}vega"] = option.greeks.vega

            rows.append(row)

        return pd.DataFrame(rows)

    def get_chain_summary(self) -> dict[str, Any]:
        """Get chain summary.

        Returns:
            Summary dict
        """
        return {
            "underlying": self._underlying,
            "expiry": self._expiry.isoformat(),
            "spot_price": float(self._spot_price),
            "atm_strike": float(self.get_atm_strike()),
            "pcr": self.get_pcr(),
            "iv_skew": self.get_iv_skew(),
            "max_pain": float(self.get_max_pain()),
            "gex": self.get_gex(),
            "straddle_price": float(self.get_straddle_price()),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "num_strikes": len(self._chain),
        }
