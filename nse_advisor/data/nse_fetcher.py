"""
NSE Data Fetcher.

Fetches option chain, indices, ban list, FII/DII data from NSE APIs.
All data is fetched through NSE session manager with proper cookies.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from zoneinfo import ZoneInfo

from nse_advisor.data.nse_session import NseSession, get_nse_session

logger = logging.getLogger(__name__)


@dataclass
class OptionData:
    """Option contract data from NSE."""
    strike_price: float
    expiry_date: date
    option_type: str  # CE or PE
    open_interest: int
    change_in_oi: int
    volume: int
    ltp: float
    bid_price: float
    ask_price: float
    iv: float
    underlying_value: float


@dataclass
class IndexData:
    """Index data from NSE."""
    symbol: str
    ltp: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    close: float
    timestamp: datetime


@dataclass
class FiiDiiData:
    """FII/DII trading data."""
    date: date
    fii_buy_value: float
    fii_sell_value: float
    fii_net_value: float
    dii_buy_value: float
    dii_sell_value: float
    dii_net_value: float


class NseFetcher:
    """
    Fetches data from NSE public APIs.
    
    Endpoints:
    - Option Chain: /api/option-chain-indices?symbol=NIFTY
    - All Indices: /api/allIndices
    - F&O Ban List: /api/fo-banlist
    - Holidays: /api/holiday-master?type=trading
    - FII/DII: /api/fiidiiTradeReact
    - Corporate Actions: /api/corporates-corporateActions
    """
    
    BASE_URL = "https://www.nseindia.com"
    
    # API endpoints
    OPTION_CHAIN_INDEX_URL = "/api/option-chain-indices?symbol={symbol}"
    OPTION_CHAIN_EQUITY_URL = "/api/option-chain-equities?symbol={symbol}"
    ALL_INDICES_URL = "/api/allIndices"
    FO_BAN_LIST_URL = "/api/equity-stockIndices?index=SECURITIES%20IN%20BAN%20PERIOD"
    HOLIDAY_URL = "/api/holiday-master?type=trading"
    FII_DII_URL = "/api/fiidiiTradeReact"
    CORP_ACTIONS_URL = "/api/corporates-corporateActions?index=equities"
    
    def __init__(self, session: NseSession | None = None) -> None:
        """Initialize fetcher with NSE session."""
        self._session = session or get_nse_session()
        self._ist = ZoneInfo("Asia/Kolkata")
    
    async def fetch_option_chain(
        self,
        symbol: str,
        is_index: bool = True
    ) -> dict[str, Any]:
        """
        Fetch option chain data for a symbol.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            is_index: True for index options, False for equity options
            
        Returns:
            Raw option chain data from NSE
        """
        if is_index:
            url = self.BASE_URL + self.OPTION_CHAIN_INDEX_URL.format(symbol=symbol)
        else:
            url = self.BASE_URL + self.OPTION_CHAIN_EQUITY_URL.format(symbol=symbol)
        
        data = await self._session.fetch(url)
        
        if not data or "records" not in data:
            logger.error(f"Invalid option chain response for {symbol}. Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            raise ValueError(f"Invalid option chain response for {symbol}")
        
        logger.debug(
            f"Fetched option chain for {symbol}",
            extra={
                "strikes_count": len(data.get("records", {}).get("data", [])),
                "underlying": data.get("records", {}).get("underlyingValue")
            }
        )
        
        return data
    
    def parse_option_chain(
        self,
        data: dict[str, Any]
    ) -> tuple[list[OptionData], float]:
        """
        Parse option chain response into structured data.
        
        Args:
            data: Raw option chain response
            
        Returns:
            Tuple of (list of OptionData, underlying spot price)
        """
        records = data.get("records", {})
        underlying_value = records.get("underlyingValue", 0.0)
        expiry_dates = records.get("expiryDates", [])
        raw_data = records.get("data", [])
        
        options: list[OptionData] = []
        
        for row in raw_data:
            expiry_str = row.get("expiryDate", "")
            try:
                expiry_date = datetime.strptime(expiry_str, "%d-%b-%Y").date()
            except ValueError:
                continue
            
            strike = row.get("strikePrice", 0)
            
            # Parse CE data
            ce_data = row.get("CE")
            if ce_data:
                options.append(OptionData(
                    strike_price=strike,
                    expiry_date=expiry_date,
                    option_type="CE",
                    open_interest=ce_data.get("openInterest", 0),
                    change_in_oi=ce_data.get("changeinOpenInterest", 0),
                    volume=ce_data.get("totalTradedVolume", 0),
                    ltp=ce_data.get("lastPrice", 0.0),
                    bid_price=ce_data.get("bidprice", 0.0),
                    ask_price=ce_data.get("askPrice", 0.0),
                    iv=ce_data.get("impliedVolatility", 0.0),
                    underlying_value=ce_data.get("underlyingValue", underlying_value)
                ))
            
            # Parse PE data
            pe_data = row.get("PE")
            if pe_data:
                options.append(OptionData(
                    strike_price=strike,
                    expiry_date=expiry_date,
                    option_type="PE",
                    open_interest=pe_data.get("openInterest", 0),
                    change_in_oi=pe_data.get("changeinOpenInterest", 0),
                    volume=pe_data.get("totalTradedVolume", 0),
                    ltp=pe_data.get("lastPrice", 0.0),
                    bid_price=pe_data.get("bidprice", 0.0),
                    ask_price=pe_data.get("askPrice", 0.0),
                    iv=pe_data.get("impliedVolatility", 0.0),
                    underlying_value=pe_data.get("underlyingValue", underlying_value)
                ))
        
        return options, underlying_value
    
    async def fetch_all_indices(self) -> list[IndexData]:
        """Fetch all NSE indices data."""
        url = self.BASE_URL + self.ALL_INDICES_URL
        data = await self._session.fetch(url)
        
        indices: list[IndexData] = []
        
        for item in data.get("data", []):
            try:
                indices.append(IndexData(
                    symbol=item.get("index", ""),
                    ltp=float(item.get("last", 0)),
                    change=float(item.get("variation", 0)),
                    change_pct=float(item.get("percentChange", 0)),
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=float(item.get("previousClose", 0)),
                    timestamp=datetime.now(self._ist)
                ))
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse index data: {e}")
                continue
        
        return indices
    
    async def fetch_index(self, symbol: str) -> IndexData | None:
        """Fetch specific index data."""
        indices = await self.fetch_all_indices()
        
        # Map common names
        symbol_map = {
            "NIFTY": "NIFTY 50",
            "NIFTY50": "NIFTY 50",
            "BANKNIFTY": "NIFTY BANK",
            "FINNIFTY": "NIFTY FIN SERVICE",
            "MIDCPNIFTY": "NIFTY MIDCAP 50",
            "INDIAVIX": "INDIA VIX",
            "VIX": "INDIA VIX"
        }
        
        search_symbol = symbol_map.get(symbol.upper(), symbol.upper())
        
        for index in indices:
            if index.symbol.upper() == search_symbol:
                return index
        
        return None
    
    async def fetch_india_vix(self) -> float:
        """Fetch India VIX value."""
        index = await self.fetch_index("INDIAVIX")
        return index.ltp if index else 0.0
    
    async def fetch_ban_list(self) -> list[str]:
        """
        Fetch F&O ban list for today.
        
        Returns:
            List of banned symbol names
        """
        url = self.BASE_URL + self.FO_BAN_LIST_URL
        
        try:
            data = await self._session.fetch(url)
            
            banned_symbols: list[str] = []
            for item in data:
                if isinstance(item, dict):
                    symbol = item.get("symbol", "")
                    if symbol:
                        banned_symbols.append(symbol)
            
            logger.info(f"Fetched ban list: {len(banned_symbols)} symbols banned")
            return banned_symbols
            
        except Exception as e:
            logger.warning(f"Failed to fetch ban list: {e}")
            return []
    
    async def fetch_holidays(self, year: int | None = None) -> list[date]:
        """
        Fetch trading holidays for a year.
        
        Args:
            year: Year to fetch holidays for (defaults to current year)
            
        Returns:
            List of holiday dates
        """
        url = self.BASE_URL + self.HOLIDAY_URL
        data = await self._session.fetch(url)
        
        holidays: list[date] = []
        
        # NSE returns holidays in CM category
        for category in data.values():
            if not isinstance(category, list):
                continue
            for item in category:
                try:
                    date_str = item.get("tradingDate", "")
                    holiday_date = datetime.strptime(date_str, "%d-%b-%Y").date()
                    
                    if year is None or holiday_date.year == year:
                        holidays.append(holiday_date)
                except (ValueError, TypeError):
                    continue
        
        return sorted(set(holidays))
    
    async def fetch_fii_dii_data(self) -> FiiDiiData | None:
        """
        Fetch FII/DII trading data.
        
        Note: Available after 18:00 IST for previous trading day.
        """
        url = self.BASE_URL + self.FII_DII_URL
        
        try:
            data = await self._session.fetch(url)
            
            if not data:
                return None
            
            # Find latest data
            latest = data[0] if data else {}
            
            return FiiDiiData(
                date=date.today(),
                fii_buy_value=float(latest.get("fii_buy_value", 0)),
                fii_sell_value=float(latest.get("fii_sell_value", 0)),
                fii_net_value=float(latest.get("fii_net_value", 0)),
                dii_buy_value=float(latest.get("dii_buy_value", 0)),
                dii_sell_value=float(latest.get("dii_sell_value", 0)),
                dii_net_value=float(latest.get("dii_net_value", 0)),
            )
            
        except Exception as e:
            logger.warning(f"Failed to fetch FII/DII data: {e}")
            return None


# Global fetcher instance
_nse_fetcher: NseFetcher | None = None


def get_nse_fetcher() -> NseFetcher:
    """Get or create global NSE fetcher instance."""
    global _nse_fetcher
    if _nse_fetcher is None:
        _nse_fetcher = NseFetcher()
    return _nse_fetcher
