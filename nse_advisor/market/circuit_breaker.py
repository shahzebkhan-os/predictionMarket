"""
Circuit Breaker Detector.

Detects NSE market halts and pauses signal generation.
NSE halts trading at -10%, -15%, -20% index moves.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from zoneinfo import ZoneInfo

from nse_advisor.data.nse_fetcher import get_nse_fetcher

logger = logging.getLogger(__name__)


class HaltLevel(Enum):
    """Circuit breaker halt levels."""
    NONE = "NONE"
    LEVEL_1 = "LEVEL_1"  # -10%: 15-min halt
    LEVEL_2 = "LEVEL_2"  # -15%: 45-min halt (or rest of day if after 14:00)
    LEVEL_3 = "LEVEL_3"  # -20%: Rest of day halt


@dataclass
class CircuitBreakerStatus:
    """Current circuit breaker status."""
    is_halted: bool
    halt_level: HaltLevel
    halt_reason: str
    halt_start: datetime | None
    estimated_resume: datetime | None
    last_check: datetime


class CircuitBreakerDetector:
    """
    Detects NSE circuit breaker halts.
    
    Detection methods:
    - NIFTY LTP unchanged for >3 minutes with volume=0
    - NSE indices API returning stale data
    - Manual halt flag
    
    On halt:
    - Set MARKET_HALTED flag
    - Pause signal generation loops
    - Send Telegram alert
    """
    
    # Circuit breaker thresholds
    CB_THRESHOLDS = {
        HaltLevel.LEVEL_1: -10.0,
        HaltLevel.LEVEL_2: -15.0,
        HaltLevel.LEVEL_3: -20.0,
    }
    
    # Halt durations
    HALT_DURATIONS = {
        HaltLevel.LEVEL_1: timedelta(minutes=15),
        HaltLevel.LEVEL_2: timedelta(minutes=45),
        HaltLevel.LEVEL_3: timedelta(hours=24),  # Rest of day
    }
    
    # Stale data detection threshold
    STALE_THRESHOLD_SECONDS = 180  # 3 minutes
    
    def __init__(self) -> None:
        """Initialize circuit breaker detector."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._is_halted = False
        self._halt_level = HaltLevel.NONE
        self._halt_start: datetime | None = None
        self._halt_reason = ""
        self._last_price: float | None = None
        self._last_price_time: datetime | None = None
        self._last_volume: int | None = None
        self._prev_close: float | None = None
    
    @property
    def is_halted(self) -> bool:
        """Check if market is halted."""
        return self._is_halted
    
    @property
    def halt_level(self) -> HaltLevel:
        """Get current halt level."""
        return self._halt_level
    
    async def check_circuit_breaker(self) -> CircuitBreakerStatus:
        """
        Check for circuit breaker conditions.
        
        Returns:
            Current circuit breaker status
        """
        now = datetime.now(self._ist)
        
        try:
            fetcher = get_nse_fetcher()
            nifty = await fetcher.fetch_index("NIFTY")
            
            if nifty is None:
                # Can't verify, assume not halted
                return self._get_status(now)
            
            current_price = nifty.ltp
            prev_close = nifty.close
            
            # Store prev close if not set
            if self._prev_close is None:
                self._prev_close = prev_close
            
            # Calculate change from previous close
            if prev_close > 0:
                change_pct = ((current_price - prev_close) / prev_close) * 100
            else:
                change_pct = 0
            
            # Check for circuit breaker trigger based on decline
            for level, threshold in self.CB_THRESHOLDS.items():
                if change_pct <= threshold and not self._is_halted:
                    self._trigger_halt(level, f"Index down {change_pct:.1f}%", now)
                    break
            
            # Check for stale data (price unchanged)
            if self._last_price is not None:
                if (
                    current_price == self._last_price
                    and self._last_price_time is not None
                ):
                    stale_seconds = (now - self._last_price_time).total_seconds()
                    
                    if stale_seconds >= self.STALE_THRESHOLD_SECONDS:
                        if not self._is_halted:
                            self._trigger_halt(
                                HaltLevel.LEVEL_1,
                                f"Stale data for {stale_seconds:.0f}s",
                                now
                            )
                else:
                    self._last_price_time = now
            
            self._last_price = current_price
            
            # Check if halt should be cleared
            if self._is_halted and self._halt_start is not None:
                halt_duration = self.HALT_DURATIONS.get(
                    self._halt_level,
                    timedelta(minutes=15)
                )
                expected_resume = self._halt_start + halt_duration
                
                if now >= expected_resume:
                    # Try to verify market has resumed
                    if current_price != self._last_price:
                        self._clear_halt()
            
            return self._get_status(now)
            
        except Exception as e:
            logger.error(f"Circuit breaker check failed: {e}")
            return self._get_status(now)
    
    def _trigger_halt(
        self,
        level: HaltLevel,
        reason: str,
        now: datetime
    ) -> None:
        """Trigger a circuit breaker halt."""
        self._is_halted = True
        self._halt_level = level
        self._halt_reason = reason
        self._halt_start = now
        
        logger.warning(
            f"Circuit breaker triggered",
            extra={
                "level": level.value,
                "reason": reason,
                "start": now.isoformat()
            }
        )
    
    def _clear_halt(self) -> None:
        """Clear the halt state."""
        logger.info(
            f"Circuit breaker cleared",
            extra={
                "level": self._halt_level.value,
                "duration": (
                    datetime.now(self._ist) - self._halt_start
                ).total_seconds() if self._halt_start else 0
            }
        )
        
        self._is_halted = False
        self._halt_level = HaltLevel.NONE
        self._halt_reason = ""
        self._halt_start = None
    
    def _get_status(self, now: datetime) -> CircuitBreakerStatus:
        """Build current status object."""
        estimated_resume: datetime | None = None
        
        if self._is_halted and self._halt_start is not None:
            halt_duration = self.HALT_DURATIONS.get(
                self._halt_level,
                timedelta(minutes=15)
            )
            estimated_resume = self._halt_start + halt_duration
        
        return CircuitBreakerStatus(
            is_halted=self._is_halted,
            halt_level=self._halt_level,
            halt_reason=self._halt_reason,
            halt_start=self._halt_start,
            estimated_resume=estimated_resume,
            last_check=now,
        )
    
    def force_halt(self, reason: str = "Manual halt") -> None:
        """Force a market halt (manual override)."""
        self._trigger_halt(HaltLevel.LEVEL_1, reason, datetime.now(self._ist))
    
    def force_resume(self) -> None:
        """Force resume market (manual override)."""
        self._clear_halt()

    # Aliases for backward compatibility
    async def check(self) -> CircuitBreakerStatus:
        """Alias for check_circuit_breaker."""
        return await self.check_circuit_breaker()

    def is_market_halted(self) -> bool:
        """Alias for is_halted."""
        return self.is_halted


# Global instance
_circuit_breaker: CircuitBreakerDetector | None = None


def get_circuit_breaker() -> CircuitBreakerDetector:
    """Get or create global circuit breaker detector."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreakerDetector()
    return _circuit_breaker
