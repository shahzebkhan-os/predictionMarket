"""Abstract broker interface for trading operations.

All broker implementations (Kite, Paper) must implement this interface.
This ensures zero code changes when switching between paper and live modes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, TypeAlias

# Type aliases
InstrumentToken: TypeAlias = int
OrderId: TypeAlias = str
TradingSymbol: TypeAlias = str


class OrderType(str, Enum):
    """Order type enumeration."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"  # Stop Loss
    SL_M = "SL-M"  # Stop Loss Market


class TransactionType(str, Enum):
    """Buy or Sell transaction type."""

    BUY = "BUY"
    SELL = "SELL"


class ProductType(str, Enum):
    """Product type for positions."""

    NRML = "NRML"  # Normal (overnight)
    MIS = "MIS"  # Margin Intraday Square-off
    CNC = "CNC"  # Cash and Carry (delivery)


class OrderStatus(str, Enum):
    """Order status enumeration."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER PENDING"


class Variety(str, Enum):
    """Order variety."""

    REGULAR = "regular"
    AMO = "amo"  # After Market Order
    CO = "co"  # Cover Order
    ICEBERG = "iceberg"
    AUCTION = "auction"


class Exchange(str, Enum):
    """Exchange enumeration."""

    NSE = "NSE"
    NFO = "NFO"  # NSE F&O
    BSE = "BSE"
    BFO = "BFO"  # BSE F&O
    MCX = "MCX"


class OptionType(str, Enum):
    """Option type (Call or Put)."""

    CE = "CE"  # Call
    PE = "PE"  # Put


@dataclass(frozen=True)
class Instrument:
    """Instrument details."""

    instrument_token: InstrumentToken
    exchange_token: int
    tradingsymbol: TradingSymbol
    name: str
    last_price: Decimal
    expiry: datetime | None
    strike: Decimal | None
    tick_size: Decimal
    lot_size: int
    instrument_type: str
    segment: str
    exchange: Exchange

    @property
    def is_option(self) -> bool:
        """Check if instrument is an option."""
        return self.instrument_type in ("CE", "PE")

    @property
    def is_future(self) -> bool:
        """Check if instrument is a future."""
        return self.instrument_type == "FUT"

    @property
    def option_type(self) -> OptionType | None:
        """Get option type if applicable."""
        if self.instrument_type == "CE":
            return OptionType.CE
        if self.instrument_type == "PE":
            return OptionType.PE
        return None


@dataclass
class Quote:
    """Market quote data."""

    instrument_token: InstrumentToken
    tradingsymbol: TradingSymbol
    last_price: Decimal
    volume: int
    buy_quantity: int
    sell_quantity: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    change: Decimal
    ohlc: dict[str, Decimal] = field(default_factory=dict)
    depth: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    oi: int = 0  # Open Interest
    oi_day_high: int = 0
    oi_day_low: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Order:
    """Order details."""

    order_id: OrderId
    exchange_order_id: str | None
    tradingsymbol: TradingSymbol
    exchange: Exchange
    transaction_type: TransactionType
    order_type: OrderType
    product: ProductType
    variety: Variety
    quantity: int
    disclosed_quantity: int
    price: Decimal
    trigger_price: Decimal
    average_price: Decimal
    filled_quantity: int
    pending_quantity: int
    status: OrderStatus
    status_message: str
    tag: str
    order_timestamp: datetime | None
    exchange_timestamp: datetime | None
    placed_by: str = ""
    validity: str = "DAY"

    @property
    def is_complete(self) -> bool:
        """Check if order is complete."""
        return self.status == OrderStatus.COMPLETE

    @property
    def is_pending(self) -> bool:
        """Check if order is still pending."""
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.OPEN,
            OrderStatus.TRIGGER_PENDING,
        )


@dataclass
class Position:
    """Position details."""

    tradingsymbol: TradingSymbol
    exchange: Exchange
    instrument_token: InstrumentToken
    product: ProductType
    quantity: int
    overnight_quantity: int
    multiplier: int
    average_price: Decimal
    close_price: Decimal
    last_price: Decimal
    value: Decimal
    pnl: Decimal
    m2m: Decimal
    unrealised: Decimal
    realised: Decimal
    buy_quantity: int
    buy_price: Decimal
    buy_value: Decimal
    sell_quantity: int
    sell_price: Decimal
    sell_value: Decimal
    day_buy_quantity: int = 0
    day_buy_price: Decimal = Decimal("0")
    day_buy_value: Decimal = Decimal("0")
    day_sell_quantity: int = 0
    day_sell_price: Decimal = Decimal("0")
    day_sell_value: Decimal = Decimal("0")

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.quantity < 0


@dataclass
class Holding:
    """Holdings data."""

    tradingsymbol: TradingSymbol
    exchange: Exchange
    isin: str
    quantity: int
    t1_quantity: int
    realised_quantity: int
    authorised_quantity: int
    average_price: Decimal
    last_price: Decimal
    close_price: Decimal
    pnl: Decimal
    day_change: Decimal
    day_change_percentage: Decimal
    opening_quantity: int = 0
    collateral_quantity: int = 0
    collateral_type: str = ""


@dataclass
class Margins:
    """Margin details."""

    enabled: bool
    net: Decimal
    available: dict[str, Decimal]
    utilised: dict[str, Decimal]


class BaseBroker(ABC):
    """Abstract base class for broker implementations.

    All broker implementations must provide these methods.
    This ensures seamless switching between paper and live modes.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the broker."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker is connected."""
        ...

    # Instrument methods
    @abstractmethod
    async def get_instruments(self, exchange: Exchange | None = None) -> list[Instrument]:
        """Get list of all instruments.

        Args:
            exchange: Optional exchange filter (NSE, NFO, etc.)

        Returns:
            List of Instrument objects
        """
        ...

    @abstractmethod
    async def get_instrument(
        self, tradingsymbol: TradingSymbol, exchange: Exchange
    ) -> Instrument | None:
        """Get single instrument by symbol and exchange.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange (NSE, NFO, etc.)

        Returns:
            Instrument if found, None otherwise
        """
        ...

    # Quote methods
    @abstractmethod
    async def get_quote(
        self, instruments: list[tuple[Exchange, TradingSymbol]]
    ) -> dict[str, Quote]:
        """Get quotes for multiple instruments.

        Args:
            instruments: List of (exchange, tradingsymbol) tuples

        Returns:
            Dict mapping instrument key to Quote
        """
        ...

    @abstractmethod
    async def get_ltp(
        self, instruments: list[tuple[Exchange, TradingSymbol]]
    ) -> dict[str, Decimal]:
        """Get last traded price for instruments.

        Args:
            instruments: List of (exchange, tradingsymbol) tuples

        Returns:
            Dict mapping instrument key to LTP
        """
        ...

    # Order methods
    @abstractmethod
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
            exchange: Exchange (NSE, NFO)
            transaction_type: BUY or SELL
            quantity: Order quantity
            order_type: MARKET, LIMIT, SL, SL-M
            product: NRML, MIS, CNC
            price: Limit price (required for LIMIT orders)
            trigger_price: Trigger price (required for SL orders)
            validity: DAY, IOC, TTL
            variety: regular, amo, co, iceberg
            disclosed_quantity: Disclosed quantity
            tag: Order tag for identification

        Returns:
            Order ID
        """
        ...

    @abstractmethod
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
            order_id: Order ID to modify
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
        ...

    @abstractmethod
    async def cancel_order(
        self, order_id: OrderId, variety: Variety = Variety.REGULAR
    ) -> OrderId:
        """Cancel an existing order.

        Args:
            order_id: Order ID to cancel
            variety: Order variety

        Returns:
            Order ID
        """
        ...

    @abstractmethod
    async def get_orders(self) -> list[Order]:
        """Get all orders for the day.

        Returns:
            List of Order objects
        """
        ...

    @abstractmethod
    async def get_order_history(self, order_id: OrderId) -> list[Order]:
        """Get order history for a specific order.

        Args:
            order_id: Order ID

        Returns:
            List of Order objects showing order lifecycle
        """
        ...

    # Position methods
    @abstractmethod
    async def get_positions(self) -> dict[str, list[Position]]:
        """Get all positions.

        Returns:
            Dict with 'net' and 'day' position lists
        """
        ...

    @abstractmethod
    async def get_holdings(self) -> list[Holding]:
        """Get all holdings.

        Returns:
            List of Holding objects
        """
        ...

    # Margin methods
    @abstractmethod
    async def get_margins(self, segment: str = "equity") -> Margins:
        """Get margin details.

        Args:
            segment: equity or commodity

        Returns:
            Margins object
        """
        ...

    @abstractmethod
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
        ...


class TickerCallback:
    """Callback interface for ticker events."""

    async def on_ticks(self, ticks: list[dict[str, Any]]) -> None:
        """Called when ticks are received."""
        pass

    async def on_connect(self) -> None:
        """Called when ticker connects."""
        pass

    async def on_close(self, code: int, reason: str) -> None:
        """Called when ticker disconnects."""
        pass

    async def on_error(self, error: Exception) -> None:
        """Called on ticker error."""
        pass

    async def on_reconnect(self, attempts: int) -> None:
        """Called on reconnection attempt."""
        pass

    async def on_noreconnect(self) -> None:
        """Called when max reconnection attempts reached."""
        pass

    async def on_order_update(self, order: dict[str, Any]) -> None:
        """Called on order update."""
        pass


class BaseTicker(ABC):
    """Abstract base class for WebSocket ticker implementations."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the WebSocket."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the WebSocket."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if ticker is connected."""
        ...

    @abstractmethod
    async def subscribe(self, instrument_tokens: list[InstrumentToken]) -> None:
        """Subscribe to instrument tokens.

        Args:
            instrument_tokens: List of instrument tokens
        """
        ...

    @abstractmethod
    async def unsubscribe(self, instrument_tokens: list[InstrumentToken]) -> None:
        """Unsubscribe from instrument tokens.

        Args:
            instrument_tokens: List of instrument tokens
        """
        ...

    @abstractmethod
    async def set_mode(
        self, mode: str, instrument_tokens: list[InstrumentToken]
    ) -> None:
        """Set subscription mode for instruments.

        Args:
            mode: ltp, quote, or full
            instrument_tokens: List of instrument tokens
        """
        ...

    @abstractmethod
    def set_callbacks(self, callbacks: TickerCallback) -> None:
        """Set callback handler for ticker events.

        Args:
            callbacks: TickerCallback instance
        """
        ...
