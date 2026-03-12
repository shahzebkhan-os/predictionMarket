"""
NSE Session Health Monitor.

Monitors NSE session health and attempts automatic recovery.
Sends Telegram alerts on persistent failures.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from nse_advisor.data.nse_session import NseSession

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Test URL — lightweight, always available, returns small JSON
HEALTH_CHECK_URL = "https://www.nseindia.com/api/allIndices"


async def health_check_loop(
    session: "NseSession",
    interval_seconds: int = 300
) -> None:
    """
    Run every 5 minutes during market hours.
    Attempts a lightweight fetch to confirm session is alive.
    
    Args:
        session: NseSession instance to monitor
        interval_seconds: Check interval in seconds (default 5 minutes)
    """
    from nse_advisor.data.nse_session import NseIpBannedError, NseSessionError
    from nse_advisor.alerts.telegram import get_telegram_dispatcher
    from nse_advisor.market.circuit_breaker import get_circuit_breaker
    
    logger.info("Starting NSE session health check loop")
    telegram = get_telegram_dispatcher()
    circuit_breaker = get_circuit_breaker()
    
    while True:
        try:
            # Attempt lightweight fetch
            await session.fetch(HEALTH_CHECK_URL)
            logger.debug("NSE session health check passed")
            
        except NseIpBannedError:
            # IP banned - need manual intervention
            await telegram.send_risk_alert(
                "NSE_IP_BANNED",
                "NSE IP BANNED",
                (
                    "🚨 All data fetches will fail for 1-6 hours.\n"
                    "Options:\n"
                    "1. Wait for ban to lift\n"
                    "2. Restart from a different IP\n"
                    "3. Enable Playwright mode (slower)\n"
                    "Signals paused until resolved."
                )
            )
            # Set circuit breaker flag
            circuit_breaker.set_data_unavailable(reason="NSE_IP_BANNED")
            logger.error("NSE IP banned - stopping health check loop")
            break  # Stop loop — manual intervention needed
            
        except NseSessionError as e:
            # Session error - attempt recovery
            logger.warning(f"NSE session error: {e}")
            await telegram.send_risk_alert(
                "NSE_SESSION_ERROR",
                "NSE session error",
                f"Attempting recovery... Error: {e}"
            )
            
            try:
                await session.init()
                await telegram.send_risk_alert(
                    "NSE_SESSION_RECOVERED",
                    "NSE session recovered",
                    "Session successfully re-initialized"
                )
                logger.info("NSE session recovered after error")
            except Exception as re:
                await telegram.send_risk_alert(
                    "NSE_SESSION_RECOVERY_FAILED",
                    "NSE session recovery failed",
                    str(re)
                )
                logger.error(f"NSE session recovery failed: {re}")
                
        except Exception as e:
            logger.error(f"Unexpected error in health check: {e}")
        
        await asyncio.sleep(interval_seconds)


async def check_session_health(session: "NseSession") -> dict:
    """
    Single health check - returns status dict.
    
    Args:
        session: NseSession instance to check
        
    Returns:
        dict with health status
    """
    from nse_advisor.data.nse_session import NseIpBannedError, NseSessionError
    
    status = session.status()
    status["healthy"] = False
    status["error"] = None
    
    try:
        await session.fetch(HEALTH_CHECK_URL)
        status["healthy"] = True
    except NseIpBannedError as e:
        status["error"] = "IP_BANNED"
        status["error_detail"] = str(e)
    except NseSessionError as e:
        status["error"] = "SESSION_ERROR"
        status["error_detail"] = str(e)
    except Exception as e:
        status["error"] = "UNKNOWN"
        status["error_detail"] = str(e)
    
    return status


async def start_health_monitor(session: "NseSession") -> asyncio.Task:
    """
    Start health monitor as background task.
    
    Args:
        session: NseSession instance to monitor
        
    Returns:
        asyncio.Task running the health check loop
    """
    from nse_advisor.config import get_settings
    
    settings = get_settings()
    interval = settings.nse_health_check_interval_seconds
    
    task = asyncio.create_task(health_check_loop(session, interval))
    logger.info(f"Health monitor started with {interval}s interval")
    
    return task
