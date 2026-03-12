"""
Storage package initialization.
"""

from nse_advisor.storage.models import (
    Base,
    Signal,
    Recommendation,
    Trade,
    TradePostmortemRecord,
    OptionChainRecord,
    IVHistory,
    DailyReport,
)
from nse_advisor.storage.event_log import (
    EventType,
    Event,
    EventLog,
    get_event_log,
    log_signal_generated,
    log_recommendation_created,
    log_trade_logged,
    log_exit_alert,
    log_regime_change,
    log_circuit_breaker,
)
from nse_advisor.storage.db import (
    Database,
    get_database,
    init_database,
    close_database,
)

__all__ = [
    # Models
    "Base",
    "Signal",
    "Recommendation",
    "Trade",
    "TradePostmortemRecord",
    "OptionChainRecord",
    "IVHistory",
    "DailyReport",
    # Event Log
    "EventType",
    "Event",
    "EventLog",
    "get_event_log",
    "log_signal_generated",
    "log_recommendation_created",
    "log_trade_logged",
    "log_exit_alert",
    "log_regime_change",
    "log_circuit_breaker",
    # Database
    "Database",
    "get_database",
    "init_database",
    "close_database",
]
