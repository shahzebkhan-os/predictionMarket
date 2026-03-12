"""
F&O Ban List Checker.

Fetches and manages the NSE F&O ban list.
Blocks recommendations for banned instruments.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date

from zoneinfo import ZoneInfo

from nse_advisor.data.nse_fetcher import get_nse_fetcher

logger = logging.getLogger(__name__)


class BanListChecker:
    """
    Checks and manages F&O ban list.
    
    NSE bans stocks from F&O when OI crosses 95% of MWPL.
    - Only closing positions allowed for banned stocks
    - No new recommendations should be generated
    - Daily refresh at 08:30 IST
    """
    
    def __init__(self) -> None:
        """Initialize ban list checker."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._banned_symbols: set[str] = set()
        self._last_refresh: datetime | None = None
    
    @property
    def banned_count(self) -> int:
        """Get count of banned symbols."""
        return len(self._banned_symbols)
    
    @property
    def banned_symbols(self) -> list[str]:
        """Get list of banned symbols."""
        return sorted(self._banned_symbols)
    
    async def refresh(self) -> list[str]:
        """
        Refresh ban list from NSE.
        
        Returns:
            List of banned symbols
        """
        try:
            fetcher = get_nse_fetcher()
            symbols = await fetcher.fetch_ban_list()
            
            # Track changes
            new_bans = set(symbols) - self._banned_symbols
            removed = self._banned_symbols - set(symbols)
            
            self._banned_symbols = set(symbols)
            self._last_refresh = datetime.now(self._ist)
            
            if new_bans:
                logger.info(f"New bans added: {list(new_bans)}")
            if removed:
                logger.info(f"Bans removed: {list(removed)}")
            
            logger.info(
                f"Ban list refreshed",
                extra={
                    "count": len(symbols),
                    "symbols": symbols
                }
            )
            
            return symbols
            
        except Exception as e:
            logger.error(f"Failed to refresh ban list: {e}")
            return list(self._banned_symbols)
    
    def is_banned(self, symbol: str) -> bool:
        """
        Check if a symbol is in ban list.
        
        Args:
            symbol: Symbol to check
            
        Returns:
            True if symbol is banned
        """
        return symbol.upper() in self._banned_symbols
    
    def check_recommendation(self, underlying: str) -> tuple[bool, str]:
        """
        Check if recommendations are allowed for underlying.
        
        Args:
            underlying: Underlying symbol
            
        Returns:
            Tuple of (is_allowed, reason)
        """
        if self.is_banned(underlying):
            return (
                False,
                f"{underlying} is in F&O ban list - only closing positions allowed"
            )
        return (True, "")
    
    def get_status(self) -> dict:
        """Get ban list status."""
        return {
            "banned_count": self.banned_count,
            "banned_symbols": self.banned_symbols,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
        }


# Global instance
_ban_list_checker: BanListChecker | None = None


def get_ban_list_checker() -> BanListChecker:
    """Get or create global ban list checker."""
    global _ban_list_checker
    if _ban_list_checker is None:
        _ban_list_checker = BanListChecker()
    return _ban_list_checker


async def init_ban_list_checker() -> BanListChecker:
    """Initialize ban list checker with data."""
    checker = get_ban_list_checker()
    await checker.refresh()
    return checker
