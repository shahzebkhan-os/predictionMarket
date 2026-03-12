"""
yfinance Data Fetcher.

Fetches historical OHLCV, global cues, and VIX data from yfinance.
Used for technical analysis backfill and global market cues.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


@dataclass
class OHLCVData:
    """OHLCV candle data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class GlobalCues:
    """Global market cues data."""
    timestamp: datetime
    gift_nifty: float | None
    gift_nifty_change_pct: float | None
    spx_close: float
    spx_change_pct: float
    nasdaq_close: float
    nasdaq_change_pct: float
    dxy: float
    dxy_change_pct: float
    crude_wti: float
    crude_change_pct: float
    usdinr: float
    usdinr_change_pct: float


class YFinanceFetcher:
    """
    Fetches data from yfinance.
    
    Symbols:
    - NIFTY 50: ^NSEI
    - NIFTY Bank: ^NSEBANK
    - India VIX: ^INDIAVIX
    - S&P 500: ^GSPC
    - Nasdaq: ^IXIC
    - DXY: DX-Y.NYB
    - Crude WTI: CL=F
    - USD/INR: USDINR=X
    - GIFT Nifty proxy: NIFTYBEES.NS
    """
    
    # Symbol mappings
    SYMBOL_MAP = {
        "NIFTY": "^NSEI",
        "NIFTY50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "INDIAVIX": "^INDIAVIX",
        "VIX": "^INDIAVIX",
        "SPX": "^GSPC",
        "NASDAQ": "^IXIC",
        "DXY": "DX-Y.NYB",
        "CRUDE": "CL=F",
        "WTI": "CL=F",
        "USDINR": "USDINR=X",
        "GIFT": "NIFTYBEES.NS",
    }
    
    def __init__(self) -> None:
        """Initialize fetcher."""
        self._ist = ZoneInfo("Asia/Kolkata")
    
    def _get_ticker_symbol(self, symbol: str) -> str:
        """Map common names to yfinance ticker symbols."""
        return self.SYMBOL_MAP.get(symbol.upper(), symbol)
    
    async def fetch_historical_ohlcv(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "5m"
    ) -> list[OHLCVData]:
        """
        Fetch historical OHLCV data.
        
        Args:
            symbol: Underlying symbol or yfinance ticker
            period: Data period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo)
            
        Returns:
            List of OHLCV data
        """
        ticker = self._get_ticker_symbol(symbol)
        
        def _fetch() -> pd.DataFrame:
            yf_ticker = yf.Ticker(ticker)
            return yf_ticker.history(period=period, interval=interval)
        
        try:
            df = await asyncio.to_thread(_fetch)
            
            if df.empty:
                logger.warning(f"No data returned for {symbol}")
                return []
            
            candles: list[OHLCVData] = []
            for idx, row in df.iterrows():
                ts = idx
                if isinstance(ts, pd.Timestamp):
                    ts = ts.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=self._ist)
                
                candles.append(OHLCVData(
                    timestamp=ts,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row.get("Volume", 0))
                ))
            
            logger.debug(f"Fetched {len(candles)} candles for {symbol}")
            return candles
            
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
            return []
    
    async def fetch_daily_close(
        self,
        symbol: str,
        days: int = 252
    ) -> list[tuple[date, float]]:
        """
        Fetch daily closing prices.
        
        Args:
            symbol: Underlying symbol
            days: Number of days to fetch
            
        Returns:
            List of (date, close_price) tuples
        """
        ticker = self._get_ticker_symbol(symbol)
        period = f"{max(days + 30, 252)}d"  # Extra buffer for weekends/holidays
        
        def _fetch() -> pd.DataFrame:
            yf_ticker = yf.Ticker(ticker)
            return yf_ticker.history(period=period, interval="1d")
        
        try:
            df = await asyncio.to_thread(_fetch)
            
            if df.empty:
                return []
            
            # Take last N days
            df = df.tail(days)
            
            closes: list[tuple[date, float]] = []
            for idx, row in df.iterrows():
                ts = idx
                if isinstance(ts, pd.Timestamp):
                    dt = ts.date()
                else:
                    dt = ts.date() if hasattr(ts, 'date') else ts
                
                closes.append((dt, float(row["Close"])))
            
            return closes
            
        except Exception as e:
            logger.error(f"Failed to fetch daily closes for {symbol}: {e}")
            return []
    
    async def fetch_iv_history(
        self,
        symbol: str,
        days: int = 252
    ) -> list[tuple[date, float]]:
        """
        Fetch historical India VIX data.
        
        Note: This fetches ^INDIAVIX for India VIX historical values.
        For individual stock/index IV, this would need options data.
        
        Args:
            symbol: Ignored (always fetches India VIX)
            days: Number of days of history
            
        Returns:
            List of (date, vix_value) tuples
        """
        return await self.fetch_daily_close("INDIAVIX", days)
    
    async def fetch_global_cues(self) -> GlobalCues:
        """
        Fetch global market cues.
        
        Includes: SPX, Nasdaq, DXY, Crude, USD/INR, GIFT Nifty proxy.
        """
        symbols = ["SPX", "NASDAQ", "DXY", "CRUDE", "USDINR", "GIFT"]
        
        async def _fetch_symbol(sym: str) -> tuple[str, float, float]:
            """Fetch symbol with change percentage."""
            closes = await self.fetch_daily_close(sym, 2)
            if len(closes) >= 2:
                prev = closes[-2][1]
                curr = closes[-1][1]
                change_pct = ((curr - prev) / prev) * 100 if prev else 0
                return (sym, curr, change_pct)
            elif closes:
                return (sym, closes[-1][1], 0.0)
            return (sym, 0.0, 0.0)
        
        # Fetch all symbols in parallel
        results = await asyncio.gather(*[_fetch_symbol(s) for s in symbols])
        
        data: dict[str, tuple[float, float]] = {}
        for sym, value, change in results:
            data[sym] = (value, change)
        
        spx_data = data.get("SPX", (0.0, 0.0))
        nasdaq_data = data.get("NASDAQ", (0.0, 0.0))
        dxy_data = data.get("DXY", (0.0, 0.0))
        crude_data = data.get("CRUDE", (0.0, 0.0))
        usdinr_data = data.get("USDINR", (0.0, 0.0))
        gift_data = data.get("GIFT", (0.0, 0.0))
        
        return GlobalCues(
            timestamp=datetime.now(self._ist),
            gift_nifty=gift_data[0] if gift_data[0] else None,
            gift_nifty_change_pct=gift_data[1] if gift_data[0] else None,
            spx_close=spx_data[0],
            spx_change_pct=spx_data[1],
            nasdaq_close=nasdaq_data[0],
            nasdaq_change_pct=nasdaq_data[1],
            dxy=dxy_data[0],
            dxy_change_pct=dxy_data[1],
            crude_wti=crude_data[0],
            crude_change_pct=crude_data[1],
            usdinr=usdinr_data[0],
            usdinr_change_pct=usdinr_data[1],
        )
    
    async def fetch_previous_close(self, symbol: str) -> float | None:
        """Fetch previous day's close price."""
        closes = await self.fetch_daily_close(symbol, 2)
        if len(closes) >= 2:
            return closes[-2][1]
        return None
    
    async def backfill_candles(
        self,
        symbol: str,
        count: int = 50,
        interval: str = "5m"
    ) -> pd.DataFrame:
        """
        Backfill candles for technical analysis.
        
        Required for valid Supertrend, RSI, Bollinger on first scan.
        
        Args:
            symbol: Underlying symbol
            count: Number of candles to fetch
            interval: Candle interval
            
        Returns:
            DataFrame with OHLCV columns
        """
        # Calculate period based on interval
        if interval in ["1m", "2m", "5m"]:
            period = "5d"  # Max for intraday
        elif interval in ["15m", "30m", "60m", "1h"]:
            period = "1mo"
        else:
            period = "3mo"
        
        candles = await self.fetch_historical_ohlcv(symbol, period, interval)
        
        if not candles:
            return pd.DataFrame()
        
        # Convert to DataFrame
        df = pd.DataFrame([
            {
                "datetime": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume
            }
            for c in candles
        ])
        
        df.set_index("datetime", inplace=True)
        
        # Take last N candles
        return df.tail(count)


# Global fetcher instance
_yf_fetcher: YFinanceFetcher | None = None


def get_yfinance_fetcher() -> YFinanceFetcher:
    """Get or create global yfinance fetcher instance."""
    global _yf_fetcher
    if _yf_fetcher is None:
        _yf_fetcher = YFinanceFetcher()
    return _yf_fetcher
