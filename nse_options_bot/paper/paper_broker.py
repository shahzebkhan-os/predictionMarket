"""Paper trading broker implementation.

PaperBroker implements identical interface to KiteClient.
Zero code changes to switch modes.

place_order() behavior:
- Fill price = market_price + slippage_model.calculate(symbol, qty, order_type)
- Slippage: ATM options 0.5-1.5pts, OTM(>200pts away) 1.5-4pts, market orders 2x
- Random 50-300ms fill delay
- Reject orders outside 09:15-15:25 IST
- Reject if qty > 5% of avg daily volume
- Apply STT: 0.0625% of premium on sell side
- Apply brokerage: ₹20 flat per executed order
- Simulate SPAN+Exposure margin at flat 15% for short options
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

import pytz
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
from nse_options_bot.paper.paper_ledger import PaperLedger
from nse_options_bot.paper.slippage_model import SlippageModel

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class PaperBrokerError(Exception):
    """Paper broker error."""

    pass


class PaperBroker(BaseBroker):
    """Paper trading broker with realistic simulation.

    Implements the same interface as KiteClient for seamless mode switching.
    """

    # Market timing (IST)
    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 25)  # 5 min before actual close for order entry

    # Fill delay range (milliseconds)
    MIN_FILL_DELAY_MS = 50
    MAX_FILL_DELAY_MS = 300

    # Volume rejection threshold
    MAX_VOLUME_PCT = 0.05  # 5% of avg daily volume

    def __init__(
        self,
        initial_capital: Decimal | None = None,
        slippage_model: SlippageModel | None = None,
    ) -> None:
        """Initialize paper broker.

        Args:
            initial_capital: Starting capital
            slippage_model: Slippage model for fill price calculation
        """
        self._ledger = PaperLedger(initial_capital=initial_capital)
        self._slippage_model = slippage_model or SlippageModel()
        self._connected = False

        # Simulated market data
        self._market_prices: dict[str, Decimal] = {}
        self._spot_prices: dict[str, Decimal] = {}
        self._avg_volumes: dict[str, int] = {}

        # Instrument cache
        self._instruments: dict[str, Instrument] = {}

        # Order tracking
        self._orders: dict[OrderId, Order] = {}
        self._order_counter = 0

    @property
    def is_connected(self) -> bool:
        """Check if broker is connected."""
        return self._connected

    @property
    def ledger(self) -> PaperLedger:
        """Get paper ledger."""
        return self._ledger

    async def connect(self) -> None:
        """Establish connection (no-op for paper broker)."""
        self._connected = True
        logger.info("paper_broker_connected")

    async def disconnect(self) -> None:
        """Disconnect (no-op for paper broker)."""
        self._connected = False
        logger.info("paper_broker_disconnected")

    def set_market_price(
        self, exchange: Exchange, tradingsymbol: TradingSymbol, price: Decimal
    ) -> None:
        """Set simulated market price for an instrument.

        Args:
            exchange: Exchange
            tradingsymbol: Trading symbol
            price: Current price
        """
        key = f"{exchange.value}:{tradingsymbol}"
        self._market_prices[key] = price

    def set_spot_price(self, symbol: str, price: Decimal) -> None:
        """Set spot price for underlying.

        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY)
            price: Current spot price
        """
        self._spot_prices[symbol] = price

    def set_avg_volume(self, tradingsymbol: TradingSymbol, volume: int) -> None:
        """Set average daily volume for a symbol.

        Args:
            tradingsymbol: Trading symbol
            volume: Average daily volume
        """
        self._avg_volumes[tradingsymbol] = volume
        self._slippage_model.set_avg_volume(tradingsymbol, volume)

    def add_instrument(self, instrument: Instrument) -> None:
        """Add instrument to cache.

        Args:
            instrument: Instrument to add
        """
        key = f"{instrument.exchange.value}:{instrument.tradingsymbol}"
        self._instruments[key] = instrument

    def _is_market_open(self) -> bool:
        """Check if market is open for order entry.

        Returns:
            True if within market hours
        """
        now = datetime.now(IST).time()
        return self.MARKET_OPEN <= now <= self.MARKET_CLOSE

    def _check_volume_limit(
        self, tradingsymbol: TradingSymbol, quantity: int, lot_size: int
    ) -> bool:
        """Check if order exceeds volume limit.

        Args:
            tradingsymbol: Trading symbol
            quantity: Order quantity in lots
            lot_size: Lot size

        Returns:
            True if within limit
        """
        avg_volume = self._avg_volumes.get(tradingsymbol, 0)
        if avg_volume == 0:
            return True  # No volume data, allow order

        total_qty = quantity * lot_size
        return total_qty <= avg_volume * self.MAX_VOLUME_PCT

    def _generate_order_id(self) -> OrderId:
        """Generate unique order ID.

        Returns:
            Order ID string
        """
        self._order_counter += 1
        return f"PAPER-{self._order_counter:012d}"

    async def _simulate_fill_delay(self) -> None:
        """Simulate random fill delay."""
        delay_ms = random.randint(self.MIN_FILL_DELAY_MS, self.MAX_FILL_DELAY_MS)
        await asyncio.sleep(delay_ms / 1000)

    def _get_underlying_from_symbol(self, tradingsymbol: TradingSymbol) -> str:
        """Extract underlying symbol from option symbol.

        Args:
            tradingsymbol: Option trading symbol

        Returns:
            Underlying symbol
        """
        # Parse NIFTY24D1925500CE format
        for underlying in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            if tradingsymbol.startswith(underlying):
                return underlying
        return "NIFTY"  # Default

    def _parse_option_details(
        self, tradingsymbol: TradingSymbol
    ) -> tuple[str, Decimal | None, bool | None]:
        """Parse option details from symbol.

        Args:
            tradingsymbol: Trading symbol

        Returns:
            Tuple of (underlying, strike, is_call)
        """
        underlying = self._get_underlying_from_symbol(tradingsymbol)

        # Simple parsing - look for CE/PE at end
        is_call = None
        strike = None

        if tradingsymbol.endswith("CE"):
            is_call = True
        elif tradingsymbol.endswith("PE"):
            is_call = False

        # Try to extract strike
        try:
            # Remove underlying and option type
            remaining = tradingsymbol[len(underlying) :]
            # Remove date part (e.g., 24D19) and option type
            for opt_type in ["CE", "PE"]:
                if remaining.endswith(opt_type):
                    remaining = remaining[:-2]
                    break

            # Last part should be strike
            # Find where numbers start
            for i, c in enumerate(remaining):
                if c.isdigit():
                    strike = Decimal(remaining[i:])
                    break
        except (ValueError, IndexError):
            pass

        return underlying, strike, is_call

    async def get_instruments(
        self, exchange: Exchange | None = None
    ) -> list[Instrument]:
        """Get list of instruments.

        Args:
            exchange: Optional exchange filter

        Returns:
            List of Instrument objects
        """
        instruments = list(self._instruments.values())
        if exchange:
            instruments = [i for i in instruments if i.exchange == exchange]
        return instruments

    async def get_instrument(
        self, tradingsymbol: TradingSymbol, exchange: Exchange
    ) -> Instrument | None:
        """Get single instrument.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange

        Returns:
            Instrument if found
        """
        key = f"{exchange.value}:{tradingsymbol}"
        return self._instruments.get(key)

    async def get_quote(
        self, instruments: list[tuple[Exchange, TradingSymbol]]
    ) -> dict[str, Quote]:
        """Get quotes for instruments.

        Args:
            instruments: List of (exchange, tradingsymbol) tuples

        Returns:
            Dict mapping key to Quote
        """
        quotes = {}
        for exchange, tradingsymbol in instruments:
            key = f"{exchange.value}:{tradingsymbol}"
            price = self._market_prices.get(key, Decimal("0"))

            quotes[key] = Quote(
                instrument_token=0,
                tradingsymbol=tradingsymbol,
                last_price=price,
                volume=self._avg_volumes.get(tradingsymbol, 0),
                buy_quantity=0,
                sell_quantity=0,
                open=price,
                high=price,
                low=price,
                close=price,
                change=Decimal("0"),
            )

        return quotes

    async def get_ltp(
        self, instruments: list[tuple[Exchange, TradingSymbol]]
    ) -> dict[str, Decimal]:
        """Get last traded price.

        Args:
            instruments: List of (exchange, tradingsymbol) tuples

        Returns:
            Dict mapping key to LTP
        """
        return {
            f"{ex.value}:{sym}": self._market_prices.get(
                f"{ex.value}:{sym}", Decimal("0")
            )
            for ex, sym in instruments
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
        """Place an order in paper trading mode.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange
            transaction_type: BUY or SELL
            quantity: Order quantity in lots
            order_type: MARKET, LIMIT, SL, SL-M
            product: NRML, MIS
            price: Limit price
            trigger_price: Trigger price for SL orders
            validity: DAY, IOC
            variety: Order variety
            disclosed_quantity: Disclosed quantity
            tag: Order tag

        Returns:
            Order ID

        Raises:
            PaperBrokerError: If order is rejected
        """
        # Validate market hours
        if not self._is_market_open():
            raise PaperBrokerError(
                "Order rejected: Market is closed (09:15-15:25 IST)"
            )

        # Get instrument details
        key = f"{exchange.value}:{tradingsymbol}"
        instrument = self._instruments.get(key)
        lot_size = instrument.lot_size if instrument else 1

        # Check volume limit
        if not self._check_volume_limit(tradingsymbol, quantity, lot_size):
            raise PaperBrokerError(
                f"Order rejected: Quantity exceeds 5% of avg daily volume"
            )

        # Get market price
        market_price = self._market_prices.get(key)
        if market_price is None:
            raise PaperBrokerError(f"No market price available for {tradingsymbol}")

        # Parse option details for slippage calculation
        underlying, strike, is_call = self._parse_option_details(tradingsymbol)
        spot_price = self._spot_prices.get(underlying, market_price)

        # Calculate slippage
        is_market = order_type == OrderType.MARKET
        is_buy = transaction_type == TransactionType.BUY
        is_option = instrument.is_option if instrument else (strike is not None)

        slippage = self._slippage_model.calculate(
            symbol=tradingsymbol,
            quantity=quantity,
            lot_size=lot_size,
            is_market_order=is_market,
            spot_price=spot_price,
            strike_price=strike,
            is_call=is_call,
            is_buy=is_buy,
        )

        # Calculate fill price
        fill_price = self._slippage_model.apply_slippage(
            market_price, slippage, is_buy
        )

        # For limit orders, check if price is acceptable
        if order_type == OrderType.LIMIT and price is not None:
            if is_buy and price < fill_price:
                # Buy limit below fill price - order pending
                fill_price = price
            elif not is_buy and price > fill_price:
                # Sell limit above fill price - order pending
                fill_price = price

        # Generate order ID
        order_id = self._generate_order_id()

        # Create order record
        now = datetime.now(IST)
        order = Order(
            order_id=order_id,
            exchange_order_id=f"EX-{order_id}",
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            order_type=order_type,
            product=product,
            variety=variety,
            quantity=quantity * lot_size,
            disclosed_quantity=disclosed_quantity,
            price=price or Decimal("0"),
            trigger_price=trigger_price or Decimal("0"),
            average_price=fill_price,
            filled_quantity=quantity * lot_size,
            pending_quantity=0,
            status=OrderStatus.COMPLETE,
            status_message="",
            tag=tag,
            order_timestamp=now,
            exchange_timestamp=now,
        )
        self._orders[order_id] = order

        # Simulate fill delay
        await self._simulate_fill_delay()

        # Record trade in ledger
        instrument_token = instrument.instrument_token if instrument else 0
        success, message = self._ledger.record_trade(
            order_id=order_id,
            tradingsymbol=tradingsymbol,
            exchange=exchange.value,
            instrument_token=instrument_token,
            product=product.value,
            transaction_type=transaction_type.value,
            quantity=quantity,
            price=fill_price,
            lot_size=lot_size,
            is_option=is_option,
        )

        if not success:
            # Mark order as rejected
            order.status = OrderStatus.REJECTED
            order.status_message = message
            raise PaperBrokerError(f"Order rejected: {message}")

        logger.info(
            "paper_order_filled",
            order_id=order_id,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type.value,
            quantity=quantity,
            fill_price=str(fill_price),
            market_price=str(market_price),
            slippage=str(slippage),
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
        """Modify an order (not implemented for paper trading).

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

        Raises:
            PaperBrokerError: Modification not supported
        """
        # Paper trading fills orders immediately, so modification is not meaningful
        order = self._orders.get(order_id)
        if order is None:
            raise PaperBrokerError(f"Order not found: {order_id}")

        if order.status == OrderStatus.COMPLETE:
            raise PaperBrokerError("Cannot modify completed order")

        # Update order if pending
        if quantity is not None:
            order.quantity = quantity
        if price is not None:
            order.price = price
        if trigger_price is not None:
            order.trigger_price = trigger_price
        if order_type is not None:
            order.order_type = order_type

        return order_id

    async def cancel_order(
        self, order_id: OrderId, variety: Variety = Variety.REGULAR
    ) -> OrderId:
        """Cancel an order.

        Args:
            order_id: Order ID
            variety: Order variety

        Returns:
            Order ID

        Raises:
            PaperBrokerError: If order cannot be cancelled
        """
        order = self._orders.get(order_id)
        if order is None:
            raise PaperBrokerError(f"Order not found: {order_id}")

        if order.status == OrderStatus.COMPLETE:
            raise PaperBrokerError("Cannot cancel completed order")

        order.status = OrderStatus.CANCELLED
        order.status_message = "Cancelled by user"
        return order_id

    async def get_orders(self) -> list[Order]:
        """Get all orders.

        Returns:
            List of Order objects
        """
        return list(self._orders.values())

    async def get_order_history(self, order_id: OrderId) -> list[Order]:
        """Get order history.

        Args:
            order_id: Order ID

        Returns:
            List of Order objects
        """
        order = self._orders.get(order_id)
        if order is None:
            return []
        return [order]

    async def get_positions(self) -> dict[str, list[Position]]:
        """Get all positions.

        Returns:
            Dict with 'net' and 'day' position lists
        """
        positions = []
        for paper_pos in self._ledger.positions.values():
            position = Position(
                tradingsymbol=paper_pos.tradingsymbol,
                exchange=Exchange(paper_pos.exchange),
                instrument_token=paper_pos.instrument_token,
                product=ProductType(paper_pos.product),
                quantity=paper_pos.quantity * paper_pos.multiplier,
                overnight_quantity=0,
                multiplier=paper_pos.multiplier,
                average_price=paper_pos.average_price,
                close_price=paper_pos.last_price,
                last_price=paper_pos.last_price,
                value=paper_pos.value,
                pnl=paper_pos.unrealized_pnl + paper_pos.realized_pnl,
                m2m=paper_pos.unrealized_pnl,
                unrealised=paper_pos.unrealized_pnl,
                realised=paper_pos.realized_pnl,
                buy_quantity=paper_pos.quantity * paper_pos.multiplier
                if paper_pos.is_long
                else 0,
                buy_price=paper_pos.average_price if paper_pos.is_long else Decimal("0"),
                buy_value=paper_pos.value if paper_pos.is_long else Decimal("0"),
                sell_quantity=-paper_pos.quantity * paper_pos.multiplier
                if paper_pos.is_short
                else 0,
                sell_price=paper_pos.average_price
                if paper_pos.is_short
                else Decimal("0"),
                sell_value=paper_pos.value if paper_pos.is_short else Decimal("0"),
            )
            positions.append(position)

        return {"net": positions, "day": positions}

    async def get_holdings(self) -> list[Holding]:
        """Get all holdings (empty for F&O paper trading).

        Returns:
            Empty list
        """
        return []

    async def get_margins(self, segment: str = "equity") -> Margins:
        """Get margin details.

        Args:
            segment: Segment (ignored for paper)

        Returns:
            Margins object
        """
        return Margins(
            enabled=True,
            net=self._ledger.cash_balance,
            available={
                "cash": self._ledger.margin_available,
                "collateral": Decimal("0"),
                "intraday_payin": Decimal("0"),
            },
            utilised={
                "span": self._ledger.margin_used,
                "exposure": Decimal("0"),
                "option_premium": Decimal("0"),
            },
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
        results = []
        for order_params in orders:
            tradingsymbol = order_params.get("tradingsymbol", "")
            exchange = order_params.get("exchange", "NFO")
            quantity = order_params.get("quantity", 1)
            transaction_type = order_params.get("transaction_type", "BUY")

            key = f"{exchange}:{tradingsymbol}"
            price = self._market_prices.get(key, Decimal("100"))
            instrument = self._instruments.get(key)
            lot_size = instrument.lot_size if instrument else 1

            is_short = transaction_type == "SELL"
            is_option = instrument.is_option if instrument else True

            margin = self._ledger.calculate_margin_required(
                price, quantity, lot_size, is_short, is_option
            )

            results.append(
                {
                    "tradingsymbol": tradingsymbol,
                    "type": transaction_type,
                    "total": float(margin),
                    "span": float(margin) if is_short else 0,
                    "exposure": 0,
                    "option_premium": float(price * Decimal(str(quantity * lot_size)))
                    if not is_short
                    else 0,
                }
            )

        return results

    def update_mtm(self) -> None:
        """Update mark-to-market for all positions."""
        prices = {}
        for key, price in self._market_prices.items():
            positions = self._ledger.positions
            for pos_key in positions:
                if key == pos_key:
                    prices[pos_key] = price

        self._ledger.update_mtm(prices)

    def get_daily_statement(self):
        """Get and reset daily statement.

        Returns:
            Daily statement
        """
        return self._ledger.reset_daily_stats()
