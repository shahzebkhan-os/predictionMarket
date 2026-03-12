"""Trade watcher - main monitoring loop.

Fast loop (5s): Price updates, Greeks, exit checks
Slow loop (60s): Theta tracking, alerts, regime checks
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Any, Callable, Coroutine

import pytz
import structlog

from nse_options_bot.alerts.telegram import TelegramAlerter
from nse_options_bot.brokers.base import BaseBroker
from nse_options_bot.config import Settings
from nse_options_bot.market.nse_calendar import NseCalendar
from nse_options_bot.market.option_chain import OptionChainSnapshot
from nse_options_bot.market.regime import MarketRegime, RegimeDetector
from nse_options_bot.watcher.exits import ExitConditionChecker, ExitSignal
from nse_options_bot.watcher.greeks_tracker import GreeksTracker
from nse_options_bot.watcher.state import (
    ExitReason,
    OptionsTradeState,
    TradeStatus,
    TradeLegState,
)

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class WatcherConfig:
    """Watcher configuration."""

    fast_loop_interval: float = 5.0  # seconds
    slow_loop_interval: float = 60.0  # seconds
    exit_check_interval: float = 5.0  # seconds

    # Risk limits
    max_daily_loss: Decimal = Decimal("50000")  # INR
    kill_switch_loss_pct: float = 5.0  # % of capital

    # Alert settings
    alert_on_entry: bool = True
    alert_on_exit: bool = True
    alert_on_adjustment: bool = True


# Type alias for exit callback
ExitCallback = Callable[[OptionsTradeState, ExitReason], Coroutine[Any, Any, None]]


class TradeWatcher:
    """Trade monitoring and management.

    Responsibilities:
    - Monitor live positions
    - Update prices and Greeks
    - Check exit conditions
    - Trigger exits when needed
    - Track theta decay
    - Send alerts
    """

    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 30)

    def __init__(
        self,
        broker: BaseBroker,
        config: WatcherConfig | None = None,
        settings: Settings | None = None,
        calendar: NseCalendar | None = None,
        alerter: TelegramAlerter | None = None,
    ) -> None:
        """Initialize watcher.

        Args:
            broker: Broker client
            config: Watcher configuration
            settings: App settings
            calendar: NSE calendar
            alerter: Telegram alerter
        """
        self._broker = broker
        self._config = config or WatcherConfig()
        self._settings = settings
        self._calendar = calendar or NseCalendar()
        self._alerter = alerter

        # Active trades
        self._trades: dict[str, OptionsTradeState] = {}
        self._greeks_trackers: dict[str, GreeksTracker] = {}

        # State
        self._running = False
        self._kill_switch = False
        self._daily_pnl = Decimal("0")
        self._current_regime: MarketRegime | None = None

        # Callbacks
        self._exit_callback: ExitCallback | None = None

        # Components
        self._exit_checker = ExitConditionChecker()
        self._regime_detector: RegimeDetector | None = None

    def set_exit_callback(self, callback: ExitCallback) -> None:
        """Set callback for trade exits.

        Args:
            callback: Async callback function
        """
        self._exit_callback = callback

    def set_regime_detector(self, detector: RegimeDetector) -> None:
        """Set regime detector.

        Args:
            detector: Regime detector instance
        """
        self._regime_detector = detector

    def add_trade(self, trade: OptionsTradeState) -> None:
        """Add trade to watch.

        Args:
            trade: Trade state
        """
        self._trades[trade.trade_id] = trade
        self._greeks_trackers[trade.trade_id] = GreeksTracker()

        logger.info(
            "trade_added_to_watcher",
            trade_id=trade.trade_id,
            strategy=trade.strategy_type,
            underlying=trade.underlying,
        )

    def remove_trade(self, trade_id: str) -> None:
        """Remove trade from watch.

        Args:
            trade_id: Trade ID
        """
        if trade_id in self._trades:
            del self._trades[trade_id]
        if trade_id in self._greeks_trackers:
            del self._greeks_trackers[trade_id]

    def get_trade(self, trade_id: str) -> OptionsTradeState | None:
        """Get trade by ID.

        Args:
            trade_id: Trade ID

        Returns:
            OptionsTradeState or None
        """
        return self._trades.get(trade_id)

    def get_all_trades(self) -> list[OptionsTradeState]:
        """Get all active trades.

        Returns:
            List of trades
        """
        return list(self._trades.values())

    async def start(self) -> None:
        """Start the watcher loops."""
        self._running = True
        logger.info("watcher_started")

        await asyncio.gather(
            self._fast_loop(),
            self._slow_loop(),
            return_exceptions=True,
        )

    async def stop(self) -> None:
        """Stop the watcher."""
        self._running = False
        logger.info("watcher_stopped")

    def activate_kill_switch(self, reason: str = "") -> None:
        """Activate kill switch.

        Args:
            reason: Reason for activation
        """
        self._kill_switch = True
        logger.warning("kill_switch_activated", reason=reason)

        if self._alerter:
            asyncio.create_task(
                self._alerter.send_alert(
                    f"🚨 KILL SWITCH ACTIVATED: {reason}",
                    priority="critical",
                )
            )

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch."""
        self._kill_switch = False
        logger.info("kill_switch_deactivated")

    async def _fast_loop(self) -> None:
        """Fast loop - runs every 5 seconds.

        - Update prices
        - Update Greeks
        - Check exit conditions
        """
        while self._running:
            try:
                if not self._is_market_open():
                    await asyncio.sleep(self._config.fast_loop_interval)
                    continue

                for trade_id, trade in list(self._trades.items()):
                    if not trade.is_open:
                        continue

                    # Update prices
                    await self._update_trade_prices(trade)

                    # Update Greeks
                    await self._update_trade_greeks(trade)

                    # Take Greeks snapshot
                    tracker = self._greeks_trackers.get(trade_id)
                    if tracker:
                        tracker.take_snapshot(trade)

                    # Check exits
                    await self._check_exit_conditions(trade)

            except Exception as e:
                logger.error("fast_loop_error", error=str(e))

            await asyncio.sleep(self._config.fast_loop_interval)

    async def _slow_loop(self) -> None:
        """Slow loop - runs every 60 seconds.

        - Update regime
        - Track theta decay
        - Send periodic alerts
        - Log status
        """
        while self._running:
            try:
                if not self._is_market_open():
                    await asyncio.sleep(self._config.slow_loop_interval)
                    continue

                # Update regime
                if self._regime_detector:
                    self._current_regime = await self._regime_detector.detect()

                # Process each trade
                for trade_id, trade in list(self._trades.items()):
                    if not trade.is_open:
                        continue

                    # Track theta
                    await self._track_theta(trade)

                    # Get Greeks alerts
                    tracker = self._greeks_trackers.get(trade_id)
                    if tracker:
                        dte = self._calendar.days_to_expiry(trade.underlying)
                        alerts = tracker.get_alerts(trade, dte)

                        for alert in alerts:
                            if alert["severity"] in ("HIGH", "WARNING"):
                                trade.flag_attention(alert["message"])

                    # Log status
                    self._log_trade_status(trade)

                # Log portfolio status
                self._log_portfolio_status()

            except Exception as e:
                logger.error("slow_loop_error", error=str(e))

            await asyncio.sleep(self._config.slow_loop_interval)

    async def _update_trade_prices(self, trade: OptionsTradeState) -> None:
        """Update prices for all legs.

        Args:
            trade: Trade state
        """
        symbols = [leg.tradingsymbol for leg in trade.legs]

        try:
            quotes = await self._broker.get_quotes(symbols)

            for leg in trade.legs:
                if leg.tradingsymbol in quotes:
                    quote = quotes[leg.tradingsymbol]
                    leg.update_price(Decimal(str(quote.get("last_price", 0))))

            # Update spot price
            spot_symbol = trade.underlying
            if spot_symbol in quotes:
                trade.current_spot_price = Decimal(
                    str(quotes[spot_symbol].get("last_price", 0))
                )

            trade.last_update = datetime.now(IST)
            trade.update_peak_profit()

        except Exception as e:
            logger.error(
                "price_update_error",
                trade_id=trade.trade_id,
                error=str(e),
            )

    async def _update_trade_greeks(self, trade: OptionsTradeState) -> None:
        """Update Greeks for all legs.

        Args:
            trade: Trade state
        """
        # In a real implementation, fetch Greeks from option chain
        # For now, we assume Greeks are updated elsewhere
        trade.update_aggregate_greeks()

    async def _track_theta(self, trade: OptionsTradeState) -> None:
        """Track theta decay.

        Args:
            trade: Trade state
        """
        now = datetime.now(IST)
        market_open = datetime.combine(now.date(), self.MARKET_OPEN)
        market_open = IST.localize(market_open)

        hours_elapsed = (now - market_open).total_seconds() / 3600

        tracker = self._greeks_trackers.get(trade.trade_id)
        if tracker:
            tracker.calculate_theta_decay(trade, hours_elapsed)

    async def _check_exit_conditions(self, trade: OptionsTradeState) -> None:
        """Check if trade should exit.

        Args:
            trade: Trade state
        """
        # Update exit checker state
        self._exit_checker.kill_switch_active = self._kill_switch
        self._exit_checker.max_daily_loss = self._config.max_daily_loss
        self._exit_checker.current_daily_pnl = self._daily_pnl

        # Get entry IV (from first snapshot)
        tracker = self._greeks_trackers.get(trade.trade_id)
        entry_iv = 0.0
        current_iv = 0.0
        if tracker:
            first_snapshot = tracker.get_first_snapshot()
            last_snapshot = tracker.get_last_snapshot()

            if first_snapshot:
                first_leg_greeks = list(first_snapshot.leg_greeks.values())
                if first_leg_greeks:
                    entry_iv = first_leg_greeks[0].get("iv", 0)

            if last_snapshot:
                last_leg_greeks = list(last_snapshot.leg_greeks.values())
                if last_leg_greeks:
                    current_iv = last_leg_greeks[0].get("iv", 0)

        dte = self._calendar.days_to_expiry(trade.underlying)
        current_regime = self._current_regime.name if self._current_regime else ""

        # Check conditions
        signals = self._exit_checker.check_all_conditions(
            trade=trade,
            current_iv=current_iv,
            entry_iv=entry_iv,
            dte=dte,
            current_regime=current_regime,
        )

        should_exit, reason, description = self._exit_checker.get_exit_action(signals)

        if should_exit and reason:
            logger.info(
                "exit_triggered",
                trade_id=trade.trade_id,
                reason=reason.value,
                description=description,
            )

            # Execute exit
            await self._execute_exit(trade, reason, description)

    async def _execute_exit(
        self,
        trade: OptionsTradeState,
        reason: ExitReason,
        description: str,
    ) -> None:
        """Execute trade exit.

        Args:
            trade: Trade state
            reason: Exit reason
            description: Exit description
        """
        # Mark trade as closing
        trade.status = TradeStatus.CLOSING

        # Call exit callback if set
        if self._exit_callback:
            try:
                await self._exit_callback(trade, reason)
            except Exception as e:
                logger.error(
                    "exit_callback_error",
                    trade_id=trade.trade_id,
                    error=str(e),
                )

        # Mark trade as closed
        trade.mark_closed(reason, description)

        # Update daily P&L
        self._daily_pnl += trade.total_pnl

        # Send alert
        if self._alerter and self._config.alert_on_exit:
            pnl = float(trade.total_pnl)
            emoji = "🟢" if pnl >= 0 else "🔴"
            await self._alerter.send_alert(
                f"{emoji} Trade Exited: {trade.strategy_type}\n"
                f"Reason: {reason.value}\n"
                f"P&L: ₹{pnl:,.0f}\n"
                f"{description}",
                priority="high" if abs(pnl) > 10000 else "normal",
            )

        logger.info(
            "trade_exited",
            trade_id=trade.trade_id,
            reason=reason.value,
            pnl=float(trade.total_pnl),
        )

    def _is_market_open(self) -> bool:
        """Check if market is currently open.

        Returns:
            True if market is open
        """
        now = datetime.now(IST)

        if not self._calendar.is_trading_day(now.date()):
            return False

        current_time = now.time()
        return self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE

    def _log_trade_status(self, trade: OptionsTradeState) -> None:
        """Log trade status.

        Args:
            trade: Trade state
        """
        logger.info(
            "trade_status",
            trade_id=trade.trade_id,
            strategy=trade.strategy_type,
            pnl=float(trade.total_pnl),
            pnl_pct=trade.total_pnl_pct,
            net_delta=trade.net_delta,
            net_theta=trade.net_theta,
            time_in_trade=trade.time_in_trade_minutes,
        )

    def _log_portfolio_status(self) -> None:
        """Log overall portfolio status."""
        open_trades = [t for t in self._trades.values() if t.is_open]
        total_pnl = sum(t.total_pnl for t in open_trades)

        logger.info(
            "portfolio_status",
            open_trades=len(open_trades),
            total_unrealized_pnl=float(total_pnl),
            daily_pnl=float(self._daily_pnl),
            kill_switch=self._kill_switch,
            regime=self._current_regime.name if self._current_regime else "UNKNOWN",
        )

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary.

        Returns:
            Summary dict
        """
        open_trades = [t for t in self._trades.values() if t.is_open]
        closed_trades = [t for t in self._trades.values() if not t.is_open]

        return {
            "open_trades": len(open_trades),
            "closed_trades": len(closed_trades),
            "total_unrealized_pnl": sum(float(t.total_pnl) for t in open_trades),
            "total_realized_pnl": sum(float(t.total_pnl) for t in closed_trades),
            "daily_pnl": float(self._daily_pnl),
            "kill_switch_active": self._kill_switch,
            "current_regime": (
                self._current_regime.name if self._current_regime else None
            ),
            "trades": [t.to_dict() for t in open_trades],
        }

    def reset_daily(self) -> None:
        """Reset daily counters."""
        self._daily_pnl = Decimal("0")

        # Clear closed trades
        self._trades = {
            k: v for k, v in self._trades.items() if v.is_open
        }

        logger.info("daily_reset_completed")
