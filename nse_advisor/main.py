"""
NSE Options Signal Advisor - Main Entry Point.

Orchestrates all system components and runs the main event loops.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
import sys
from datetime import datetime, time
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.data.nse_session import get_nse_session, NseSession
from nse_advisor.data.nse_fetcher import get_nse_fetcher
from nse_advisor.data.yfinance_fetcher import get_yfinance_fetcher
from nse_advisor.market.nse_calendar import get_nse_calendar
from nse_advisor.market.instruments import get_instrument_master
from nse_advisor.market.ban_list import get_ban_list_checker
from nse_advisor.market.circuit_breaker import get_circuit_breaker
from nse_advisor.market.option_chain import get_option_chain_manager
from nse_advisor.market.regime import get_regime_classifier
from nse_advisor.signals.engine import get_signal_engine
from nse_advisor.tracker.position_tracker import get_position_tracker
from nse_advisor.tracker.exit_advisor import get_exit_advisor
from nse_advisor.postmortem.engine import get_postmortem_engine
from nse_advisor.storage.db import init_database, close_database
from nse_advisor.storage.event_log import get_event_log, EventType, log_circuit_breaker
from nse_advisor.alerts.telegram import get_telegram_dispatcher, close_telegram_dispatcher

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


class NseAdvisor:
    """
    Main application class for NSE Options Signal Advisor.
    
    Coordinates all components and runs the main event loops.
    """
    
    def __init__(self) -> None:
        """Initialize the advisor."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        self._running = False
        self._scheduler: AsyncIOScheduler | None = None
        self._dashboard_process: subprocess.Popen | None = None
        
        # Component references
        self._nse_session: NseSession | None = None
        
    async def initialize(self) -> None:
        """
        Initialize all components.
        
        Startup sequence:
        1. Load config, validate env vars
        2. Init NSE session (seed cookies)
        3. Download NSE holiday list
        4. Download NFO instrument master
        5. Fetch F&O ban list
        6. Backfill historical data
        7. Fetch first option chain snapshot
        """
        logger.info("Starting NSE Options Signal Advisor...")
        
        # 1. Validate config
        logger.info("Loading configuration...")
        settings = get_settings()
        logger.info(
            "Config loaded",
            primary=settings.primary_underlying,
            secondary=settings.secondary_underlying,
        )
        
        # 2. Init database
        logger.info("Initializing database...")
        await init_database()
        
        # 3. Init NSE session
        logger.info("Initializing NSE session...")
        self._nse_session = get_nse_session()
        await self._nse_session.init_session()
        
        # 4. Load NSE calendar
        logger.info("Loading NSE holiday calendar...")
        calendar = get_nse_calendar()
        await calendar.refresh_holidays()
        
        # 5. Load instrument master
        logger.info("Loading instrument master...")
        instruments = get_instrument_master()
        await instruments.refresh()
        
        # 6. Fetch ban list
        logger.info("Fetching F&O ban list...")
        ban_list = get_ban_list_checker()
        await ban_list.refresh()
        
        # 7. Backfill historical data
        logger.info("Backfilling historical OHLCV data...")
        yf_fetcher = get_yfinance_fetcher()
        await yf_fetcher.backfill_ohlcv(
            symbols=["^NSEI", "^NSEBANK"],
            period="5d",
            interval="5m",
        )
        
        # 8. Backfill IV history
        logger.info("Backfilling IV history...")
        await yf_fetcher.backfill_iv_history(
            symbols=["^INDIAVIX"],
            days=252,
        )
        
        # 9. Fetch first option chain
        logger.info("Fetching initial option chain...")
        chain_manager = get_option_chain_manager()
        try:
            await chain_manager.refresh(settings.primary_underlying)
        except Exception as e:
            logger.warning(f"Failed to fetch initial option chain: {e}. System will retry in background.")
        
        logger.info("Initialization complete!")
    
    async def run(self) -> None:
        """
        Run the main event loops.
        
        Loops:
        a. option_chain_refresh_loop() - every 5s
        b. signal_scan_loop() - every SCAN_INTERVAL_SECONDS
        c. position_tracker.fast_loop() - every 5s
        d. position_tracker.slow_loop() - every 60s
        e. regime_classifier_loop() - every 15min
        f. global_cues_loop() - every 30min
        g. nse_session_refresh_loop() - every 25min
        h. circuit_breaker_monitor() - every 60s
        """
        self._running = True
        
        # Setup scheduler for periodic tasks
        self._scheduler = AsyncIOScheduler(timezone=self._ist)
        self._setup_scheduled_tasks()
        self._scheduler.start()
        
        # Start dashboard in subprocess
        self._start_dashboard()
        
        # Create tasks for all loops
        tasks = [
            asyncio.create_task(self._option_chain_loop()),
            asyncio.create_task(self._signal_scan_loop()),
            asyncio.create_task(self._position_tracker_fast_loop()),
            asyncio.create_task(self._position_tracker_slow_loop()),
            asyncio.create_task(self._regime_classifier_loop()),
            asyncio.create_task(self._circuit_breaker_loop()),
            asyncio.create_task(self._session_refresh_loop()),
        ]
        
        logger.info("All event loops started")
        
        # Wait for shutdown signal
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled, shutting down...")
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Initiating shutdown...")
        
        self._running = False
        
        # Stop scheduler
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
        
        # Stop dashboard
        if self._dashboard_process:
            self._dashboard_process.terminate()
        
        # Close connections
        await close_database()
        await close_telegram_dispatcher()
        
        # Send shutdown alert
        telegram = get_telegram_dispatcher()
        await telegram.send_risk_alert(
            "SHUTDOWN",
            "NSE Options Signal Advisor stopped",
            "Manual shutdown initiated",
        )
        
        logger.info("Shutdown complete")
    
    def _setup_scheduled_tasks(self) -> None:
        """Setup APScheduler tasks."""
        if not self._scheduler:
            return
        
        # Ban list refresh at 08:30 IST daily
        self._scheduler.add_job(
            self._refresh_ban_list,
            'cron',
            hour=8,
            minute=30,
            id='ban_list_refresh',
        )
        
        # Instrument master refresh every Monday 08:00 IST
        self._scheduler.add_job(
            self._refresh_instruments,
            'cron',
            day_of_week='mon',
            hour=8,
            minute=0,
            id='instrument_refresh',
        )
        
        # FII/DII update at 18:30 IST every trading day
        self._scheduler.add_job(
            self._update_fii_dii,
            'cron',
            day_of_week='mon-fri',
            hour=18,
            minute=30,
            id='fii_dii_update',
        )
        
        # Nightly postmortem at 16:30 IST every trading day
        self._scheduler.add_job(
            self._run_nightly_postmortem,
            'cron',
            day_of_week='mon-fri',
            hour=16,
            minute=30,
            id='nightly_postmortem',
        )
        
        logger.info("Scheduled tasks configured")
    
    def _start_dashboard(self) -> None:
        """Start Streamlit dashboard in subprocess."""
        try:
            self._dashboard_process = subprocess.Popen(
                [
                    sys.executable, "-m", "streamlit", "run",
                    "nse_advisor/dashboard/streamlit_app.py",
                    "--server.port", "8501",
                    "--server.headless", "true",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            logger.info("Dashboard started on port 8501")
        except Exception as e:
            logger.error(f"Failed to start dashboard: {e}")
    
    def _is_market_hours(self) -> bool:
        """Check if within market hours."""
        now = datetime.now(self._ist)
        
        # Weekday check
        if now.weekday() >= 5:
            return False
        
        # Time check (09:15 - 15:30)
        market_open = time(9, 15)
        market_close = time(15, 30)
        
        return market_open <= now.time() <= market_close
    
    # === Event Loops ===
    
    async def _option_chain_loop(self) -> None:
        """Option chain refresh loop - every 5 seconds."""
        chain_manager = get_option_chain_manager()
        underlying = self._settings.primary_underlying
        
        while self._running:
            try:
                if self._is_market_hours():
                    await chain_manager.refresh(underlying)
                    logger.debug("Option chain refreshed", underlying=underlying)
            except Exception as e:
                logger.error(f"Option chain refresh error: {e}")
            
            await asyncio.sleep(5)
    
    async def _signal_scan_loop(self) -> None:
        """Signal scanning loop - every SCAN_INTERVAL_SECONDS."""
        signal_engine = get_signal_engine()
        interval = self._settings.scan_interval_seconds
        timeout = interval * 0.8
        
        while self._running:
            try:
                if self._is_market_hours():
                    # Check circuit breaker
                    cb = get_circuit_breaker()
                    if not cb.is_market_halted():
                        # Scan with timeout
                        try:
                            result = await asyncio.wait_for(
                                signal_engine.scan(),
                                timeout=timeout,
                            )
                            if result and result.should_recommend:
                                logger.info(
                                    "Signal generated",
                                    score=result.composite_score,
                                    confidence=result.composite_confidence,
                                )
                        except asyncio.TimeoutError:
                            logger.warning("Signal scan timeout")
                            get_event_log().log(
                                EventType.SIGNAL_TIMEOUT,
                                {"timeout_seconds": timeout},
                            )
            except Exception as e:
                logger.error(f"Signal scan error: {e}")
            
            await asyncio.sleep(interval)
    
    async def _position_tracker_fast_loop(self) -> None:
        """Position tracker fast loop - price updates every 5 seconds."""
        tracker = get_position_tracker()
        chain_manager = get_option_chain_manager()
        
        while self._running:
            try:
                if self._is_market_hours():
                    chain = chain_manager.get_latest(self._settings.primary_underlying)
                    if chain:
                        tracker.update_prices(chain)
            except Exception as e:
                logger.error(f"Position tracker fast loop error: {e}")
            
            await asyncio.sleep(5)
    
    async def _position_tracker_slow_loop(self) -> None:
        """Position tracker slow loop - Greeks update every 60 seconds."""
        tracker = get_position_tracker()
        exit_advisor = get_exit_advisor()
        chain_manager = get_option_chain_manager()
        telegram = get_telegram_dispatcher()
        
        while self._running:
            try:
                if self._is_market_hours():
                    chain = chain_manager.get_latest(self._settings.primary_underlying)
                    if chain:
                        tracker.update_greeks(chain)
                        
                        # Check exit conditions
                        open_trades = tracker.get_open_trades()
                        regime = get_regime_classifier().get_current_regime()
                        
                        alerts = exit_advisor.get_priority_alerts(open_trades, regime)
                        for alert in alerts:
                            await telegram.send_exit_alert(alert)
                            logger.info(
                                "Exit alert sent",
                                trade_id=alert.trade_id,
                                alert_type=alert.alert_type,
                            )
            except Exception as e:
                logger.error(f"Position tracker slow loop error: {e}")
            
            await asyncio.sleep(60)
    
    async def _regime_classifier_loop(self) -> None:
        """Regime classification loop - every 15 minutes."""
        classifier = get_regime_classifier()
        
        while self._running:
            try:
                if self._is_market_hours():
                    # Fetch data for classification
                    from nse_advisor.data.yfinance_fetcher import get_yfinance_fetcher
                    from nse_advisor.market.option_chain import get_chain_builder
                    
                    yf = get_yfinance_fetcher()
                    builder = get_chain_builder()
                    
                    price_data = await yf.backfill_candles(self._settings.primary_underlying, count=50)
                    chain = builder.get_cached_snapshot(self._settings.primary_underlying)
                    vix = await yf.fetch_india_vix() if hasattr(yf, 'fetch_india_vix') else 0.0
                    
                    if not price_data.empty:
                        old_regime = classifier.get_current_regime()
                        new_regime = await asyncio.to_thread(
                            classifier.classify, 
                            price_data=price_data,
                            chain=chain,
                            vix=vix
                        )
                        
                        if old_regime and new_regime and old_regime.regime != new_regime.regime:
                            from nse_advisor.storage.event_log import log_regime_change
                            log_regime_change(
                                old_regime.regime.value,
                                new_regime.regime.value,
                                self._settings.primary_underlying,
                            )
                            logger.info(
                                "Regime changed",
                                old=old_regime.regime.value,
                                new=new_regime.regime.value,
                            )
            except Exception as e:
                logger.error(f"Regime classifier error: {e}")
            
            await asyncio.sleep(900)  # 15 minutes
    
    async def _circuit_breaker_loop(self) -> None:
        """Circuit breaker monitoring loop - every 60 seconds."""
        cb = get_circuit_breaker()
        telegram = get_telegram_dispatcher()
        was_halted = False
        
        while self._running:
            try:
                await cb.check()
                is_halted = cb.is_market_halted()
                
                if is_halted and not was_halted:
                    # Market just halted
                    log_circuit_breaker(True, cb.halt_level)
                    await telegram.send_circuit_breaker_alert(True, cb.halt_level)
                    logger.warning("Market halt detected", level=cb.halt_level)
                
                elif not is_halted and was_halted:
                    # Market resumed
                    log_circuit_breaker(False)
                    await telegram.send_circuit_breaker_alert(False)
                    logger.info("Market resumed")
                
                was_halted = is_halted
            except Exception as e:
                logger.error(f"Circuit breaker error: {e}")
            
            await asyncio.sleep(60)
    
    async def _session_refresh_loop(self) -> None:
        """NSE session refresh loop - every 25 minutes."""
        session = get_nse_session()
        refresh_interval = self._settings.nse_session_refresh_minutes * 60
        
        while self._running:
            await asyncio.sleep(refresh_interval)
            
            try:
                await session.refresh_session()
                logger.debug("NSE session refreshed")
                get_event_log().log(EventType.NSE_SESSION_REFRESHED, {})
            except Exception as e:
                logger.error(f"NSE session refresh error: {e}")
                get_event_log().log(
                    EventType.NSE_SESSION_ERROR,
                    {"error": str(e)},
                )
    
    # === Scheduled Tasks ===
    
    async def _refresh_ban_list(self) -> None:
        """Refresh F&O ban list."""
        try:
            ban_list = get_ban_list_checker()
            await ban_list.refresh()
            
            banned = ban_list.get_banned_symbols()
            if banned:
                telegram = get_telegram_dispatcher()
                await telegram.send_ban_list_alert(banned)
            
            logger.info("Ban list refreshed", count=len(banned))
        except Exception as e:
            logger.error(f"Ban list refresh error: {e}")
    
    async def _refresh_instruments(self) -> None:
        """Refresh instrument master."""
        try:
            instruments = get_instrument_master()
            await instruments.refresh()
            logger.info("Instrument master refreshed")
        except Exception as e:
            logger.error(f"Instrument refresh error: {e}")
    
    async def _update_fii_dii(self) -> None:
        """Update FII/DII data."""
        try:
            from nse_advisor.signals.fii_dii import get_fii_dii_signal
            fii_dii = get_fii_dii_signal()
            await fii_dii.refresh()
            logger.info("FII/DII data updated")
        except Exception as e:
            logger.error(f"FII/DII update error: {e}")
    
    async def _run_nightly_postmortem(self) -> None:
        """Run nightly postmortem report."""
        try:
            tracker = get_position_tracker()
            postmortem = get_postmortem_engine()
            telegram = get_telegram_dispatcher()
            
            trades = tracker.get_all_trades()
            report = postmortem.nightly_report(trades, lookback_days=30)
            
            await telegram.send_daily_report(report)
            logger.info("Nightly postmortem completed")
        except Exception as e:
            logger.error(f"Nightly postmortem error: {e}")


async def main() -> None:
    """Main entry point."""
    advisor = NseAdvisor()
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        asyncio.create_task(advisor.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await advisor.initialize()
        await advisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        await advisor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
