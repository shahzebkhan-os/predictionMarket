"""Append-only event log.

Stores all system events for audit and debugging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import json

import pytz
import structlog

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class EventType(str, Enum):
    """Event type classification."""

    # Trade events
    TRADE_ENTRY = "TRADE_ENTRY"
    TRADE_EXIT = "TRADE_EXIT"
    TRADE_ADJUSTMENT = "TRADE_ADJUSTMENT"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"

    # Signal events
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    REGIME_CHANGE = "REGIME_CHANGE"
    ALERT_TRIGGERED = "ALERT_TRIGGERED"

    # Risk events
    RISK_LIMIT_HIT = "RISK_LIMIT_HIT"
    KILL_SWITCH = "KILL_SWITCH"
    MARGIN_WARNING = "MARGIN_WARNING"

    # System events
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    ERROR = "ERROR"

    # Market events
    MARKET_OPEN = "MARKET_OPEN"
    MARKET_CLOSE = "MARKET_CLOSE"
    EXPIRY_DAY = "EXPIRY_DAY"


@dataclass
class Event:
    """System event."""

    event_type: EventType
    timestamp: datetime
    data: dict[str, Any]

    # Context
    trade_id: str | None = None
    underlying: str | None = None
    spot_price: float | None = None

    # Metadata
    source: str = ""
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Event dict
        """
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "trade_id": self.trade_id,
            "underlying": self.underlying,
            "spot_price": self.spot_price,
            "source": self.source,
            "correlation_id": self.correlation_id,
        }

    def to_json(self) -> str:
        """Convert to JSON string.

        Returns:
            JSON string
        """
        return json.dumps(self.to_dict(), default=str)


class EventLog:
    """Append-only event log.

    Stores events for audit trail and debugging.
    Supports in-memory and file-based storage.
    """

    def __init__(
        self,
        max_memory_events: int = 10000,
        log_file: str | None = None,
    ) -> None:
        """Initialize event log.

        Args:
            max_memory_events: Max events to keep in memory
            log_file: Optional file path for persistent storage
        """
        self._events: list[Event] = []
        self._max_events = max_memory_events
        self._log_file = log_file
        self._file_handle = None

        if log_file:
            self._file_handle = open(log_file, "a")

    def append(
        self,
        event_type: EventType,
        data: dict[str, Any],
        trade_id: str | None = None,
        underlying: str | None = None,
        spot_price: float | None = None,
        source: str = "",
        correlation_id: str | None = None,
    ) -> Event:
        """Append event to log.

        Args:
            event_type: Event type
            data: Event data
            trade_id: Related trade ID
            underlying: Related underlying
            spot_price: Spot price at event time
            source: Event source
            correlation_id: Correlation ID for related events

        Returns:
            Created event
        """
        event = Event(
            event_type=event_type,
            timestamp=datetime.now(IST),
            data=data,
            trade_id=trade_id,
            underlying=underlying,
            spot_price=spot_price,
            source=source,
            correlation_id=correlation_id,
        )

        # Add to memory
        self._events.append(event)

        # Trim if needed
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        # Write to file
        if self._file_handle:
            self._file_handle.write(event.to_json() + "\n")
            self._file_handle.flush()

        logger.debug(
            "event_logged",
            event_type=event_type.value,
            trade_id=trade_id,
        )

        return event

    def log_trade_entry(
        self,
        trade_id: str,
        strategy: str,
        underlying: str,
        legs: list[dict[str, Any]],
        signals: dict[str, float],
        spot_price: float,
    ) -> Event:
        """Log trade entry event.

        Args:
            trade_id: Trade ID
            strategy: Strategy type
            underlying: Underlying symbol
            legs: Trade legs
            signals: Entry signals
            spot_price: Spot price

        Returns:
            Event
        """
        return self.append(
            event_type=EventType.TRADE_ENTRY,
            data={
                "strategy": strategy,
                "legs": legs,
                "signals": signals,
            },
            trade_id=trade_id,
            underlying=underlying,
            spot_price=spot_price,
            source="executor",
        )

    def log_trade_exit(
        self,
        trade_id: str,
        underlying: str,
        exit_reason: str,
        pnl: float,
        spot_price: float,
    ) -> Event:
        """Log trade exit event.

        Args:
            trade_id: Trade ID
            underlying: Underlying symbol
            exit_reason: Exit reason
            pnl: Trade P&L
            spot_price: Spot price

        Returns:
            Event
        """
        return self.append(
            event_type=EventType.TRADE_EXIT,
            data={
                "exit_reason": exit_reason,
                "pnl": pnl,
            },
            trade_id=trade_id,
            underlying=underlying,
            spot_price=spot_price,
            source="watcher",
        )

    def log_order(
        self,
        event_type: EventType,
        order_id: str,
        tradingsymbol: str,
        data: dict[str, Any],
    ) -> Event:
        """Log order event.

        Args:
            event_type: Order event type
            order_id: Order ID
            tradingsymbol: Trading symbol
            data: Order data

        Returns:
            Event
        """
        return self.append(
            event_type=event_type,
            data={"order_id": order_id, **data},
            source="order_manager",
        )

    def log_signal(
        self,
        signal_type: str,
        score: float,
        confidence: float,
        reason: str,
        underlying: str,
        spot_price: float,
    ) -> Event:
        """Log signal event.

        Args:
            signal_type: Signal type
            score: Signal score
            confidence: Confidence
            reason: Signal reason
            underlying: Underlying
            spot_price: Spot price

        Returns:
            Event
        """
        return self.append(
            event_type=EventType.SIGNAL_GENERATED,
            data={
                "signal_type": signal_type,
                "score": score,
                "confidence": confidence,
                "reason": reason,
            },
            underlying=underlying,
            spot_price=spot_price,
            source="signal_engine",
        )

    def log_regime_change(
        self,
        old_regime: str,
        new_regime: str,
        underlying: str,
        spot_price: float,
    ) -> Event:
        """Log regime change event.

        Args:
            old_regime: Previous regime
            new_regime: New regime
            underlying: Underlying
            spot_price: Spot price

        Returns:
            Event
        """
        return self.append(
            event_type=EventType.REGIME_CHANGE,
            data={
                "old_regime": old_regime,
                "new_regime": new_regime,
            },
            underlying=underlying,
            spot_price=spot_price,
            source="regime_detector",
        )

    def log_risk_event(
        self,
        event_type: EventType,
        message: str,
        details: dict[str, Any],
    ) -> Event:
        """Log risk event.

        Args:
            event_type: Risk event type
            message: Event message
            details: Event details

        Returns:
            Event
        """
        return self.append(
            event_type=event_type,
            data={"message": message, **details},
            source="risk_manager",
        )

    def log_error(
        self,
        error: str,
        context: dict[str, Any],
        source: str = "",
    ) -> Event:
        """Log error event.

        Args:
            error: Error message
            context: Error context
            source: Error source

        Returns:
            Event
        """
        return self.append(
            event_type=EventType.ERROR,
            data={"error": error, "context": context},
            source=source,
        )

    def get_events(
        self,
        event_type: EventType | None = None,
        trade_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Get events with filters.

        Args:
            event_type: Filter by event type
            trade_id: Filter by trade ID
            since: Filter by timestamp
            limit: Max events to return

        Returns:
            List of events
        """
        events = self._events

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        if trade_id:
            events = [e for e in events if e.trade_id == trade_id]

        if since:
            events = [e for e in events if e.timestamp >= since]

        return events[-limit:]

    def get_trade_timeline(self, trade_id: str) -> list[Event]:
        """Get complete timeline for a trade.

        Args:
            trade_id: Trade ID

        Returns:
            List of events in chronological order
        """
        return sorted(
            [e for e in self._events if e.trade_id == trade_id],
            key=lambda e: e.timestamp,
        )

    def close(self) -> None:
        """Close file handle."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
