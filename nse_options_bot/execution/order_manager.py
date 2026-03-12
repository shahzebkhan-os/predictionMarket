"""Order manager - order lifecycle management.

Handles limit orders, SL-M orders, order state tracking.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Coroutine

import pytz
import structlog

from nse_options_bot.brokers.base import (
    BaseBroker,
    Order,
    OrderStatus,
    OrderType,
    ProductType,
    TransactionType,
)

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class OrderStage(str, Enum):
    """Order lifecycle stage."""

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass
class ManagedOrder:
    """Order with lifecycle management."""

    order_id: str
    tradingsymbol: str
    exchange: str
    transaction_type: TransactionType
    quantity: int
    order_type: OrderType
    product: ProductType

    # Prices
    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None
    average_price: Decimal | None = None

    # State
    stage: OrderStage = OrderStage.CREATED
    filled_quantity: int = 0
    pending_quantity: int = 0
    status_message: str = ""

    # Tracking
    created_at: datetime = field(default_factory=lambda: datetime.now(IST))
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    last_update: datetime | None = None

    # Retry handling
    retry_count: int = 0
    max_retries: int = 3

    # Related orders
    parent_order_id: str | None = None
    sl_order_id: str | None = None
    target_order_id: str | None = None

    @property
    def is_complete(self) -> bool:
        """Check if order is in terminal state."""
        return self.stage in (
            OrderStage.FILLED,
            OrderStage.REJECTED,
            OrderStage.CANCELLED,
            OrderStage.FAILED,
        )

    @property
    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.stage in (
            OrderStage.SUBMITTED,
            OrderStage.PENDING,
            OrderStage.PARTIALLY_FILLED,
        )


# Type alias for order callback
OrderCallback = Callable[[ManagedOrder], Coroutine[Any, Any, None]]


class OrderManager:
    """Order lifecycle manager.

    Responsibilities:
    - Submit and track orders
    - Handle partial fills
    - Manage SL-M orders
    - Retry failed orders
    - Track order state
    """

    # Order timing
    POLL_INTERVAL = 0.5  # seconds
    MAX_WAIT_TIME = 30  # seconds for order fill
    RETRY_DELAY = 1.0  # seconds between retries

    def __init__(
        self,
        broker: BaseBroker,
        on_fill: OrderCallback | None = None,
        on_reject: OrderCallback | None = None,
    ) -> None:
        """Initialize order manager.

        Args:
            broker: Broker client
            on_fill: Callback on order fill
            on_reject: Callback on order rejection
        """
        self._broker = broker
        self._on_fill = on_fill
        self._on_reject = on_reject

        self._orders: dict[str, ManagedOrder] = {}
        self._pending_orders: set[str] = set()

    async def place_order(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: TransactionType,
        quantity: int,
        order_type: OrderType = OrderType.LIMIT,
        product: ProductType = ProductType.NRML,
        limit_price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        tag: str = "",
    ) -> ManagedOrder:
        """Place an order.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange
            transaction_type: BUY or SELL
            quantity: Quantity
            order_type: Order type
            product: Product type
            limit_price: Limit price
            trigger_price: Trigger price for SL orders
            tag: Order tag

        Returns:
            ManagedOrder
        """
        managed = ManagedOrder(
            order_id="",  # Will be set after submission
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            pending_quantity=quantity,
            order_type=order_type,
            product=product,
            limit_price=limit_price,
            trigger_price=trigger_price,
        )

        try:
            # Submit to broker
            order_id = await self._broker.place_order(
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=order_type,
                product=product,
                price=float(limit_price) if limit_price else None,
                trigger_price=float(trigger_price) if trigger_price else None,
                tag=tag,
            )

            managed.order_id = order_id
            managed.stage = OrderStage.SUBMITTED
            managed.submitted_at = datetime.now(IST)

            self._orders[order_id] = managed
            self._pending_orders.add(order_id)

            logger.info(
                "order_placed",
                order_id=order_id,
                symbol=tradingsymbol,
                type=order_type.value,
                quantity=quantity,
            )

            # Start tracking
            asyncio.create_task(self._track_order(managed))

            return managed

        except Exception as e:
            managed.stage = OrderStage.FAILED
            managed.status_message = str(e)
            logger.error(
                "order_place_failed",
                symbol=tradingsymbol,
                error=str(e),
            )
            raise

    async def place_sl_order(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: TransactionType,
        quantity: int,
        trigger_price: Decimal,
        product: ProductType = ProductType.NRML,
        parent_order_id: str | None = None,
    ) -> ManagedOrder:
        """Place a stop-loss market order.

        Args:
            tradingsymbol: Trading symbol
            exchange: Exchange
            transaction_type: BUY or SELL
            quantity: Quantity
            trigger_price: SL trigger price
            product: Product type
            parent_order_id: Parent order ID

        Returns:
            ManagedOrder
        """
        managed = await self.place_order(
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=OrderType.SL_M,
            product=product,
            trigger_price=trigger_price,
        )

        managed.parent_order_id = parent_order_id

        # Link to parent
        if parent_order_id and parent_order_id in self._orders:
            self._orders[parent_order_id].sl_order_id = managed.order_id

        return managed

    async def modify_order(
        self,
        order_id: str,
        quantity: int | None = None,
        price: Decimal | None = None,
        trigger_price: Decimal | None = None,
        order_type: OrderType | None = None,
    ) -> bool:
        """Modify an existing order.

        Args:
            order_id: Order ID
            quantity: New quantity
            price: New price
            trigger_price: New trigger price
            order_type: New order type

        Returns:
            True if successful
        """
        if order_id not in self._orders:
            logger.warning("modify_unknown_order", order_id=order_id)
            return False

        managed = self._orders[order_id]

        if not managed.is_active:
            logger.warning(
                "modify_inactive_order",
                order_id=order_id,
                stage=managed.stage.value,
            )
            return False

        try:
            await self._broker.modify_order(
                order_id=order_id,
                quantity=quantity,
                price=float(price) if price else None,
                trigger_price=float(trigger_price) if trigger_price else None,
                order_type=order_type,
            )

            if quantity:
                managed.quantity = quantity
                managed.pending_quantity = quantity - managed.filled_quantity
            if price:
                managed.limit_price = price
            if trigger_price:
                managed.trigger_price = trigger_price

            managed.last_update = datetime.now(IST)

            logger.info(
                "order_modified",
                order_id=order_id,
                quantity=quantity,
                price=price,
            )

            return True

        except Exception as e:
            logger.error(
                "order_modify_failed",
                order_id=order_id,
                error=str(e),
            )
            return False

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Order ID

        Returns:
            True if successful
        """
        if order_id not in self._orders:
            logger.warning("cancel_unknown_order", order_id=order_id)
            return False

        managed = self._orders[order_id]

        if not managed.is_active:
            logger.warning(
                "cancel_inactive_order",
                order_id=order_id,
                stage=managed.stage.value,
            )
            return False

        try:
            await self._broker.cancel_order(order_id)

            managed.stage = OrderStage.CANCELLED
            managed.last_update = datetime.now(IST)
            self._pending_orders.discard(order_id)

            logger.info("order_cancelled", order_id=order_id)

            return True

        except Exception as e:
            logger.error(
                "order_cancel_failed",
                order_id=order_id,
                error=str(e),
            )
            return False

    async def cancel_all_pending(self) -> int:
        """Cancel all pending orders.

        Returns:
            Number of orders cancelled
        """
        cancelled = 0

        for order_id in list(self._pending_orders):
            if await self.cancel_order(order_id):
                cancelled += 1

        return cancelled

    async def _track_order(self, managed: ManagedOrder) -> None:
        """Track order until completion.

        Args:
            managed: Managed order
        """
        start_time = datetime.now(IST)
        elapsed = 0.0

        while elapsed < self.MAX_WAIT_TIME and managed.is_active:
            try:
                # Get order status
                order = await self._broker.get_order(managed.order_id)

                if order:
                    self._update_managed_order(managed, order)

                    if managed.stage == OrderStage.FILLED:
                        self._pending_orders.discard(managed.order_id)
                        if self._on_fill:
                            await self._on_fill(managed)
                        break

                    elif managed.stage == OrderStage.REJECTED:
                        self._pending_orders.discard(managed.order_id)
                        if self._on_reject:
                            await self._on_reject(managed)
                        break

            except Exception as e:
                logger.error(
                    "order_tracking_error",
                    order_id=managed.order_id,
                    error=str(e),
                )

            await asyncio.sleep(self.POLL_INTERVAL)
            elapsed = (datetime.now(IST) - start_time).total_seconds()

        # Timeout handling
        if elapsed >= self.MAX_WAIT_TIME and managed.is_active:
            logger.warning(
                "order_tracking_timeout",
                order_id=managed.order_id,
                stage=managed.stage.value,
            )

    def _update_managed_order(
        self,
        managed: ManagedOrder,
        order: Order,
    ) -> None:
        """Update managed order from broker order.

        Args:
            managed: Managed order
            order: Broker order
        """
        # Map broker status to stage
        status_map = {
            OrderStatus.PENDING: OrderStage.PENDING,
            OrderStatus.OPEN: OrderStage.PENDING,
            OrderStatus.COMPLETE: OrderStage.FILLED,
            OrderStatus.REJECTED: OrderStage.REJECTED,
            OrderStatus.CANCELLED: OrderStage.CANCELLED,
        }

        managed.stage = status_map.get(order.status, managed.stage)
        managed.filled_quantity = order.filled_quantity
        managed.pending_quantity = order.pending_quantity
        managed.average_price = Decimal(str(order.average_price))
        managed.status_message = order.status_message or ""
        managed.last_update = datetime.now(IST)

        if managed.stage == OrderStage.FILLED:
            managed.filled_at = datetime.now(IST)

    def get_order(self, order_id: str) -> ManagedOrder | None:
        """Get order by ID.

        Args:
            order_id: Order ID

        Returns:
            ManagedOrder or None
        """
        return self._orders.get(order_id)

    def get_pending_orders(self) -> list[ManagedOrder]:
        """Get all pending orders.

        Returns:
            List of pending orders
        """
        return [
            self._orders[oid]
            for oid in self._pending_orders
            if oid in self._orders
        ]

    def get_orders_for_symbol(self, tradingsymbol: str) -> list[ManagedOrder]:
        """Get all orders for a symbol.

        Args:
            tradingsymbol: Trading symbol

        Returns:
            List of orders
        """
        return [
            o for o in self._orders.values()
            if o.tradingsymbol == tradingsymbol
        ]

    async def wait_for_fill(
        self,
        order_id: str,
        timeout: float = 30.0,
    ) -> bool:
        """Wait for order to fill.

        Args:
            order_id: Order ID
            timeout: Timeout in seconds

        Returns:
            True if filled, False if timeout/rejected
        """
        if order_id not in self._orders:
            return False

        managed = self._orders[order_id]
        start = datetime.now(IST)

        while (datetime.now(IST) - start).total_seconds() < timeout:
            if managed.stage == OrderStage.FILLED:
                return True
            if managed.is_complete:
                return False

            await asyncio.sleep(0.5)

        return False

    def clear_completed(self) -> int:
        """Clear completed orders from tracking.

        Returns:
            Number of orders cleared
        """
        to_remove = [
            oid for oid, order in self._orders.items()
            if order.is_complete
        ]

        for oid in to_remove:
            del self._orders[oid]
            self._pending_orders.discard(oid)

        return len(to_remove)
