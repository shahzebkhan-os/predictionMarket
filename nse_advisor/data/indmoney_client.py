"""
IndMoney Client.

Read-only client for IndMoney API to fetch portfolio positions and P&L.
Used for syncing actual trades and cross-checking with logged trades.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class IndMoneyPosition:
    """Position from IndMoney portfolio."""
    symbol: str
    quantity: int
    average_price: float
    current_price: float
    pnl: float
    pnl_pct: float
    instrument_type: str  # EQUITY, OPTION, FUTURE
    expiry: datetime | None
    strike: float | None
    option_type: str | None  # CE, PE


@dataclass
class IndMoneyPortfolio:
    """Portfolio summary from IndMoney."""
    total_investment: float
    current_value: float
    total_pnl: float
    total_pnl_pct: float
    positions: list[IndMoneyPosition]
    last_sync: datetime


class IndMoneyClient:
    """
    Read-only client for IndMoney API.
    
    Used to:
    - Fetch actual portfolio positions
    - Get real P&L for comparison with paper trades
    - Detect untracked positions
    
    Note: This is read-only. No trade execution through IndMoney.
    """
    
    BASE_URL = "https://api.indmoney.com"
    
    # API endpoints (these are placeholder - actual endpoints may differ)
    PORTFOLIO_URL = "/v1/portfolio"
    POSITIONS_URL = "/v1/positions"
    PNL_URL = "/v1/pnl"
    
    def __init__(self, bearer_token: str | None = None) -> None:
        """
        Initialize IndMoney client.
        
        Args:
            bearer_token: Bearer token for authentication
        """
        settings = get_settings()
        self._token = bearer_token or settings.indmoney_bearer_token
        self._ist = ZoneInfo("Asia/Kolkata")
        self._session: aiohttp.ClientSession | None = None
    
    @property
    def is_configured(self) -> bool:
        """Check if client is configured with a token."""
        return bool(self._token)
    
    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authorization."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._get_headers())
        return self._session
    
    async def _fetch(self, endpoint: str) -> dict[str, Any]:
        """
        Fetch data from IndMoney API.
        
        Args:
            endpoint: API endpoint path
            
        Returns:
            Parsed JSON response
        """
        if not self.is_configured:
            raise ValueError("IndMoney bearer token not configured")
        
        session = await self._ensure_session()
        url = self.BASE_URL + endpoint
        
        async with session.get(url) as response:
            if response.status == 401:
                raise ValueError("IndMoney authentication failed - check token")
            response.raise_for_status()
            return await response.json()
    
    async def fetch_portfolio(self) -> IndMoneyPortfolio | None:
        """
        Fetch complete portfolio from IndMoney.
        
        Returns:
            Portfolio with all positions, or None if not configured
        """
        if not self.is_configured:
            logger.warning("IndMoney client not configured, skipping portfolio sync")
            return None
        
        try:
            data = await self._fetch(self.PORTFOLIO_URL)
            
            positions: list[IndMoneyPosition] = []
            
            for pos_data in data.get("positions", []):
                # Parse expiry if present
                expiry = None
                expiry_str = pos_data.get("expiry")
                if expiry_str:
                    try:
                        expiry = datetime.fromisoformat(expiry_str)
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=self._ist)
                    except ValueError:
                        pass
                
                positions.append(IndMoneyPosition(
                    symbol=pos_data.get("symbol", ""),
                    quantity=int(pos_data.get("quantity", 0)),
                    average_price=float(pos_data.get("average_price", 0)),
                    current_price=float(pos_data.get("current_price", 0)),
                    pnl=float(pos_data.get("pnl", 0)),
                    pnl_pct=float(pos_data.get("pnl_pct", 0)),
                    instrument_type=pos_data.get("instrument_type", "EQUITY"),
                    expiry=expiry,
                    strike=pos_data.get("strike"),
                    option_type=pos_data.get("option_type"),
                ))
            
            return IndMoneyPortfolio(
                total_investment=float(data.get("total_investment", 0)),
                current_value=float(data.get("current_value", 0)),
                total_pnl=float(data.get("total_pnl", 0)),
                total_pnl_pct=float(data.get("total_pnl_pct", 0)),
                positions=positions,
                last_sync=datetime.now(self._ist),
            )
            
        except Exception as e:
            logger.error(f"Failed to fetch IndMoney portfolio: {e}")
            return None
    
    async def fetch_option_positions(self) -> list[IndMoneyPosition]:
        """
        Fetch only option positions.
        
        Returns:
            List of option positions
        """
        portfolio = await self.fetch_portfolio()
        
        if not portfolio:
            return []
        
        return [
            pos for pos in portfolio.positions
            if pos.instrument_type == "OPTION"
        ]
    
    async def sync_positions(self) -> tuple[list[IndMoneyPosition], datetime]:
        """
        Sync positions from IndMoney.
        
        Returns:
            Tuple of (positions list, sync timestamp)
        """
        portfolio = await self.fetch_portfolio()
        
        if portfolio:
            return (portfolio.positions, portfolio.last_sync)
        
        return ([], datetime.now(self._ist))
    
    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


# Global client instance
_indmoney_client: IndMoneyClient | None = None


def get_indmoney_client() -> IndMoneyClient:
    """Get or create global IndMoney client instance."""
    global _indmoney_client
    if _indmoney_client is None:
        _indmoney_client = IndMoneyClient()
    return _indmoney_client


async def close_indmoney_client() -> None:
    """Close global IndMoney client."""
    global _indmoney_client
    if _indmoney_client is not None:
        await _indmoney_client.close()
        _indmoney_client = None
