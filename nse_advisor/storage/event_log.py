"""
Event Log.

Append-only event store for all system events.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, JSON, Text
from sqlalchemy.orm import Session

from zoneinfo import ZoneInfo

from nse_advisor.storage.models import Base

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Event types for logging."""
    # Signals
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SIGNAL_TIMEOUT = "SIGNAL_TIMEOUT"
    
    # Recommendations
    RECOMMENDATION_CREATED = "RECOMMENDATION_CREATED"
    
    # Trades
    TRADE_LOGGED = "TRADE_LOGGED"
    TRADE_CLOSED = "TRADE_CLOSED"
    PAPER_TRADE_AUTO_LOGGED = "PAPER_TRADE_AUTO_LOGGED"
    
    # Price/Data updates
    PRICE_UPDATE = "PRICE_UPDATE"
    OPTION_CHAIN_SNAPSHOT = "OPTION_CHAIN_SNAPSHOT"
    IV_RANK_UPDATE = "IV_RANK_UPDATE"
    GREEKS_UPDATE = "GREEKS_UPDATE"
    
    # Alerts
    EXIT_ALERT_SENT = "EXIT_ALERT_SENT"
    ALERT_SENT = "ALERT_SENT"
    
    # Postmortem
    POSTMORTEM_COMPLETE = "POSTMORTEM_COMPLETE"
    
    # Market state
    REGIME_CHANGE = "REGIME_CHANGE"
    EXPIRY_APPROACHING = "EXPIRY_APPROACHING"
    ROLLOVER_SUGGESTED = "ROLLOVER_SUGGESTED"
    
    # Events
    EVENT_BLACKOUT_START = "EVENT_BLACKOUT_START"
    EVENT_BLACKOUT_END = "EVENT_BLACKOUT_END"
    
    # Ban list
    BAN_LIST_UPDATED = "BAN_LIST_UPDATED"
    
    # Circuit breaker
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"
    CIRCUIT_BREAKER_CLEARED = "CIRCUIT_BREAKER_CLEARED"
    
    # Session
    NSE_SESSION_REFRESHED = "NSE_SESSION_REFRESHED"
    NSE_SESSION_ERROR = "NSE_SESSION_ERROR"
    
    # IndMoney
    INDMONEY_SYNC = "INDMONEY_SYNC"
    
    # Warnings
    DATA_STALE_WARNING = "DATA_STALE_WARNING"


class EventRecord(Base):
    """Persisted event log entry."""
    __tablename__ = "event_log"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_type = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    
    # Optional context
    underlying = Column(String(20), index=True)
    trade_id = Column(String(36), index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


@dataclass
class Event:
    """An event to log."""
    event_id: str
    event_type: EventType
    timestamp: datetime
    payload: dict[str, Any]
    underlying: str | None = None
    trade_id: str | None = None


class EventLog:
    """
    Append-only event log.
    
    All significant system events are logged here for:
    - Audit trail
    - Debugging
    - Backtesting replay
    """
    
    def __init__(self) -> None:
        """Initialize event log."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._in_memory_events: list[Event] = []
        self._max_memory_events = 10000
    
    def log(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        underlying: str | None = None,
        trade_id: str | None = None,
    ) -> Event:
        """
        Log an event.
        
        Args:
            event_type: Type of event
            payload: Event data
            underlying: Optional underlying symbol
            trade_id: Optional trade ID
            
        Returns:
            Created event
        """
        now = datetime.now(self._ist)
        
        event = Event(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=now,
            payload=payload,
            underlying=underlying,
            trade_id=trade_id,
        )
        
        # Add to in-memory buffer
        self._in_memory_events.append(event)
        
        # Trim if too large
        if len(self._in_memory_events) > self._max_memory_events:
            self._in_memory_events = self._in_memory_events[-self._max_memory_events:]
        
        logger.debug(f"Event logged: {event_type.value} - {payload.get('message', '')[:100]}")
        
        return event
    
    def get_recent(
        self,
        count: int = 100,
        event_type: EventType | None = None,
        underlying: str | None = None,
    ) -> list[Event]:
        """
        Get recent events.
        
        Args:
            count: Number of events to return
            event_type: Filter by event type
            underlying: Filter by underlying
            
        Returns:
            List of events (most recent first)
        """
        events = self._in_memory_events.copy()
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        if underlying:
            events = [e for e in events if e.underlying == underlying]
        
        return events[-count:][::-1]
    
    def get_events_since(
        self,
        since: datetime,
        event_type: EventType | None = None,
    ) -> list[Event]:
        """
        Get events since a timestamp.
        
        Args:
            since: Start timestamp
            event_type: Filter by event type
            
        Returns:
            List of events
        """
        events = [e for e in self._in_memory_events if e.timestamp >= since]
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        return events
    
    def clear(self) -> None:
        """Clear in-memory events."""
        self._in_memory_events.clear()


# Global instance
_event_log: EventLog | None = None


def get_event_log() -> EventLog:
    """Get or create global event log."""
    global _event_log
    if _event_log is None:
        _event_log = EventLog()
    return _event_log


# Convenience logging functions
def log_signal_generated(underlying: str, signal_name: str, score: float, reason: str) -> Event:
    """Log a signal generation."""
    return get_event_log().log(
        EventType.SIGNAL_GENERATED,
        {"signal_name": signal_name, "score": score, "reason": reason},
        underlying=underlying,
    )


def log_recommendation_created(recommendation_id: str, underlying: str, strategy: str) -> Event:
    """Log a recommendation creation."""
    return get_event_log().log(
        EventType.RECOMMENDATION_CREATED,
        {"recommendation_id": recommendation_id, "strategy": strategy},
        underlying=underlying,
    )


def log_trade_logged(trade_id: str, underlying: str, strategy: str, paper_mode: bool) -> Event:
    """Log a trade entry."""
    return get_event_log().log(
        EventType.TRADE_LOGGED,
        {"strategy": strategy, "paper_mode": paper_mode},
        underlying=underlying,
        trade_id=trade_id,
    )


def log_exit_alert(trade_id: str, alert_type: str, urgency: str, message: str) -> Event:
    """Log an exit alert."""
    return get_event_log().log(
        EventType.EXIT_ALERT_SENT,
        {"alert_type": alert_type, "urgency": urgency, "message": message},
        trade_id=trade_id,
    )


def log_regime_change(old_regime: str, new_regime: str, underlying: str) -> Event:
    """Log a regime change."""
    return get_event_log().log(
        EventType.REGIME_CHANGE,
        {"old_regime": old_regime, "new_regime": new_regime},
        underlying=underlying,
    )


def log_circuit_breaker(triggered: bool, level: str | None = None) -> Event:
    """Log circuit breaker event."""
    event_type = EventType.CIRCUIT_BREAKER_TRIGGERED if triggered else EventType.CIRCUIT_BREAKER_CLEARED
    return get_event_log().log(
        event_type,
        {"level": level} if level else {},
    )
