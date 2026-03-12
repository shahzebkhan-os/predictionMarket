"""IndMoney API client (read-only).

Used for portfolio P&L cross-check and position verification only.
Kite is primary for all execution.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import aiohttp
import structlog

from nse_options_bot.config import settings

logger = structlog.get_logger(__name__)


class IndMoneyError(Exception):
    """IndMoney API error."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Initialize error.

        Args:
            message: Error message
            code: Error code
        """
        super().__init__(message)
        self.code = code


class IndMoneyClient:
    """IndMoney API client for read-only operations.

    Used for cross-validation of portfolio and positions.
    """

    BASE_URL = "https://api.indmoney.com"

    def __init__(self, bearer_token: str | None = None) -> None:
        """Initialize IndMoney client.

        Args:
            bearer_token: IndMoney Bearer token
        """
        self._bearer_token = (
            bearer_token or settings.indmoney_bearer_token.get_secret_value()
        )
        self._session: aiohttp.ClientSession | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self._session is not None

    async def connect(self) -> None:
        """Establish connection to IndMoney API."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._bearer_token}",
                    "Content-Type": "application/json",
                }
            )
        self._connected = True
        logger.info("indmoney_client_connected")

    async def disconnect(self) -> None:
        """Disconnect from IndMoney API."""
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("indmoney_client_disconnected")

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to IndMoney API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            data: Request body

        Returns:
            Response data
        """
        if not self._session:
            raise IndMoneyError("Client not connected")

        url = f"{self.BASE_URL}{endpoint}"
        try:
            async with self._session.request(
                method, url, params=params, json=data
            ) as response:
                resp_data = await response.json()

                if response.status != 200:
                    raise IndMoneyError(
                        resp_data.get("message", "Unknown error"),
                        resp_data.get("code"),
                    )

                return resp_data

        except aiohttp.ClientError as e:
            logger.error("indmoney_request_error", error=str(e), endpoint=endpoint)
            raise IndMoneyError(f"Request failed: {e}") from e

    async def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary.

        Returns:
            Portfolio summary data
        """
        return await self._request("GET", "/v1/portfolio/summary")

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all positions.

        Returns:
            List of position data
        """
        data = await self._request("GET", "/v1/portfolio/positions")
        return data.get("positions", [])

    async def get_holdings(self) -> list[dict[str, Any]]:
        """Get all holdings.

        Returns:
            List of holding data
        """
        data = await self._request("GET", "/v1/portfolio/holdings")
        return data.get("holdings", [])

    async def get_pnl_summary(self) -> dict[str, Any]:
        """Get P&L summary.

        Returns:
            P&L summary data
        """
        return await self._request("GET", "/v1/portfolio/pnl")

    async def get_trade_history(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get trade history.

        Args:
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of trade records
        """
        params: dict[str, str] = {}
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date

        data = await self._request("GET", "/v1/portfolio/trades", params=params)
        return data.get("trades", [])

    async def verify_position(
        self,
        tradingsymbol: str,
        expected_quantity: int,
    ) -> bool:
        """Verify position matches expected quantity.

        Used for cross-validation with Kite positions.

        Args:
            tradingsymbol: Trading symbol
            expected_quantity: Expected position quantity

        Returns:
            True if position matches
        """
        try:
            positions = await self.get_positions()
            for pos in positions:
                if pos.get("symbol") == tradingsymbol:
                    actual_qty = pos.get("quantity", 0)
                    if actual_qty == expected_quantity:
                        return True
                    logger.warning(
                        "position_mismatch",
                        symbol=tradingsymbol,
                        expected=expected_quantity,
                        actual=actual_qty,
                    )
                    return False
            # Position not found
            return expected_quantity == 0
        except IndMoneyError as e:
            logger.error("position_verification_error", error=str(e))
            return False

    async def get_realized_pnl(self, tradingsymbol: str) -> Decimal:
        """Get realized P&L for a symbol.

        Args:
            tradingsymbol: Trading symbol

        Returns:
            Realized P&L amount
        """
        try:
            pnl_data = await self.get_pnl_summary()
            symbol_pnl = pnl_data.get("symbol_pnl", {}).get(tradingsymbol, {})
            return Decimal(str(symbol_pnl.get("realized", 0)))
        except (IndMoneyError, KeyError) as e:
            logger.error("realized_pnl_error", error=str(e))
            return Decimal("0")

    async def cross_validate_pnl(
        self,
        kite_pnl: Decimal,
        tolerance: Decimal = Decimal("100"),
    ) -> tuple[bool, Decimal]:
        """Cross-validate P&L with Kite.

        Args:
            kite_pnl: P&L from Kite
            tolerance: Acceptable difference threshold

        Returns:
            Tuple of (is_valid, difference)
        """
        try:
            pnl_data = await self.get_pnl_summary()
            indmoney_pnl = Decimal(str(pnl_data.get("total_pnl", 0)))
            difference = abs(kite_pnl - indmoney_pnl)

            is_valid = difference <= tolerance

            if not is_valid:
                logger.warning(
                    "pnl_cross_validation_failed",
                    kite_pnl=str(kite_pnl),
                    indmoney_pnl=str(indmoney_pnl),
                    difference=str(difference),
                )

            return is_valid, difference

        except IndMoneyError as e:
            logger.error("cross_validation_error", error=str(e))
            return False, Decimal("0")
