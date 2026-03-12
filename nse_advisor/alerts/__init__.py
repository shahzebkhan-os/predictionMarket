"""
Alerts package initialization.
"""

from nse_advisor.alerts.telegram import (
    TelegramMessage,
    TelegramDispatcher,
    get_telegram_dispatcher,
    close_telegram_dispatcher,
)

__all__ = [
    "TelegramMessage",
    "TelegramDispatcher",
    "get_telegram_dispatcher",
    "close_telegram_dispatcher",
]
