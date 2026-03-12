"""Trade executor - routes to paper or live broker.

Handles atomic multi-leg execution for option strategies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.brokers.base import (
    BaseBroker,
    OrderType,
    ProductType,
    TransactionType,
)
from nse_options_bot.execution.order_manager import ManagedOrder, OrderManager, OrderStage
from nse_options_bot.execution.risk import RiskManager
from nse_options_bot.execution.sizer import PositionSizer, SizingResult
from nse_options_bot.market.regime import MarketRegime
from nse_options_bot.strategies.base_strategy import StrategyLeg, StrategyPosition

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class ExecutionMode(str, Enum):
    """Execution mode."""

    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionStatus(str, Enum):
    """Execution status."""

    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class LegExecution:
    """Single leg execution result."""

    leg: StrategyLeg
    order: ManagedOrder | None = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    error: str = ""
    fill_price: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")


@dataclass
class StrategyExecution:
    """Complete strategy execution result."""

    strategy_type: str
    underlying: str
    legs: list[LegExecution] = field(default_factory=list)
    status: ExecutionStatus = ExecutionStatus.PENDING
    total_premium: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    execution_time_ms: float = 0.0
    error: str = ""

    @property
    def is_complete(self) -> bool:
        """Check if all legs are filled."""
        return all(
            leg.status == ExecutionStatus.COMPLETE
            for leg in self.legs
        )

    @property
    def is_failed(self) -> bool:
        """Check if execution failed."""
        return any(
            leg.status == ExecutionStatus.FAILED
            for leg in self.legs
        )


class Executor:
    """Trade execution engine.

    Responsibilities:
    - Route to paper or live broker
    - Atomic multi-leg execution
    - Rollback on partial failures
    - Slippage tracking
    """

    # Execution limits
    MAX_SLIPPAGE_PCT = 5.0  # Max 5% slippage per leg
    ORDER_TIMEOUT = 30.0  # seconds

    def __init__(
        self,
        broker: BaseBroker,
        mode: ExecutionMode = ExecutionMode.PAPER,
        risk_manager: RiskManager | None = None,
        sizer: PositionSizer | None = None,
    ) -> None:
        """Initialize executor.

        Args:
            broker: Broker client (paper or live)
            mode: Execution mode
            risk_manager: Risk manager
            sizer: Position sizer
        """
        self._broker = broker
        self._mode = mode
        self._risk_manager = risk_manager
        self._sizer = sizer

        self._order_manager = OrderManager(broker)
        self._executions: list[StrategyExecution] = []

    @property
    def mode(self) -> ExecutionMode:
        """Get execution mode."""
        return self._mode

    async def execute_strategy(
        self,
        position: StrategyPosition,
        expected_prices: dict[str, Decimal],
        product: ProductType = ProductType.NRML,
        use_limit_orders: bool = True,
        atomic: bool = True,
    ) -> StrategyExecution:
        """Execute a complete strategy.

        Args:
            position: Strategy position with legs
            expected_prices: Expected prices for each leg
            product: Product type (NRML/MIS)
            use_limit_orders: Use limit orders vs market
            atomic: Rollback on partial failure

        Returns:
            StrategyExecution result
        """
        start_time = datetime.now(IST)

        execution = StrategyExecution(
            strategy_type=position.strategy_type.value,
            underlying=position.underlying,
        )

        # Pre-execution checks
        if self._risk_manager:
            allowed, reason = self._risk_manager.check_entry_allowed(
                strategy_type=position.strategy_type,
                required_margin=self._estimate_margin(position),
                max_loss=position.max_loss,
                num_lots=position.legs[0].quantity if position.legs else 1,
                underlying=position.underlying,
            )

            if not allowed:
                execution.status = ExecutionStatus.FAILED
                execution.error = f"Risk check failed: {reason}"
                logger.warning("execution_blocked", reason=reason)
                return execution

        # Execute each leg
        for leg in position.legs:
            expected_price = expected_prices.get(leg.tradingsymbol, Decimal("0"))
            leg_exec = await self._execute_leg(
                leg=leg,
                expected_price=expected_price,
                product=product,
                use_limit=use_limit_orders,
            )
            execution.legs.append(leg_exec)

            if leg_exec.status == ExecutionStatus.FAILED:
                logger.error(
                    "leg_execution_failed",
                    symbol=leg.tradingsymbol,
                    error=leg_exec.error,
                )

                if atomic:
                    # Rollback executed legs
                    await self._rollback_execution(execution)
                    execution.status = ExecutionStatus.ROLLED_BACK
                    execution.error = f"Rolled back due to: {leg_exec.error}"
                    break
                else:
                    execution.status = ExecutionStatus.PARTIAL

        # Calculate totals
        if execution.status not in (ExecutionStatus.FAILED, ExecutionStatus.ROLLED_BACK):
            execution.status = ExecutionStatus.COMPLETE if execution.is_complete else ExecutionStatus.PARTIAL

            for leg_exec in execution.legs:
                if leg_exec.status == ExecutionStatus.COMPLETE:
                    if leg_exec.leg.transaction_type == TransactionType.SELL:
                        execution.total_premium -= leg_exec.fill_price * Decimal(str(leg_exec.leg.total_quantity))
                    else:
                        execution.total_premium += leg_exec.fill_price * Decimal(str(leg_exec.leg.total_quantity))

                    execution.total_slippage += leg_exec.slippage

        execution.execution_time_ms = (datetime.now(IST) - start_time).total_seconds() * 1000

        self._executions.append(execution)

        logger.info(
            "strategy_executed",
            strategy=position.strategy_type.value,
            status=execution.status.value,
            premium=float(execution.total_premium),
            slippage=float(execution.total_slippage),
            time_ms=execution.execution_time_ms,
        )

        return execution

    async def _execute_leg(
        self,
        leg: StrategyLeg,
        expected_price: Decimal,
        product: ProductType,
        use_limit: bool,
    ) -> LegExecution:
        """Execute a single leg.

        Args:
            leg: Strategy leg
            expected_price: Expected fill price
            product: Product type
            use_limit: Use limit order

        Returns:
            LegExecution result
        """
        leg_exec = LegExecution(leg=leg)

        try:
            # Determine order type and price
            if use_limit:
                order_type = OrderType.LIMIT
                # Add/subtract slippage buffer for limit orders
                buffer = expected_price * Decimal("0.002")  # 0.2% buffer
                if leg.transaction_type == TransactionType.BUY:
                    limit_price = expected_price + buffer
                else:
                    limit_price = expected_price - buffer
            else:
                order_type = OrderType.MARKET
                limit_price = None

            # Place order
            order = await self._order_manager.place_order(
                tradingsymbol=leg.tradingsymbol,
                exchange=leg.exchange,
                transaction_type=leg.transaction_type,
                quantity=leg.total_quantity,
                order_type=order_type,
                product=product,
                limit_price=limit_price,
            )

            leg_exec.order = order

            # Wait for fill
            filled = await self._order_manager.wait_for_fill(
                order.order_id,
                timeout=self.ORDER_TIMEOUT,
            )

            if filled and order.average_price:
                leg_exec.status = ExecutionStatus.COMPLETE
                leg_exec.fill_price = order.average_price
                leg_exec.slippage = abs(order.average_price - expected_price)

                # Check slippage
                slippage_pct = float(leg_exec.slippage / expected_price * 100) if expected_price > 0 else 0
                if slippage_pct > self.MAX_SLIPPAGE_PCT:
                    logger.warning(
                        "high_slippage",
                        symbol=leg.tradingsymbol,
                        slippage_pct=slippage_pct,
                    )

                # Update leg
                leg.entry_price = order.average_price
                leg.order_id = order.order_id

            else:
                leg_exec.status = ExecutionStatus.FAILED
                leg_exec.error = order.status_message or "Order not filled"

                # Cancel if still pending
                if order.is_active:
                    await self._order_manager.cancel_order(order.order_id)

        except Exception as e:
            leg_exec.status = ExecutionStatus.FAILED
            leg_exec.error = str(e)
            logger.error(
                "leg_execution_error",
                symbol=leg.tradingsymbol,
                error=str(e),
            )

        return leg_exec

    async def _rollback_execution(self, execution: StrategyExecution) -> None:
        """Rollback executed legs.

        Args:
            execution: Execution to rollback
        """
        for leg_exec in execution.legs:
            if leg_exec.status == ExecutionStatus.COMPLETE and leg_exec.order:
                # Place opposite order
                opposite_type = (
                    TransactionType.SELL
                    if leg_exec.leg.transaction_type == TransactionType.BUY
                    else TransactionType.BUY
                )

                try:
                    await self._order_manager.place_order(
                        tradingsymbol=leg_exec.leg.tradingsymbol,
                        exchange=leg_exec.leg.exchange,
                        transaction_type=opposite_type,
                        quantity=leg_exec.order.filled_quantity,
                        order_type=OrderType.MARKET,
                        product=leg_exec.leg.product,
                    )
                    logger.info(
                        "leg_rolled_back",
                        symbol=leg_exec.leg.tradingsymbol,
                    )
                except Exception as e:
                    logger.error(
                        "rollback_failed",
                        symbol=leg_exec.leg.tradingsymbol,
                        error=str(e),
                    )

    async def close_position(
        self,
        position: StrategyPosition,
        use_limit_orders: bool = False,
    ) -> StrategyExecution:
        """Close an open position.

        Args:
            position: Position to close
            use_limit_orders: Use limit orders

        Returns:
            StrategyExecution result
        """
        execution = StrategyExecution(
            strategy_type=position.strategy_type.value,
            underlying=position.underlying,
        )

        for leg in position.legs:
            # Reverse the transaction type
            close_type = (
                TransactionType.SELL
                if leg.transaction_type == TransactionType.BUY
                else TransactionType.BUY
            )

            # Create close leg
            close_leg = StrategyLeg(
                tradingsymbol=leg.tradingsymbol,
                exchange=leg.exchange,
                strike=leg.strike,
                option_type=leg.option_type,
                transaction_type=close_type,
                quantity=leg.quantity,
                lot_size=leg.lot_size,
                product=leg.product,
            )

            expected_price = leg.current_price or leg.entry_price or Decimal("0")

            leg_exec = await self._execute_leg(
                leg=close_leg,
                expected_price=expected_price,
                product=leg.product,
                use_limit=use_limit_orders,
            )
            execution.legs.append(leg_exec)

        execution.status = (
            ExecutionStatus.COMPLETE
            if execution.is_complete
            else ExecutionStatus.PARTIAL
        )

        return execution

    async def place_sl_orders(
        self,
        position: StrategyPosition,
        sl_prices: dict[str, Decimal],
    ) -> dict[str, ManagedOrder]:
        """Place stop-loss orders for position.

        Args:
            position: Position
            sl_prices: SL trigger prices for each leg

        Returns:
            Dict of symbol to SL order
        """
        sl_orders = {}

        for leg in position.legs:
            if leg.tradingsymbol not in sl_prices:
                continue

            # For short positions, SL is a buy order
            # For long positions, SL is a sell order
            sl_type = (
                TransactionType.BUY
                if leg.transaction_type == TransactionType.SELL
                else TransactionType.SELL
            )

            sl_order = await self._order_manager.place_sl_order(
                tradingsymbol=leg.tradingsymbol,
                exchange=leg.exchange,
                transaction_type=sl_type,
                quantity=leg.total_quantity,
                trigger_price=sl_prices[leg.tradingsymbol],
                product=leg.product,
                parent_order_id=leg.order_id,
            )

            sl_orders[leg.tradingsymbol] = sl_order

        return sl_orders

    def _estimate_margin(self, position: StrategyPosition) -> Decimal:
        """Estimate margin requirement.

        Args:
            position: Strategy position

        Returns:
            Estimated margin
        """
        margin = Decimal("0")

        for leg in position.legs:
            if leg.transaction_type == TransactionType.SELL:
                # Short options need margin
                leg_value = (leg.entry_price or Decimal("0")) * Decimal(str(leg.total_quantity))
                margin += leg_value * Decimal("0.15")  # 15% margin
            else:
                # Long options need full premium
                margin += (leg.entry_price or Decimal("0")) * Decimal(str(leg.total_quantity))

        return margin

    def get_execution_stats(self) -> dict[str, Any]:
        """Get execution statistics.

        Returns:
            Stats dict
        """
        if not self._executions:
            return {"total_executions": 0}

        complete = [e for e in self._executions if e.status == ExecutionStatus.COMPLETE]
        failed = [e for e in self._executions if e.status == ExecutionStatus.FAILED]

        total_slippage = sum(e.total_slippage for e in complete)
        avg_time = sum(e.execution_time_ms for e in complete) / len(complete) if complete else 0

        return {
            "total_executions": len(self._executions),
            "complete": len(complete),
            "failed": len(failed),
            "success_rate": len(complete) / len(self._executions) * 100 if self._executions else 0,
            "total_slippage": float(total_slippage),
            "avg_execution_time_ms": avg_time,
            "mode": self._mode.value,
        }
