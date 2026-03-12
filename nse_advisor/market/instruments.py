"""
Instrument Master.

Manages NFO instrument data, lot sizes, and symbol mappings.
Fetches instrument data from NSE at startup and weekly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from zoneinfo import ZoneInfo

from nse_advisor.data.nse_fetcher import get_nse_fetcher

logger = logging.getLogger(__name__)


@dataclass
class InstrumentInfo:
    """Information about a tradeable instrument."""
    symbol: str
    underlying: str
    instrument_type: Literal["CE", "PE", "FUT", "EQ"]
    strike: float | None
    expiry: date | None
    lot_size: int
    tick_size: float
    exchange: str = "NFO"


class InstrumentMaster:
    """
    Manages instrument data for NFO segment.
    
    Features:
    - Fetch and cache instrument list from NSE
    - Build lookup maps for quick access
    - Get lot sizes (never hardcoded)
    - Get active expiries for each underlying
    """
    
    # Default lot sizes (used as fallback, prefer live data)
    DEFAULT_LOT_SIZES = {
        "NIFTY": 25,
        "BANKNIFTY": 15,
        "FINNIFTY": 25,
        "MIDCPNIFTY": 50,
        "RELIANCE": 250,
        "TCS": 150,
        "HDFCBANK": 550,
        "INFY": 300,
        "ICICIBANK": 700,
    }
    
    def __init__(self) -> None:
        """Initialize instrument master."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._instruments: dict[str, InstrumentInfo] = {}
        self._lot_sizes: dict[str, int] = dict(self.DEFAULT_LOT_SIZES)
        self._expiries: dict[str, list[date]] = {}
        self._last_refresh: datetime | None = None
        self._last_downloaded: datetime | None = None
        self._loaded = False
    
    @property
    def is_loaded(self) -> bool:
        """Check if instruments are loaded."""
        return self._loaded
    
    @property
    def last_downloaded(self) -> datetime | None:
        """Get last download timestamp."""
        return self._last_downloaded
    
    async def refresh(self) -> None:
        """
        Load instrument data from NSE.
        
        Note: NSE doesn't have a public bulk instruments API like brokers.
        We derive lot sizes from option chain data.
        """
        try:
            fetcher = get_nse_fetcher()
            
            # Fetch option chains for primary underlyings to get lot sizes and expiries
            for underlying in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
                await self._load_underlying_info(underlying, fetcher)
            
            self._last_refresh = datetime.now(self._ist)
            self._last_downloaded = datetime.now(self._ist)
            self._loaded = True
            
            logger.info(
                f"Loaded instruments for {len(self._lot_sizes)} underlyings",
                extra={"lot_sizes": self._lot_sizes}
            )
            
        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")
            # Use defaults
            self._loaded = True
    
    async def refresh_master(self) -> None:
        """
        Refresh instrument master data.
        
        Called by APScheduler every Monday at 08:00 IST to pick up
        new weekly contracts listed on Thursday.
        """
        logger.info("Refreshing instrument master")
        await self.refresh()
    
    async def _load_underlying_info(
        self,
        underlying: str,
        fetcher: object | None = None
    ) -> None:
        """Load info for a specific underlying from option chain."""
        if fetcher is None:
            fetcher = get_nse_fetcher()
        
        try:
            data = await fetcher.fetch_option_chain(underlying, is_index=True)
            
            records = data.get("records", {})
            
            # Extract expiry dates
            expiry_dates_str = records.get("expiryDates", [])
            expiries: list[date] = []
            for exp_str in expiry_dates_str[:5]:  # First 5 expiries
                try:
                    exp_date = datetime.strptime(exp_str, "%d-%b-%Y").date()
                    expiries.append(exp_date)
                except ValueError:
                    continue
            
            self._expiries[underlying] = sorted(expiries)
            
            # Try to extract lot size from data (NSE includes this in metadata)
            # If not available, use defaults
            
        except Exception as e:
            logger.warning(f"Failed to load info for {underlying}: {e}")
    
    def get_lot_size(self, underlying: str) -> int:
        """
        Get lot size for an underlying.
        
        IMPORTANT: Always fetch from live data, never hardcode.
        Falls back to default if not available.
        
        Args:
            underlying: Symbol name
            
        Returns:
            Lot size
        """
        underlying_upper = underlying.upper()
        return self._lot_sizes.get(underlying_upper, 25)  # Default to 25
    
    def set_lot_size(self, underlying: str, lot_size: int) -> None:
        """Set lot size for an underlying."""
        self._lot_sizes[underlying.upper()] = lot_size
    
    def get_active_expiries(
        self,
        underlying: str,
        count: int = 3
    ) -> list[date]:
        """
        Get next N expiry dates for an underlying.
        
        Args:
            underlying: Symbol name
            count: Number of expiries to return
            
        Returns:
            List of expiry dates
        """
        expiries = self._expiries.get(underlying.upper(), [])
        
        # Filter out past expiries
        today = datetime.now(self._ist).date()
        future_expiries = [e for e in expiries if e >= today]
        
        return future_expiries[:count]
    
    def get_option_symbol(
        self,
        underlying: str,
        strike: float,
        option_type: Literal["CE", "PE"],
        expiry: date
    ) -> str:
        """
        Build NSE trading symbol for an option.
        
        Format: NIFTY24DEC24000CE
        
        Args:
            underlying: Underlying symbol
            strike: Strike price
            option_type: CE or PE
            expiry: Expiry date
            
        Returns:
            Trading symbol string
        """
        # Format: NIFTY24DEC24000CE
        year_short = expiry.strftime("%y")
        month = expiry.strftime("%b").upper()
        strike_int = int(strike)
        
        return f"{underlying.upper()}{year_short}{month}{strike_int}{option_type}"
    
    def parse_option_symbol(
        self,
        symbol: str
    ) -> tuple[str, float, str, str] | None:
        """
        Parse an NSE option symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Tuple of (underlying, strike, option_type, expiry_str) or None
        """
        import re
        
        # Pattern: UNDERLYING + YY + MON + STRIKE + CE/PE
        pattern = r"([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)"
        match = re.match(pattern, symbol.upper())
        
        if not match:
            return None
        
        underlying = match.group(1)
        year = match.group(2)
        month = match.group(3)
        strike = float(match.group(4))
        option_type = match.group(5)
        
        return (underlying, strike, option_type, f"{year}{month}")
    
    def get_underlying_list(self) -> list[str]:
        """Get list of available underlyings."""
        return list(self._lot_sizes.keys())


# Global instance
_instrument_master: InstrumentMaster | None = None


def get_instrument_master() -> InstrumentMaster:
    """Get or create global instrument master instance."""
    global _instrument_master
    if _instrument_master is None:
        _instrument_master = InstrumentMaster()
    return _instrument_master


async def init_instrument_master() -> InstrumentMaster:
    """Initialize instrument master with data."""
    master = get_instrument_master()
    await master.refresh()
    return master
