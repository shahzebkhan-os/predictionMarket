"""Kite Connect REST API client.

Handles all REST API interactions with Zerodha Kite Connect.
Auth: login URL → request_token → generate_session → access_token (expires daily 06:00 IST).
REST limit: 3 req/sec.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

import aiohttp
import structlog

from nse_options_bot.brokers.base import (
    BaseBroker,
    Exchange,
    Holding,
    Instrument,
    Margins,
    Order,
    OrderId,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Quote,
    TradingSymbol,
    TransactionType,
    Variety,
)
from nse_options_bot.config import settings

logger = structlog.get_logger(__name__)


class KiteRateLimiter:
    """Rate limiter for Kite API (3 requests per second)."""

    def __init__(self, requests_per_second: int = 3) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second
        """
        self._requests_per_second = requests_per_second
        self._interval = 1.0 / requests_per_second
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire permission to make a request."""
        async with self._lock:
            current_time = asyncio.get_event_loop().time()
            time_since_last = current_time - self._last_request_time
            if time_since_last < self._interval:
                await asyncio.sleep(self._interval - time_since_last)
            self._last_request_time = asyncio.get_event_loop().time()


class KiteConnectError(Exception):
    """Kite Connect API error."""

    def __init__(
        self, message: str, code: int | None = None, data: Any = None
    ) -> None:
        """Initialize error.

        Args:
            message: Error message
            code: Error code
            data: Additional error data
        """
        super().__init__(message)
        self.code = code
        self.data = data


class KiteClient(BaseBroker):
    """Kite Connect REST API client implementation."""

    BASE_URL = "https://api.kite.trade"
    LOGIN_URL = "https://kite.zerodha.com/connect/login"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
    ) -> None:
        """Initialize Kite client.

        Args:
            api_key: Kite API key
            api_secret: Kite API secret
            access_token: Kite access token (if already authenticated)
        """
        self._api_key = api_key or settings.kite_api_key
        self._api_secret = api_secret or settings.kite_api_secret.get_secret_value()
        self._access_token = (
            access_token or settings.kite_access_token.get_secret_value()
        )
        self._session: aiohttp.ClientSession | None = None
        self._rate_limiter = KiteRateLimiter(settings.kite_requests_per_second)
        self._connected = False
        self._instruments_cache: dict[str, Instrument] = {}

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self._session is not None

    def get_login_url(self) -> str:
        """Get login URL for user authentication.

        Returns:
            Login URL
        """
        return f"{self.LOGIN_URL}?api_key={self._api_key}&v=3"

    async def connect(self) -> None:
        """Establish connection to Kite API."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={
                    "X-Kite-Version": "3",
                    "Authorization": f"token {self._api_key}:{self._access_token}",
                }
            )
        self._connected = True
        logger.info("kite_client_connected")

    async def disconnect(self) -> None:
        """Disconnect from Kite API."""
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("kite_client_disconnected")

    async def generate_session(self, request_token: str) -> dict[str, Any]:
        """Generate access token from request token.

        Args:
            request_token: Request token from login redirect

        Returns:
            Session data including access_token
        """
        import hashlib

        checksum = hashlib.sha256(
            f"{self._api_key}{request_token}{self._api_secret}".encode()
        ).hexdigest()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.BASE_URL}/session/token",
                data={
                    "api_key": self._api_key,
                    "request_token": request_token,
                    "checksum": checksum,
                },
            ) as response:
                data = await response.json()
                if data.get("status") != "success":
                    raise KiteConnectError(
                        data.get("message", "Session generation failed"),
                        data.get("error_type"),
                    )
                self._access_token = data["data"]["access_token"]
                return data["data"]

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Kite API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            data: Request body data

        Returns:
            Response data
        """
        if not self._session:
            raise KiteConnectError("Client not connected")

        await self._rate_limiter.acquire()

        url = f"{self.BASE_URL}{endpoint}"
        try:
            async with self._session.request(
                method, url, params=params, data=data
            ) as response:
                resp_data = await response.json()

                if resp_data.get("status") != "success":
                    raise KiteConnectError(
                        resp_data.get("message", "Unknown error"),
                        resp_data.get("error_type"),
                        resp_data.get("data"),
                    )

                return resp_data.get("data", {})

        except aiohttp.ClientError as e:
            logger.error("kite_request_error", error=str(e), endpoint=endpoint)
            raise KiteConnectError(f"Request failed: {e}") from e

    async def get_instruments(
        self, exchange: Exchange | None = None
    ) -> list[Instrument]:
        """Get list of all instruments.

        Args:
            exchange: Optional exchange filter

        Returns:
            List of Instrument objects
        """
        # Kite returns CSV for instruments endpoint
        if not self._session:
            raise KiteConnectError("Client not connected")

        await self._rate_limiter.acquire()

        exchange_str = exchange.value if exchange else ""
        url = f"{self.BASE_URL}/instruments"
        if exchange_str:
            url = f"{url}/{exchange_str}"

        async with self._session.get(url) as response:
            text = await response.text()

        instruments = []
        lines = text.strip().split("\n")
        if len(lines) <= 1:
            return instruments

        # Skip header row
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 12:
                continue

            try:
                expiry = None
                if parts[5]:
                    expiry = datetime.strptime(parts[5], "%Y-%m-%d")

                strike = None
                if parts[6]:
                    strike = Decimal(parts[6])

                instrument = Instrument(
                    instrument_token=int(parts[0]),
                    exchange_token=int(parts[1]),
                    tradingsymbol=parts[2],
                    name=parts[3],
                    last_price=Decimal(parts[4]) if parts[4] else Decimal("0"),
                    expiry=expiry,
                    strike=strike,
                    tick_size=Decimal(parts[7]) if parts[7] else Decimal("0.05"),
                    lot_size=int(parts[8]) if parts[8] else 1,
                    instrument_type=parts[9],
                    segment=parts[10],
                    exchange=Exchange(parts[11]) if parts[11] else Exchange.NSE,
                )
                instruments.append(instrument)

                # Cache instrument
                key = f"{instrument.exchange.value}:{instrument.tradingsymbol}"
                self._instruments_cache[key] = instrument

            except (ValueError, IndexError) as e:
                logger.warning("instrument_parse_error", line=line, error=str(e))
                continue

        logger.info("instruments_loaded", count=len(instruments), exchange=exchange_str)
        return instruments

    async def get_instrument(
        self, tradingsymbol: TradingSymbol, exchange: Exchange
    ) -> Instrument | None:
        """Get single instrument by symbol and exchange.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange

        Returns:
            Instrument if found
        """
        key = f"{exchange.value}:{tradingsymbol}"
        if key in self._instruments_cache:
            return self._instruments_cache[key]

        # Load instruments if cache is empty
        if not self._instruments_cache:
            await self.get_instruments(exchange)

        return self._instruments_cache.get(key)

    async def get_quote(
        self, instruments: list[tuple[Exchange, TradingSymbol]]
    ) -> dict[str, Quote]:
        """Get quotes for multiple instruments.

        Args:
            instruments: List of (exchange, tradingsymbol) tuples

        Returns:
            Dict mapping instrument key to Quote
        """
        if not instruments:
            return {}

        # Format: NFO:NIFTY24D1925500CE
        keys = [f"{ex.value}:{sym}" for ex, sym in instruments]
        params = {"i": keys}

        data = await self._request("GET", "/quote", params=params)

        quotes = {}
        for key, quote_data in data.items():
            try:
                ohlc = quote_data.get("ohlc", {})
                depth = quote_data.get("depth", {"buy": [], "sell": []})

                quote = Quote(
                    instrument_token=quote_data.get("instrument_token", 0),
                    tradingsymbol=key.split(":")[-1],
                    last_price=Decimal(str(quote_data.get("last_price", 0))),
                    volume=quote_data.get("volume", 0),
                    buy_quantity=quote_data.get("buy_quantity", 0),
                    sell_quantity=quote_data.get("sell_quantity", 0),
                    open=Decimal(str(ohlc.get("open", 0))),
                    high=Decimal(str(ohlc.get("high", 0))),
                    low=Decimal(str(ohlc.get("low", 0))),
                    close=Decimal(str(ohlc.get("close", 0))),
                    change=Decimal(str(quote_data.get("net_change", 0))),
                    ohlc={k: Decimal(str(v)) for k, v in ohlc.items()},
                    depth=depth,
                    oi=quote_data.get("oi", 0),
                    oi_day_high=quote_data.get("oi_day_high", 0),
                    oi_day_low=quote_data.get("oi_day_low", 0),
                    timestamp=datetime.now(),
                )
                quotes[key] = quote
            except (KeyError, ValueError) as e:
                logger.warning("quote_parse_error", key=key, error=str(e))
                continue

        return quotes

    async def get_ltp(
        self, instruments: list[tuple[Exchange, TradingSymbol]]
    ) -> dict[str, Decimal]:
        """Get last traded price for instruments.

        Args:
            instruments: List of (exchange, tradingsymbol) tuples

        Returns:
            Dict mapping instrument key to LTP
        """
        if not instruments:
            return {}

        keys = [f"{ex.value}:{sym}" for ex, sym in instruments]
        params = {"i": keys}

        data = await self._request("GET", "/quote/ltp", params=params)

        return {
            key: Decimal(str(ltp_data.get("last_price", 0)))
            for key, ltp_data in data.items()
        }

    async def place_order(
        self,
        tradingsymbol: TradingSymbol,
        exchange: Exchange,
        transaction_type: TransactionType,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        product: ProductType = ProductType.NRML,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        validity: str = "DAY",
        variety: Variety = Variety.REGULAR,
        disclosed_quantity: int = 0,
        tag: str = "",
    ) -> OrderId:
        """Place an order.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange
            transaction_type: BUY or SELL
            quantity: Order quantity
            order_type: MARKET, LIMIT, SL, SL-M
            product: NRML, MIS, CNC
            price: Limit price
            trigger_price: Trigger price
            validity: DAY, IOC, TTL
            variety: regular, amo, co, iceberg
            disclosed_quantity: Disclosed quantity
            tag: Order tag

        Returns:
            Order ID
        """
        data: dict[str, Any] = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange.value,
            "transaction_type": transaction_type.value,
            "quantity": quantity,
            "order_type": order_type.value,
            "product": product.value,
            "validity": validity,
        }

        if price is not None:
            data["price"] = float(price)
        if trigger_price is not None:
            data["trigger_price"] = float(trigger_price)
        if disclosed_quantity > 0:
            data["disclosed_quantity"] = disclosed_quantity
        if tag:
            data["tag"] = tag

        response = await self._request(
            "POST", f"/orders/{variety.value}", data=data
        )

        order_id = response.get("order_id", "")
        logger.info(
            "order_placed",
            order_id=order_id,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type.value,
            quantity=quantity,
        )
        return order_id

    async def modify_order(
        self,
        order_id: OrderId,
        variety: Variety = Variety.REGULAR,
        quantity: int | None = None,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        order_type: OrderType | None = None,
        validity: str | None = None,
        disclosed_quantity: int | None = None,
    ) -> OrderId:
        """Modify an existing order.

        Args:
            order_id: Order ID
            variety: Order variety
            quantity: New quantity
            price: New price
            trigger_price: New trigger price
            order_type: New order type
            validity: New validity
            disclosed_quantity: New disclosed quantity

        Returns:
            Order ID
        """
        data: dict[str, Any] = {}

        if quantity is not None:
            data["quantity"] = quantity
        if price is not None:
            data["price"] = float(price)
        if trigger_price is not None:
            data["trigger_price"] = float(trigger_price)
        if order_type is not None:
            data["order_type"] = order_type.value
        if validity is not None:
            data["validity"] = validity
        if disclosed_quantity is not None:
            data["disclosed_quantity"] = disclosed_quantity

        response = await self._request(
            "PUT", f"/orders/{variety.value}/{order_id}", data=data
        )

        logger.info("order_modified", order_id=order_id)
        return response.get("order_id", order_id)

    async def cancel_order(
        self, order_id: OrderId, variety: Variety = Variety.REGULAR
    ) -> OrderId:
        """Cancel an existing order.

        Args:
            order_id: Order ID
            variety: Order variety

        Returns:
            Order ID
        """
        response = await self._request(
            "DELETE", f"/orders/{variety.value}/{order_id}"
        )

        logger.info("order_cancelled", order_id=order_id)
        return response.get("order_id", order_id)

    async def get_orders(self) -> list[Order]:
        """Get all orders for the day.

        Returns:
            List of Order objects
        """
        data = await self._request("GET", "/orders")

        orders = []
        for order_data in data:
            try:
                order = self._parse_order(order_data)
                orders.append(order)
            except (KeyError, ValueError) as e:
                logger.warning("order_parse_error", error=str(e))
                continue

        return orders

    async def get_order_history(self, order_id: OrderId) -> list[Order]:
        """Get order history for a specific order.

        Args:
            order_id: Order ID

        Returns:
            List of Order objects
        """
        data = await self._request("GET", f"/orders/{order_id}")

        orders = []
        for order_data in data:
            try:
                order = self._parse_order(order_data)
                orders.append(order)
            except (KeyError, ValueError) as e:
                logger.warning("order_history_parse_error", error=str(e))
                continue

        return orders

    def _parse_order(self, order_data: dict[str, Any]) -> Order:
        """Parse order data into Order object.

        Args:
            order_data: Raw order data

        Returns:
            Order object
        """
        order_timestamp = None
        if order_data.get("order_timestamp"):
            order_timestamp = datetime.fromisoformat(
                order_data["order_timestamp"].replace("Z", "+00:00")
            )

        exchange_timestamp = None
        if order_data.get("exchange_timestamp"):
            exchange_timestamp = datetime.fromisoformat(
                order_data["exchange_timestamp"].replace("Z", "+00:00")
            )

        return Order(
            order_id=order_data["order_id"],
            exchange_order_id=order_data.get("exchange_order_id"),
            tradingsymbol=order_data["tradingsymbol"],
            exchange=Exchange(order_data["exchange"]),
            transaction_type=TransactionType(order_data["transaction_type"]),
            order_type=OrderType(order_data["order_type"]),
            product=ProductType(order_data["product"]),
            variety=Variety(order_data["variety"]),
            quantity=order_data["quantity"],
            disclosed_quantity=order_data.get("disclosed_quantity", 0),
            price=Decimal(str(order_data.get("price", 0))),
            trigger_price=Decimal(str(order_data.get("trigger_price", 0))),
            average_price=Decimal(str(order_data.get("average_price", 0))),
            filled_quantity=order_data.get("filled_quantity", 0),
            pending_quantity=order_data.get("pending_quantity", 0),
            status=OrderStatus(order_data["status"]),
            status_message=order_data.get("status_message", ""),
            tag=order_data.get("tag", ""),
            order_timestamp=order_timestamp,
            exchange_timestamp=exchange_timestamp,
            placed_by=order_data.get("placed_by", ""),
            validity=order_data.get("validity", "DAY"),
        )

    async def get_positions(self) -> dict[str, list[Position]]:
        """Get all positions.

        Returns:
            Dict with 'net' and 'day' position lists
        """
        data = await self._request("GET", "/portfolio/positions")

        result = {"net": [], "day": []}
        for pos_type in ["net", "day"]:
            for pos_data in data.get(pos_type, []):
                try:
                    position = Position(
                        tradingsymbol=pos_data["tradingsymbol"],
                        exchange=Exchange(pos_data["exchange"]),
                        instrument_token=pos_data["instrument_token"],
                        product=ProductType(pos_data["product"]),
                        quantity=pos_data["quantity"],
                        overnight_quantity=pos_data.get("overnight_quantity", 0),
                        multiplier=pos_data.get("multiplier", 1),
                        average_price=Decimal(str(pos_data.get("average_price", 0))),
                        close_price=Decimal(str(pos_data.get("close_price", 0))),
                        last_price=Decimal(str(pos_data.get("last_price", 0))),
                        value=Decimal(str(pos_data.get("value", 0))),
                        pnl=Decimal(str(pos_data.get("pnl", 0))),
                        m2m=Decimal(str(pos_data.get("m2m", 0))),
                        unrealised=Decimal(str(pos_data.get("unrealised", 0))),
                        realised=Decimal(str(pos_data.get("realised", 0))),
                        buy_quantity=pos_data.get("buy_quantity", 0),
                        buy_price=Decimal(str(pos_data.get("buy_price", 0))),
                        buy_value=Decimal(str(pos_data.get("buy_value", 0))),
                        sell_quantity=pos_data.get("sell_quantity", 0),
                        sell_price=Decimal(str(pos_data.get("sell_price", 0))),
                        sell_value=Decimal(str(pos_data.get("sell_value", 0))),
                        day_buy_quantity=pos_data.get("day_buy_quantity", 0),
                        day_buy_price=Decimal(str(pos_data.get("day_buy_price", 0))),
                        day_buy_value=Decimal(str(pos_data.get("day_buy_value", 0))),
                        day_sell_quantity=pos_data.get("day_sell_quantity", 0),
                        day_sell_price=Decimal(str(pos_data.get("day_sell_price", 0))),
                        day_sell_value=Decimal(str(pos_data.get("day_sell_value", 0))),
                    )
                    result[pos_type].append(position)
                except (KeyError, ValueError) as e:
                    logger.warning("position_parse_error", error=str(e))
                    continue

        return result

    async def get_holdings(self) -> list[Holding]:
        """Get all holdings.

        Returns:
            List of Holding objects
        """
        data = await self._request("GET", "/portfolio/holdings")

        holdings = []
        for holding_data in data:
            try:
                holding = Holding(
                    tradingsymbol=holding_data["tradingsymbol"],
                    exchange=Exchange(holding_data["exchange"]),
                    isin=holding_data.get("isin", ""),
                    quantity=holding_data["quantity"],
                    t1_quantity=holding_data.get("t1_quantity", 0),
                    realised_quantity=holding_data.get("realised_quantity", 0),
                    authorised_quantity=holding_data.get("authorised_quantity", 0),
                    average_price=Decimal(str(holding_data.get("average_price", 0))),
                    last_price=Decimal(str(holding_data.get("last_price", 0))),
                    close_price=Decimal(str(holding_data.get("close_price", 0))),
                    pnl=Decimal(str(holding_data.get("pnl", 0))),
                    day_change=Decimal(str(holding_data.get("day_change", 0))),
                    day_change_percentage=Decimal(
                        str(holding_data.get("day_change_percentage", 0))
                    ),
                    opening_quantity=holding_data.get("opening_quantity", 0),
                    collateral_quantity=holding_data.get("collateral_quantity", 0),
                    collateral_type=holding_data.get("collateral_type", ""),
                )
                holdings.append(holding)
            except (KeyError, ValueError) as e:
                logger.warning("holding_parse_error", error=str(e))
                continue

        return holdings

    async def get_margins(self, segment: str = "equity") -> Margins:
        """Get margin details.

        Args:
            segment: equity or commodity

        Returns:
            Margins object
        """
        data = await self._request("GET", f"/user/margins/{segment}")

        return Margins(
            enabled=data.get("enabled", False),
            net=Decimal(str(data.get("net", 0))),
            available={
                k: Decimal(str(v)) for k, v in data.get("available", {}).items()
            },
            utilised={k: Decimal(str(v)) for k, v in data.get("utilised", {}).items()},
        )

    async def get_order_margins(
        self,
        orders: list[dict[str, Any]],
        mode: str = "compact",
    ) -> list[dict[str, Any]]:
        """Calculate margins required for orders.

        Args:
            orders: List of order params
            mode: compact or full

        Returns:
            List of margin requirements
        """
        data = await self._request(
            "POST",
            "/margins/orders",
            data={"orders": orders, "mode": mode},
        )
        return data
