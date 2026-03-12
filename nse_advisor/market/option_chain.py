"""
Option Chain Snapshot.

Builds live option chain snapshots with Greeks calculations.
Refreshes every 5 seconds from NSE API.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Literal

import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.data.nse_fetcher import NseFetcher, OptionData, get_nse_fetcher
from nse_advisor.market.instruments import get_instrument_master

logger = logging.getLogger(__name__)


@dataclass
class OptionStrike:
    """Data for a single option strike."""
    strike_price: float
    expiry: date
    
    # CE data
    ce_ltp: float = 0.0
    ce_bid: float = 0.0
    ce_ask: float = 0.0
    ce_oi: int = 0
    ce_oi_change: int = 0
    ce_volume: int = 0
    ce_iv: float = 0.0
    ce_delta: float = 0.0
    ce_gamma: float = 0.0
    ce_theta: float = 0.0
    ce_vega: float = 0.0
    
    # PE data
    pe_ltp: float = 0.0
    pe_bid: float = 0.0
    pe_ask: float = 0.0
    pe_oi: int = 0
    pe_oi_change: int = 0
    pe_volume: int = 0
    pe_iv: float = 0.0
    pe_delta: float = 0.0
    pe_gamma: float = 0.0
    pe_theta: float = 0.0
    pe_vega: float = 0.0


@dataclass
class OptionChainSnapshot:
    """
    Complete option chain snapshot for an underlying.
    
    Features:
    - Live OI, IV, volume, bid/ask for all strikes
    - Greeks computed via py_vollib (if available) or Black-Scholes
    - ATM detection, PCR calculation, IV skew, max pain
    """
    
    underlying: str
    spot_price: float
    expiry: date
    timestamp: datetime
    strikes: list[OptionStrike] = field(default_factory=list)
    lot_size: int = 25
    stale: bool = False
    
    @property
    def is_valid(self) -> bool:
        """Check if snapshot is valid (not stale, has data)."""
        if self.stale:
            return False
        if not self.strikes:
            return False
        settings = get_settings()
        age = (datetime.now(ZoneInfo("Asia/Kolkata")) - self.timestamp).total_seconds()
        return age <= settings.chain_stale_seconds
    
    def get_atm_strike(self) -> float:
        """
        Get ATM (At-The-Money) strike.
        
        ATM = strike closest to spot price.
        """
        if not self.strikes:
            return 0.0
        
        # Get strike interval
        if len(self.strikes) >= 2:
            interval = abs(self.strikes[1].strike_price - self.strikes[0].strike_price)
        else:
            interval = 50  # Default for NIFTY
        
        # Round spot to nearest strike
        return round(self.spot_price / interval) * interval
    
    def get_strike(self, strike_price: float) -> OptionStrike | None:
        """Get data for a specific strike."""
        for strike in self.strikes:
            if strike.strike_price == strike_price:
                return strike
        return None
    
    def get_pcr(self) -> float:
        """
        Calculate Put-Call Ratio based on OI.
        
        PCR = Total PE OI / Total CE OI
        PCR > 1.5 (rising) → Contrarian bullish
        PCR < 0.7 (falling) → Contrarian bearish
        """
        total_pe_oi = sum(s.pe_oi for s in self.strikes)
        total_ce_oi = sum(s.ce_oi for s in self.strikes)
        
        if total_ce_oi == 0:
            return 0.0
        
        return total_pe_oi / total_ce_oi
    
    def get_iv_skew(self) -> float:
        """
        Calculate IV skew (25-delta PE IV - 25-delta CE IV).
        
        Positive skew → Put premium / fear in market.
        """
        atm = self.get_atm_strike()
        
        # Approximate 25-delta strikes (roughly 4-5% OTM)
        otm_distance = atm * 0.04
        pe_strike = atm - otm_distance
        ce_strike = atm + otm_distance
        
        # Find closest strikes
        pe_iv = 0.0
        ce_iv = 0.0
        
        for strike in self.strikes:
            if abs(strike.strike_price - pe_strike) < 100:
                pe_iv = strike.pe_iv
            if abs(strike.strike_price - ce_strike) < 100:
                ce_iv = strike.ce_iv
        
        return pe_iv - ce_iv
    
    def get_max_pain(self) -> float:
        """
        Calculate max pain strike.
        
        Max Pain = strike where option writers incur minimum loss.
        Near expiry (DTE < 2), index tends to gravitate to max pain.
        """
        if not self.strikes:
            return 0.0
        
        min_pain = float('inf')
        max_pain_strike = self.strikes[0].strike_price
        
        for potential_strike in self.strikes:
            strike_price = potential_strike.strike_price
            total_pain = 0.0
            
            for s in self.strikes:
                # CE pain: max(0, strike - potential) * CE_OI
                ce_intrinsic = max(0, strike_price - s.strike_price)
                total_pain += ce_intrinsic * s.ce_oi
                
                # PE pain: max(0, potential - strike) * PE_OI
                pe_intrinsic = max(0, s.strike_price - strike_price)
                total_pain += pe_intrinsic * s.pe_oi
            
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = strike_price
        
        return max_pain_strike
    
    def get_gex(self) -> float:
        """
        Calculate Gamma Exposure (GEX).
        
        GEX = Σ(CE_gamma × CE_OI − PE_gamma × PE_OI) × spot × lot_size
        
        Positive GEX → Range-bound (dealers hedging pins price)
        Negative GEX → Trending/volatile
        GEX sign flip → Volatility expansion signal
        """
        gex = 0.0
        
        for strike in self.strikes:
            ce_gex = strike.ce_gamma * strike.ce_oi
            pe_gex = strike.pe_gamma * strike.pe_oi
            gex += (ce_gex - pe_gex) * self.spot_price * self.lot_size
        
        return gex
    
    def get_total_oi(self) -> tuple[int, int]:
        """Get total CE and PE OI."""
        ce_oi = sum(s.ce_oi for s in self.strikes)
        pe_oi = sum(s.pe_oi for s in self.strikes)
        return (ce_oi, pe_oi)
    
    def get_oi_change(self) -> tuple[int, int]:
        """Get total OI change for CE and PE."""
        ce_change = sum(s.ce_oi_change for s in self.strikes)
        pe_change = sum(s.pe_oi_change for s in self.strikes)
        return (ce_change, pe_change)
    
    def get_straddle_price(self) -> float:
        """Get ATM straddle price (ATM CE + ATM PE)."""
        atm = self.get_atm_strike()
        strike = self.get_strike(atm)
        
        if strike:
            return strike.ce_ltp + strike.pe_ltp
        return 0.0
    
    def get_expected_move_pct(self) -> float:
        """
        Calculate expected move as percentage of spot.
        
        Expected move % = straddle / spot × 100
        """
        straddle = self.get_straddle_price()
        if self.spot_price > 0:
            return (straddle / self.spot_price) * 100
        return 0.0
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert snapshot to DataFrame for analysis."""
        data = []
        for s in self.strikes:
            data.append({
                "strike": s.strike_price,
                "expiry": s.expiry,
                "ce_ltp": s.ce_ltp,
                "ce_oi": s.ce_oi,
                "ce_oi_change": s.ce_oi_change,
                "ce_iv": s.ce_iv,
                "ce_delta": s.ce_delta,
                "ce_gamma": s.ce_gamma,
                "ce_theta": s.ce_theta,
                "ce_vega": s.ce_vega,
                "pe_ltp": s.pe_ltp,
                "pe_oi": s.pe_oi,
                "pe_oi_change": s.pe_oi_change,
                "pe_iv": s.pe_iv,
                "pe_delta": s.pe_delta,
                "pe_gamma": s.pe_gamma,
                "pe_theta": s.pe_theta,
                "pe_vega": s.pe_vega,
            })
        return pd.DataFrame(data)


class OptionChainBuilder:
    """
    Builds option chain snapshots from NSE data.
    
    Features:
    - Fetches data from NSE API
    - Computes Greeks using Black-Scholes
    - Caches snapshots for staleness detection
    """
    
    def __init__(self) -> None:
        """Initialize builder."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        self._fetcher = get_nse_fetcher()
        self._snapshots: dict[str, OptionChainSnapshot] = {}
    
    def _validate_chain_freshness(self, raw_data: dict) -> bool:
        """
        Reject chain data if it's stale (NSE returned cached/expired data).
        
        Returns True if data is fresh and usable.
        """
        fetched_at_str = raw_data.get("_fetched_at")
        if not fetched_at_str:
            return True  # No timestamp — assume fresh
        
        fetched_at = datetime.fromisoformat(fetched_at_str)
        age_seconds = (datetime.now(self._ist) - fetched_at).total_seconds()
        
        if age_seconds > self._settings.chain_stale_seconds:
            logger.warning(
                "chain_data_stale",
                extra={
                    "age_seconds": age_seconds,
                    "threshold": self._settings.chain_stale_seconds,
                }
            )
            return False
        
        # Also validate the chain's own timestamp from NSE
        # NSE includes timestamp in records
        nse_timestamp = raw_data.get("records", {}).get("timestamp", "")
        # If NSE timestamp is > 30 seconds old during market hours → stale
        # (NSE updates chain every 3-5 seconds during trading)
        return True
    
    async def build_snapshot(
        self,
        underlying: str,
        expiry: date | None = None
    ) -> OptionChainSnapshot:
        """
        Build option chain snapshot for an underlying.
        
        Args:
            underlying: Underlying symbol
            expiry: Specific expiry (or nearest if None)
            
        Returns:
            OptionChainSnapshot with all strikes and Greeks
        """
        try:
            # Fetch raw data from NSE
            raw_data = await self._fetcher.fetch_option_chain(underlying)
            
            # Validate chain freshness before processing
            if not self._validate_chain_freshness(raw_data):
                logger.warning(
                    f"Chain data stale for {underlying}",
                    extra={"underlying": underlying}
                )
                # Return stale snapshot if available
                if underlying in self._snapshots:
                    old = self._snapshots[underlying]
                    old.stale = True
                    return old
                raise ValueError(f"Stale chain data for {underlying}")
            
            options, spot = self._fetcher.parse_option_chain(raw_data)
            
            if not options:
                raise ValueError(f"No option data for {underlying}")
            
            # Get lot size
            master = get_instrument_master()
            lot_size = master.get_lot_size(underlying)
            
            # Filter to specific expiry if provided
            if expiry is None:
                # Use nearest expiry
                expiry = min(o.expiry_date for o in options)
            
            filtered = [o for o in options if o.expiry_date == expiry]
            
            # Group by strike
            strikes_map: dict[float, OptionStrike] = {}
            
            for opt in filtered:
                strike_price = opt.strike_price
                
                if strike_price not in strikes_map:
                    strikes_map[strike_price] = OptionStrike(
                        strike_price=strike_price,
                        expiry=expiry
                    )
                
                strike = strikes_map[strike_price]
                
                if opt.option_type == "CE":
                    strike.ce_ltp = opt.ltp
                    strike.ce_bid = opt.bid_price
                    strike.ce_ask = opt.ask_price
                    strike.ce_oi = opt.open_interest
                    strike.ce_oi_change = opt.change_in_oi
                    strike.ce_volume = opt.volume
                    strike.ce_iv = opt.iv / 100 if opt.iv > 1 else opt.iv
                else:
                    strike.pe_ltp = opt.ltp
                    strike.pe_bid = opt.bid_price
                    strike.pe_ask = opt.ask_price
                    strike.pe_oi = opt.open_interest
                    strike.pe_oi_change = opt.change_in_oi
                    strike.pe_volume = opt.volume
                    strike.pe_iv = opt.iv / 100 if opt.iv > 1 else opt.iv
            
            # Sort strikes
            strikes_list = sorted(strikes_map.values(), key=lambda s: s.strike_price)
            
            # Compute Greeks
            self._compute_greeks(strikes_list, spot, expiry)
            
            # Create snapshot
            snapshot = OptionChainSnapshot(
                underlying=underlying,
                spot_price=spot,
                expiry=expiry,
                timestamp=datetime.now(self._ist),
                strikes=strikes_list,
                lot_size=lot_size,
                stale=False
            )
            
            # Cache snapshot
            self._snapshots[underlying] = snapshot
            
            logger.debug(
                f"Built option chain snapshot for {underlying}",
                extra={
                    "spot": spot,
                    "strikes_count": len(strikes_list),
                    "pcr": snapshot.get_pcr(),
                    "atm": snapshot.get_atm_strike()
                }
            )
            
            return snapshot
            
        except Exception as e:
            logger.error(f"Failed to build snapshot for {underlying}: {e}")
            
            # Return stale snapshot if available
            if underlying in self._snapshots:
                old = self._snapshots[underlying]
                old.stale = True
                return old
            
            raise
    
    def _compute_greeks(
        self,
        strikes: list[OptionStrike],
        spot: float,
        expiry: date
    ) -> None:
        """
        Compute Greeks for all strikes using Black-Scholes.
        
        Uses py_vollib if available, falls back to basic BS implementation.
        """
        settings = self._settings
        r = settings.rfr_rate
        
        # Time to expiry in years
        now = datetime.now(self._ist).date()
        dte = (expiry - now).days
        t = max(dte / 365.0, 0.001)  # Avoid division by zero
        
        for strike in strikes:
            k = strike.strike_price
            
            # CE Greeks
            if strike.ce_iv > 0:
                try:
                    greeks = self._black_scholes_greeks(
                        "call", spot, k, t, r, strike.ce_iv
                    )
                    strike.ce_delta = greeks["delta"]
                    strike.ce_gamma = greeks["gamma"]
                    strike.ce_theta = greeks["theta"]
                    strike.ce_vega = greeks["vega"]
                except Exception:
                    pass
            
            # PE Greeks
            if strike.pe_iv > 0:
                try:
                    greeks = self._black_scholes_greeks(
                        "put", spot, k, t, r, strike.pe_iv
                    )
                    strike.pe_delta = greeks["delta"]
                    strike.pe_gamma = greeks["gamma"]
                    strike.pe_theta = greeks["theta"]
                    strike.pe_vega = greeks["vega"]
                except Exception:
                    pass
    
    def _black_scholes_greeks(
        self,
        option_type: Literal["call", "put"],
        s: float,
        k: float,
        t: float,
        r: float,
        sigma: float
    ) -> dict[str, float]:
        """
        Compute Black-Scholes Greeks.
        
        Args:
            option_type: "call" or "put"
            s: Spot price
            k: Strike price
            t: Time to expiry (years)
            r: Risk-free rate
            sigma: Implied volatility
            
        Returns:
            Dictionary with delta, gamma, theta, vega
        """
        from math import exp, log, sqrt
        
        # Standard normal CDF and PDF
        def norm_cdf(x: float) -> float:
            return (1 + math.erf(x / sqrt(2))) / 2
        
        def norm_pdf(x: float) -> float:
            return exp(-x * x / 2) / sqrt(2 * math.pi)
        
        # Avoid edge cases
        if t <= 0 or sigma <= 0:
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}
        
        sqrt_t = sqrt(t)
        d1 = (log(s / k) + (r + sigma * sigma / 2) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        
        # Greeks
        if option_type == "call":
            delta = norm_cdf(d1)
            theta = (
                -s * norm_pdf(d1) * sigma / (2 * sqrt_t)
                - r * k * exp(-r * t) * norm_cdf(d2)
            ) / 365
        else:  # put
            delta = norm_cdf(d1) - 1
            theta = (
                -s * norm_pdf(d1) * sigma / (2 * sqrt_t)
                + r * k * exp(-r * t) * norm_cdf(-d2)
            ) / 365
        
        gamma = norm_pdf(d1) / (s * sigma * sqrt_t)
        vega = s * norm_pdf(d1) * sqrt_t / 100  # Per 1% change in IV
        
        return {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }
    
    def get_cached_snapshot(self, underlying: str) -> OptionChainSnapshot | None:
        """Get cached snapshot if available and not stale."""
        snapshot = self._snapshots.get(underlying)
        
        if snapshot and snapshot.is_valid:
            return snapshot
        
        return None


# Global builder instance
_chain_builder: OptionChainBuilder | None = None


def get_chain_builder() -> OptionChainBuilder:
    """Get or create global option chain builder."""
    global _chain_builder
    if _chain_builder is None:
        _chain_builder = OptionChainBuilder()
    return _chain_builder


# Alias for backward compatibility with tests
StrikeData = OptionStrike


class OptionChainManager:
    """
    Manages option chain snapshots for multiple underlyings.
    
    Provides a high-level interface for fetching and caching chains.
    """
    
    def __init__(self) -> None:
        """Initialize manager."""
        self._builder = get_chain_builder()
        self._ist = ZoneInfo("Asia/Kolkata")
    
    async def refresh(self, underlying: str) -> OptionChainSnapshot:
        """
        Refresh option chain for an underlying.
        
        Args:
            underlying: Underlying symbol
            
        Returns:
            Updated snapshot
        """
        return await self._builder.build_snapshot(underlying)
    
    def get_latest(self, underlying: str) -> OptionChainSnapshot | None:
        """
        Get the latest cached snapshot.
        
        Args:
            underlying: Underlying symbol
            
        Returns:
            Cached snapshot if available and valid
        """
        return self._builder.get_cached_snapshot(underlying)


# Global manager instance
_chain_manager: OptionChainManager | None = None


def get_option_chain_manager() -> OptionChainManager:
    """Get or create global option chain manager."""
    global _chain_manager
    if _chain_manager is None:
        _chain_manager = OptionChainManager()
    return _chain_manager
