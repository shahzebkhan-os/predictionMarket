"""Kite Connect WebSocket ticker with asyncio integration.

KiteTicker WebSocket for live ticks, bridged to asyncio queue.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import aiohttp
import structlog

from nse_options_bot.brokers.base import BaseTicker, InstrumentToken, TickerCallback
from nse_options_bot.config import settings

logger = structlog.get_logger(__name__)


class TickMode(str, Enum):
    """Tick subscription modes."""

    LTP = "ltp"
    QUOTE = "quote"
    FULL = "full"


class KiteTickerError(Exception):
    """Kite Ticker error."""

    pass


class KiteTicker(BaseTicker):
    """Kite Connect WebSocket ticker with asyncio integration.

    Bridges KiteTicker to asyncio using an internal queue.
    """

    WEBSOCKET_URL = "wss://ws.kite.trade"
    RECONNECT_MAX_RETRIES = 50
    RECONNECT_DELAY = 5  # seconds

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
    ) -> None:
        """Initialize Kite ticker.

        Args:
            api_key: Kite API key
            access_token: Kite access token
        """
        self._api_key = api_key or settings.kite_api_key
        self._access_token = (
            access_token or settings.kite_access_token.get_secret_value()
        )
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._connected = False
        self._callbacks: TickerCallback | None = None
        self._subscribed_tokens: set[InstrumentToken] = set()
        self._mode_map: dict[InstrumentToken, TickMode] = {}
        self._tick_queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue()
        self._reconnect_attempts = 0
        self._running = False
        self._receive_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        """Check if ticker is connected."""
        return self._connected and self._ws is not None

    @property
    def tick_queue(self) -> asyncio.Queue[list[dict[str, Any]]]:
        """Get the tick queue for consuming ticks."""
        return self._tick_queue

    def set_callbacks(self, callbacks: TickerCallback) -> None:
        """Set callback handler.

        Args:
            callbacks: TickerCallback instance
        """
        self._callbacks = callbacks

    async def connect(self) -> None:
        """Connect to KiteTicker WebSocket."""
        if self._connected:
            return

        self._session = aiohttp.ClientSession()
        url = f"{self.WEBSOCKET_URL}?api_key={self._api_key}&access_token={self._access_token}"

        try:
            self._ws = await self._session.ws_connect(url)
            self._connected = True
            self._running = True
            self._reconnect_attempts = 0

            logger.info("kite_ticker_connected")

            if self._callbacks:
                await self._callbacks.on_connect()

            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())

            # Resubscribe to previously subscribed tokens
            if self._subscribed_tokens:
                await self.subscribe(list(self._subscribed_tokens))

        except Exception as e:
            logger.error("kite_ticker_connect_error", error=str(e))
            await self._handle_reconnect()

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False
        self._connected = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

        logger.info("kite_ticker_disconnected")

        if self._callbacks:
            await self._callbacks.on_close(1000, "Normal closure")

    async def _receive_loop(self) -> None:
        """Main receive loop for WebSocket messages."""
        if not self._ws:
            return

        try:
            async for msg in self._ws:
                if not self._running:
                    break

                if msg.type == aiohttp.WSMsgType.BINARY:
                    ticks = self._parse_binary(msg.data)
                    if ticks:
                        await self._tick_queue.put(ticks)
                        if self._callbacks:
                            await self._callbacks.on_ticks(ticks)

                elif msg.type == aiohttp.WSMsgType.TEXT:
                    # Handle text messages (usually order updates)
                    import json

                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "order" and self._callbacks:
                            await self._callbacks.on_order_update(data.get("data", {}))
                    except json.JSONDecodeError:
                        pass

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("kite_ticker_ws_error", error=str(self._ws.exception()))
                    if self._callbacks:
                        await self._callbacks.on_error(
                            self._ws.exception() or Exception("WebSocket error")
                        )
                    break

                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.warning("kite_ticker_ws_closed")
                    break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("kite_ticker_receive_error", error=str(e))
            if self._callbacks:
                await self._callbacks.on_error(e)

        # Connection lost, attempt reconnect
        if self._running:
            self._connected = False
            await self._handle_reconnect()

    async def _handle_reconnect(self) -> None:
        """Handle reconnection logic."""
        if not self._running:
            return

        self._reconnect_attempts += 1

        if self._reconnect_attempts > self.RECONNECT_MAX_RETRIES:
            logger.error("kite_ticker_max_reconnect_attempts")
            if self._callbacks:
                await self._callbacks.on_noreconnect()
            return

        logger.info(
            "kite_ticker_reconnecting",
            attempt=self._reconnect_attempts,
            delay=self.RECONNECT_DELAY,
        )

        if self._callbacks:
            await self._callbacks.on_reconnect(self._reconnect_attempts)

        await asyncio.sleep(self.RECONNECT_DELAY)
        await self.connect()

    async def subscribe(self, instrument_tokens: list[InstrumentToken]) -> None:
        """Subscribe to instrument tokens.

        Args:
            instrument_tokens: List of instrument tokens
        """
        if not instrument_tokens:
            return

        self._subscribed_tokens.update(instrument_tokens)

        if not self._ws or not self._connected:
            return

        message = self._build_subscribe_message(instrument_tokens)
        await self._ws.send_bytes(message)

        logger.info("kite_ticker_subscribed", tokens=instrument_tokens)

    async def unsubscribe(self, instrument_tokens: list[InstrumentToken]) -> None:
        """Unsubscribe from instrument tokens.

        Args:
            instrument_tokens: List of instrument tokens
        """
        if not instrument_tokens:
            return

        self._subscribed_tokens.difference_update(instrument_tokens)
        for token in instrument_tokens:
            self._mode_map.pop(token, None)

        if not self._ws or not self._connected:
            return

        message = self._build_unsubscribe_message(instrument_tokens)
        await self._ws.send_bytes(message)

        logger.info("kite_ticker_unsubscribed", tokens=instrument_tokens)

    async def set_mode(
        self, mode: str, instrument_tokens: list[InstrumentToken]
    ) -> None:
        """Set subscription mode for instruments.

        Args:
            mode: ltp, quote, or full
            instrument_tokens: List of instrument tokens
        """
        if not instrument_tokens:
            return

        tick_mode = TickMode(mode)
        for token in instrument_tokens:
            self._mode_map[token] = tick_mode

        if not self._ws or not self._connected:
            return

        message = self._build_mode_message(tick_mode, instrument_tokens)
        await self._ws.send_bytes(message)

        logger.info("kite_ticker_mode_set", mode=mode, tokens=instrument_tokens)

    def _build_subscribe_message(
        self, instrument_tokens: list[InstrumentToken]
    ) -> bytes:
        """Build subscribe message.

        Args:
            instrument_tokens: List of tokens

        Returns:
            Binary message
        """
        import json

        return json.dumps({"a": "subscribe", "v": instrument_tokens}).encode()

    def _build_unsubscribe_message(
        self, instrument_tokens: list[InstrumentToken]
    ) -> bytes:
        """Build unsubscribe message.

        Args:
            instrument_tokens: List of tokens

        Returns:
            Binary message
        """
        import json

        return json.dumps({"a": "unsubscribe", "v": instrument_tokens}).encode()

    def _build_mode_message(
        self, mode: TickMode, instrument_tokens: list[InstrumentToken]
    ) -> bytes:
        """Build mode message.

        Args:
            mode: Tick mode
            instrument_tokens: List of tokens

        Returns:
            Binary message
        """
        import json

        return json.dumps({"a": "mode", "v": [mode.value, instrument_tokens]}).encode()

    def _parse_binary(self, data: bytes) -> list[dict[str, Any]]:
        """Parse binary tick data.

        Args:
            data: Raw binary data

        Returns:
            List of parsed ticks
        """
        ticks = []

        # Number of packets
        if len(data) < 2:
            return ticks

        num_packets = struct.unpack(">H", data[0:2])[0]
        offset = 2

        for _ in range(num_packets):
            if offset + 2 > len(data):
                break

            packet_length = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2

            if offset + packet_length > len(data):
                break

            packet = data[offset : offset + packet_length]
            offset += packet_length

            tick = self._parse_packet(packet)
            if tick:
                ticks.append(tick)

        return ticks

    def _parse_packet(self, packet: bytes) -> dict[str, Any] | None:
        """Parse single tick packet.

        Args:
            packet: Packet bytes

        Returns:
            Parsed tick dict
        """
        if len(packet) < 8:
            return None

        # Parse based on packet length
        instrument_token = struct.unpack(">I", packet[0:4])[0]

        # Determine packet type based on length
        # LTP packet: 8 bytes
        # Quote packet: 44 bytes
        # Full packet: 184 bytes (with market depth)

        if len(packet) == 8:
            # LTP mode
            ltp = struct.unpack(">i", packet[4:8])[0] / 100.0
            return {
                "instrument_token": instrument_token,
                "mode": "ltp",
                "last_price": Decimal(str(ltp)),
                "tradable": True,
            }

        if len(packet) >= 44:
            # Quote or Full mode
            values = struct.unpack(">iIIIIIIIII", packet[4:44])
            tick: dict[str, Any] = {
                "instrument_token": instrument_token,
                "mode": "full" if len(packet) >= 184 else "quote",
                "last_price": Decimal(str(values[0] / 100.0)),
                "last_traded_quantity": values[1],
                "average_traded_price": Decimal(str(values[2] / 100.0)),
                "volume_traded": values[3],
                "total_buy_quantity": values[4],
                "total_sell_quantity": values[5],
                "ohlc": {
                    "open": Decimal(str(values[6] / 100.0)),
                    "high": Decimal(str(values[7] / 100.0)),
                    "low": Decimal(str(values[8] / 100.0)),
                    "close": Decimal(str(values[9] / 100.0)),
                },
                "tradable": True,
            }

            # Extended quote data
            if len(packet) >= 64:
                ext_values = struct.unpack(">iIiii", packet[44:64])
                tick["change"] = Decimal(str(ext_values[0] / 100.0))
                tick["exchange_timestamp"] = datetime.fromtimestamp(ext_values[1])
                tick["oi"] = ext_values[2]
                tick["oi_day_high"] = ext_values[3]
                tick["oi_day_low"] = ext_values[4]

            # Market depth (full mode)
            if len(packet) >= 184:
                depth = {"buy": [], "sell": []}
                depth_offset = 64

                # 5 levels each for buy and sell
                for side in ["buy", "sell"]:
                    for _ in range(5):
                        if depth_offset + 20 > len(packet):
                            break
                        d = struct.unpack(">iiiii", packet[depth_offset : depth_offset + 20])
                        depth[side].append(
                            {
                                "quantity": d[0],
                                "price": Decimal(str(d[1] / 100.0)),
                                "orders": d[2],
                            }
                        )
                        depth_offset += 20

                tick["depth"] = depth

            return tick

        return None


class AsyncTickQueue:
    """Async queue wrapper for consuming ticks.

    Provides an async iterator interface for tick consumption.
    """

    def __init__(self, ticker: KiteTicker) -> None:
        """Initialize queue wrapper.

        Args:
            ticker: KiteTicker instance
        """
        self._ticker = ticker

    def __aiter__(self) -> "AsyncTickQueue":
        """Return async iterator."""
        return self

    async def __anext__(self) -> list[dict[str, Any]]:
        """Get next batch of ticks.

        Returns:
            List of tick dicts
        """
        return await self._ticker.tick_queue.get()

    async def get(self, timeout: float | None = None) -> list[dict[str, Any]] | None:
        """Get ticks with timeout.

        Args:
            timeout: Timeout in seconds

        Returns:
            List of ticks or None on timeout
        """
        try:
            return await asyncio.wait_for(
                self._ticker.tick_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None
