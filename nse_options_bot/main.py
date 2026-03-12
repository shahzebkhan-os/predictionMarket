"""NSE Options Trading Bot - Main Entry Point.

Entry point for the trading bot. Coordinates all components.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, time
from typing import Any

import pytz
import structlog

from nse_options_bot.alerts.telegram import TelegramAlerter
from nse_options_bot.brokers.kite_client import KiteClient
from nse_options_bot.config import Settings
from nse_options_bot.execution.executor import ExecutionMode, Executor
from nse_options_bot.execution.risk import RiskLimits, RiskManager
from nse_options_bot.execution.sizer import PositionSizer
from nse_options_bot.market.instruments import InstrumentManager
from nse_options_bot.market.nse_calendar import NseCalendar
from nse_options_bot.market.option_chain import OptionChainManager
from nse_options_bot.market.regime import RegimeDetector
from nse_options_bot.paper.paper_broker import PaperBroker
from nse_options_bot.paper.paper_ledger import PaperLedger
from nse_options_bot.postmortem.engine import PostmortemEngine
from nse_options_bot.signals.engine import SignalEngine
from nse_options_bot.storage.db import close_db, init_db
from nse_options_bot.storage.event_log import EventLog, EventType
from nse_options_bot.watcher.state import ExitReason, OptionsTradeState
from nse_options_bot.watcher.watcher import TradeWatcher, WatcherConfig

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class TradingBot:
    """Main trading bot orchestrator.

    Coordinates all components:
    - Broker connection
    - Signal generation
    - Trade execution
    - Position monitoring
    - Risk management
    - Alerting
    """

    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 30)
    STARTUP_BUFFER = time(9, 10)  # Start 5 minutes before market

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize trading bot.

        Args:
            settings: Application settings
        """
        self._settings = settings or Settings()
        self._running = False

        # Components (initialized in setup)
        self._broker: KiteClient | PaperBroker | None = None
        self._calendar: NseCalendar | None = None
        self._instruments: InstrumentManager | None = None
        self._option_chain: OptionChainManager | None = None
        self._signal_engine: SignalEngine | None = None
        self._regime_detector: RegimeDetector | None = None
        self._executor: Executor | None = None
        self._watcher: TradeWatcher | None = None
        self._risk_manager: RiskManager | None = None
        self._sizer: PositionSizer | None = None
        self._alerter: TelegramAlerter | None = None
        self._postmortem: PostmortemEngine | None = None
        self._event_log: EventLog | None = None

    async def setup(self) -> None:
        """Initialize all components."""
        logger.info("bot_setup_starting")

        # Initialize database
        await init_db(self._settings.database_url)

        # Initialize event log
        self._event_log = EventLog(log_file="events.jsonl")

        # Initialize calendar
        self._calendar = NseCalendar()

        # Initialize broker
        if self._settings.paper_trading:
            logger.info("using_paper_broker")
            ledger = PaperLedger(
                initial_capital=self._settings.initial_capital,
            )
            self._broker = PaperBroker(ledger=ledger, calendar=self._calendar)
            execution_mode = ExecutionMode.PAPER
        else:
            logger.info("using_live_broker")
            self._broker = KiteClient(
                api_key=self._settings.kite_api_key,
                api_secret=self._settings.kite_api_secret,
            )
            execution_mode = ExecutionMode.LIVE

        # Initialize instruments
        self._instruments = InstrumentManager(broker=self._broker)
        await self._instruments.load_instruments()

        # Initialize option chain manager
        self._option_chain = OptionChainManager(
            broker=self._broker,
            instruments=self._instruments,
        )

        # Initialize signal engine
        self._signal_engine = SignalEngine(calendar=self._calendar)

        # Initialize regime detector
        self._regime_detector = RegimeDetector()

        # Initialize risk manager
        self._risk_manager = RiskManager(
            capital=self._settings.initial_capital,
            limits=RiskLimits(
                max_daily_loss_pct=self._settings.max_daily_loss_pct,
                kill_switch_loss_pct=self._settings.kill_switch_loss_pct,
            ),
        )

        # Initialize position sizer
        self._sizer = PositionSizer(
            capital=self._settings.initial_capital,
            max_loss_pct_per_trade=self._settings.max_loss_per_trade_pct,
        )

        # Initialize executor
        self._executor = Executor(
            broker=self._broker,
            mode=execution_mode,
            risk_manager=self._risk_manager,
            sizer=self._sizer,
        )

        # Initialize alerter
        if self._settings.telegram_bot_token and self._settings.telegram_chat_id:
            self._alerter = TelegramAlerter(
                bot_token=self._settings.telegram_bot_token,
                chat_id=self._settings.telegram_chat_id,
                enabled=self._settings.telegram_enabled,
            )

        # Initialize watcher
        watcher_config = WatcherConfig(
            max_daily_loss=self._settings.initial_capital * self._settings.max_daily_loss_pct / 100,
        )
        self._watcher = TradeWatcher(
            broker=self._broker,
            config=watcher_config,
            settings=self._settings,
            calendar=self._calendar,
            alerter=self._alerter,
        )
        self._watcher.set_regime_detector(self._regime_detector)
        self._watcher.set_exit_callback(self._on_trade_exit)

        # Initialize postmortem
        self._postmortem = PostmortemEngine()

        # Log startup
        self._event_log.append(
            EventType.SYSTEM_START,
            data={
                "mode": "paper" if self._settings.paper_trading else "live",
                "capital": float(self._settings.initial_capital),
            },
        )

        logger.info("bot_setup_complete")

    async def run(self) -> None:
        """Run the trading bot."""
        await self.setup()

        self._running = True
        logger.info("bot_started")

        # Send startup alert
        if self._alerter:
            await self._alerter.send_system_status(
                "STARTED",
                {
                    "mode": "Paper" if self._settings.paper_trading else "Live",
                    "capital": f"₹{float(self._settings.initial_capital):,.0f}",
                },
            )

        try:
            # Run main loops
            await asyncio.gather(
                self._market_session_loop(),
                self._watcher.start() if self._watcher else asyncio.sleep(0),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            logger.info("bot_cancelled")
        except Exception as e:
            logger.error("bot_error", error=str(e))
            raise
        finally:
            await self.shutdown()

    async def _market_session_loop(self) -> None:
        """Main market session loop."""
        while self._running:
            now = datetime.now(IST)

            # Check if trading day
            if not self._calendar.is_trading_day(now.date()):
                logger.info("not_trading_day", date=now.date())
                await asyncio.sleep(3600)  # Check again in an hour
                continue

            # Wait for market open
            if now.time() < self.STARTUP_BUFFER:
                wait_seconds = (
                    datetime.combine(now.date(), self.STARTUP_BUFFER) - now
                ).total_seconds()
                logger.info("waiting_for_startup", seconds=wait_seconds)
                await asyncio.sleep(max(60, wait_seconds))
                continue

            # Pre-market preparation
            if now.time() < self.MARKET_OPEN:
                await self._pre_market_prep()
                await asyncio.sleep(10)
                continue

            # Market hours
            if self.MARKET_OPEN <= now.time() <= self.MARKET_CLOSE:
                await self._trading_loop()
                await asyncio.sleep(5)
            else:
                # Post-market
                await self._post_market()
                await asyncio.sleep(60)

    async def _pre_market_prep(self) -> None:
        """Pre-market preparation."""
        logger.info("pre_market_prep")

        # Refresh instruments
        if self._instruments:
            await self._instruments.refresh()

        # Reset daily counters
        if self._risk_manager:
            self._risk_manager.reset_daily()

        if self._watcher:
            self._watcher.reset_daily()

    async def _trading_loop(self) -> None:
        """Main trading loop during market hours."""
        if not all([self._signal_engine, self._option_chain, self._regime_detector]):
            return

        try:
            # Refresh option chain
            for underlying in self._settings.underlyings:
                await self._option_chain.refresh(underlying)

            # Detect regime
            regime = await self._regime_detector.detect()

            # Generate signals
            signals = await self._signal_engine.generate_all(
                underlying="NIFTY",  # Primary
                option_chain=self._option_chain.get_snapshot("NIFTY"),
            )

            # Log composite signal
            composite = self._signal_engine.get_composite_signal(signals)
            logger.info(
                "signal_update",
                composite_score=composite.score,
                regime=regime.name if regime else "UNKNOWN",
            )

            # TODO: Implement trading logic based on signals and regime

        except Exception as e:
            logger.error("trading_loop_error", error=str(e))

    async def _post_market(self) -> None:
        """Post-market processing."""
        logger.info("post_market_processing")

        # Generate daily report
        if self._postmortem and self._alerter:
            from nse_options_bot.postmortem.reports import ReportGenerator

            generator = ReportGenerator(self._postmortem)
            summary = generator.generate_telegram_summary()
            await self._alerter.send_alert(summary)

    async def _on_trade_exit(
        self,
        trade: OptionsTradeState,
        reason: ExitReason,
    ) -> None:
        """Handle trade exit.

        Args:
            trade: Exited trade
            reason: Exit reason
        """
        logger.info(
            "trade_exit_callback",
            trade_id=trade.trade_id,
            reason=reason.value,
            pnl=float(trade.total_pnl),
        )

        # Log event
        if self._event_log:
            self._event_log.log_trade_exit(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                exit_reason=reason.value,
                pnl=float(trade.total_pnl),
                spot_price=float(trade.current_spot_price),
            )

        # Analyze trade
        if self._postmortem:
            self._postmortem.analyze_trade(trade)

        # Update risk manager
        if self._risk_manager:
            self._risk_manager.record_trade_exit(
                trade_id=trade.trade_id,
                pnl=trade.total_pnl,
                margin=trade.capital_allocated,
            )

        # Send alert
        if self._alerter:
            await self._alerter.send_trade_exit(
                strategy=trade.strategy_type,
                underlying=trade.underlying,
                exit_reason=reason.value,
                pnl=float(trade.total_pnl),
                duration_minutes=trade.time_in_trade_minutes,
            )

    async def shutdown(self) -> None:
        """Shutdown the bot."""
        logger.info("bot_shutting_down")

        self._running = False

        # Stop watcher
        if self._watcher:
            await self._watcher.stop()

        # Close database
        await close_db()

        # Close event log
        if self._event_log:
            self._event_log.append(EventType.SYSTEM_STOP, data={})
            self._event_log.close()

        # Send shutdown alert
        if self._alerter:
            await self._alerter.send_system_status("STOPPED", {})

        logger.info("bot_shutdown_complete")


def handle_signal(signum: int, frame: Any) -> None:
    """Handle OS signals."""
    logger.info("signal_received", signal=signum)
    sys.exit(0)


async def main() -> None:
    """Main entry point."""
    # Setup signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Load settings
    settings = Settings()

    # Create and run bot
    bot = TradingBot(settings)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
