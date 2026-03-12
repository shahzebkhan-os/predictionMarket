"""
NSE Session Manager.

Handles NSE API session with browser-like headers and cookie management.
Auto-refreshes session cookies every 25 minutes.
All NSE API calls wrapped with retry logic and exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import requests
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

logger = logging.getLogger(__name__)


class NseSessionError(Exception):
    """Exception raised when NSE session operations fail."""
    pass


class NseSession:
    """
    NSE session manager with browser-like headers and cookie refresh.
    
    NSE requires:
    - Browser-like User-Agent
    - Session cookies from visiting homepage first
    - Proper Referer header
    - Session refresh every ~25 minutes (cookies expire after 30min)
    
    Usage:
        session = NseSession()
        await session.init_session()
        data = await session.fetch("https://www.nseindia.com/api/...")
    """
    
    BASE_URL = "https://www.nseindia.com"
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    MAX_RETRIES = 3
    BACKOFF_DELAYS = [2, 4, 8]  # seconds
    
    def __init__(self) -> None:
        """Initialize session manager."""
        self._session: requests.Session | None = None
        self._last_refresh: datetime | None = None
        self._settings = get_settings()
        self._lock = asyncio.Lock()
        self._ist = ZoneInfo("Asia/Kolkata")
    
    @property
    def is_initialized(self) -> bool:
        """Check if session is initialized and not stale."""
        if self._session is None or self._last_refresh is None:
            return False
        
        refresh_interval = timedelta(minutes=self._settings.nse_session_refresh_minutes)
        now = datetime.now(self._ist)
        return (now - self._last_refresh) < refresh_interval
    
    def _create_session(self) -> requests.Session:
        """Create a new requests session with proper headers."""
        session = requests.Session()
        session.headers.update(self.HEADERS)
        return session
    
    def _init_session_sync(self) -> None:
        """
        Initialize session by visiting NSE homepage to seed cookies.
        
        This must be called before making any API requests.
        Runs synchronously - use init_session() for async context.
        """
        self._session = self._create_session()
        
        try:
            # Visit homepage to get session cookies
            response = self._session.get(
                self.BASE_URL,
                timeout=10,
                allow_redirects=True
            )
            response.raise_for_status()
            
            # Verify we have cookies
            if not self._session.cookies:
                raise NseSessionError("No cookies received from NSE homepage")
            
            self._last_refresh = datetime.now(self._ist)
            logger.info(
                "NSE session initialized",
                extra={
                    "cookies_count": len(self._session.cookies),
                    "refresh_time": self._last_refresh.isoformat()
                }
            )
            
        except requests.RequestException as e:
            self._session = None
            raise NseSessionError(f"Failed to initialize NSE session: {e}") from e
    
    async def init_session(self) -> None:
        """Initialize session asynchronously."""
        async with self._lock:
            await asyncio.to_thread(self._init_session_sync)
    
    async def refresh_session(self) -> None:
        """Refresh session cookies."""
        logger.info("Refreshing NSE session cookies")
        await self.init_session()
    
    def _fetch_sync(self, url: str, timeout: int = 10) -> dict[str, Any]:
        """
        Fetch data from NSE API synchronously with retry logic.
        
        Args:
            url: Full URL to fetch
            timeout: Request timeout in seconds
            
        Returns:
            Parsed JSON response
            
        Raises:
            NseSessionError: If all retries fail
        """
        if self._session is None:
            raise NseSessionError("Session not initialized. Call init_session() first.")
        
        last_exception: Exception | None = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self._session.get(url, timeout=timeout)
                
                # Handle 403 - session expired
                if response.status_code == 403:
                    logger.warning(
                        "NSE returned 403, re-initializing session",
                        extra={"attempt": attempt + 1}
                    )
                    self._init_session_sync()
                    continue
                
                response.raise_for_status()
                return response.json()
                
            except requests.RequestException as e:
                last_exception = e
                
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BACKOFF_DELAYS[attempt]
                    logger.warning(
                        f"NSE fetch failed, retrying in {delay}s",
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "error": str(e)
                        }
                    )
                    import time
                    time.sleep(delay)
        
        raise NseSessionError(
            f"NSE fetch failed after {self.MAX_RETRIES} attempts: {last_exception}"
        )
    
    async def fetch(self, url: str, timeout: int = 10) -> dict[str, Any]:
        """
        Fetch data from NSE API asynchronously.
        
        Wraps sync fetch in asyncio.to_thread for async compatibility.
        
        Args:
            url: Full URL to fetch
            timeout: Request timeout in seconds
            
        Returns:
            Parsed JSON response
        """
        async with self._lock:
            # Check if session needs refresh
            if not self.is_initialized:
                await asyncio.to_thread(self._init_session_sync)
            
            return await asyncio.to_thread(self._fetch_sync, url, timeout)
    
    def close(self) -> None:
        """Close the session."""
        if self._session:
            self._session.close()
            self._session = None
            self._last_refresh = None
            logger.info("NSE session closed")


# Global session instance
_nse_session: NseSession | None = None


def get_nse_session() -> NseSession:
    """Get or create global NSE session instance."""
    global _nse_session
    if _nse_session is None:
        _nse_session = NseSession()
    return _nse_session


async def close_nse_session() -> None:
    """Close global NSE session."""
    global _nse_session
    if _nse_session is not None:
        _nse_session.close()
        _nse_session = None
