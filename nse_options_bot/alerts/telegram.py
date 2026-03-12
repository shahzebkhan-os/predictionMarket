"""Telegram alert dispatcher.

Sends trading alerts via Telegram Bot API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import aiohttp
import pytz
import structlog

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class AlertPriority(str, Enum):
    """Alert priority levels."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Alert message."""

    message: str
    priority: AlertPriority
    timestamp: datetime
    sent: bool = False
    error: str | None = None


class TelegramAlerter:
    """Telegram alert dispatcher.

    Sends alerts via Telegram Bot API.
    Supports priority-based filtering and rate limiting.
    """

    TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    # Rate limiting
    MAX_MESSAGES_PER_MINUTE = 20
    CRITICAL_BYPASS_RATE_LIMIT = True

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        min_priority: AlertPriority = AlertPriority.NORMAL,
    ) -> None:
        """Initialize alerter.

        Args:
            bot_token: Telegram bot token
            chat_id: Chat ID to send messages to
            enabled: Whether alerts are enabled
            min_priority: Minimum priority to send
        """
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled
        self._min_priority = min_priority

        self._message_history: list[datetime] = []
        self._pending_alerts: asyncio.Queue[Alert] = asyncio.Queue()
        self._alert_history: list[Alert] = []

    async def send_alert(
        self,
        message: str,
        priority: str | AlertPriority = AlertPriority.NORMAL,
        parse_mode: str = "HTML",
    ) -> bool:
        """Send an alert.

        Args:
            message: Alert message
            priority: Alert priority
            parse_mode: Telegram parse mode

        Returns:
            True if sent successfully
        """
        if isinstance(priority, str):
            priority = AlertPriority(priority)

        alert = Alert(
            message=message,
            priority=priority,
            timestamp=datetime.now(IST),
        )

        # Check if should send
        if not self._should_send(alert):
            logger.debug(
                "alert_filtered",
                priority=priority.value,
                min_priority=self._min_priority.value,
            )
            return False

        # Check rate limit
        if not self._check_rate_limit(alert):
            logger.warning("alert_rate_limited", priority=priority.value)
            await self._pending_alerts.put(alert)
            return False

        # Send
        success = await self._send_telegram(message, parse_mode)

        alert.sent = success
        self._alert_history.append(alert)
        self._message_history.append(datetime.now(IST))

        return success

    async def send_trade_entry(
        self,
        strategy: str,
        underlying: str,
        legs: list[dict[str, Any]],
        signals: dict[str, float],
    ) -> bool:
        """Send trade entry alert.

        Args:
            strategy: Strategy type
            underlying: Underlying symbol
            legs: Trade legs
            signals: Entry signals

        Returns:
            True if sent
        """
        # Format legs
        legs_text = "\n".join(
            f"  • {leg.get('symbol', 'N/A')}: {leg.get('qty', 0)} @ ₹{leg.get('price', 0):.2f}"
            for leg in legs
        )

        # Top signals
        top_signals = sorted(signals.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
        signals_text = ", ".join(f"{k}: {v:.2f}" for k, v in top_signals)

        message = (
            f"🔔 <b>TRADE ENTRY</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Strategy: {strategy}\n"
            f"Underlying: {underlying}\n\n"
            f"<b>Legs:</b>\n{legs_text}\n\n"
            f"<b>Signals:</b> {signals_text}"
        )

        return await self.send_alert(message, AlertPriority.HIGH)

    async def send_trade_exit(
        self,
        strategy: str,
        underlying: str,
        exit_reason: str,
        pnl: float,
        duration_minutes: float,
    ) -> bool:
        """Send trade exit alert.

        Args:
            strategy: Strategy type
            underlying: Underlying symbol
            exit_reason: Exit reason
            pnl: Trade P&L
            duration_minutes: Trade duration

        Returns:
            True if sent
        """
        emoji = "🟢" if pnl >= 0 else "🔴"
        pnl_text = f"₹{pnl:+,.0f}"

        message = (
            f"{emoji} <b>TRADE EXIT</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Strategy: {strategy}\n"
            f"Underlying: {underlying}\n"
            f"Reason: {exit_reason}\n\n"
            f"<b>P&L:</b> {pnl_text}\n"
            f"Duration: {duration_minutes:.0f} min"
        )

        return await self.send_alert(message, AlertPriority.HIGH)

    async def send_daily_summary(
        self,
        date_str: str,
        trades: int,
        win_rate: float,
        pnl: float,
    ) -> bool:
        """Send daily summary alert.

        Args:
            date_str: Date string
            trades: Number of trades
            win_rate: Win rate percentage
            pnl: Net P&L

        Returns:
            True if sent
        """
        emoji = "🟢" if pnl >= 0 else "🔴"

        message = (
            f"📊 <b>DAILY SUMMARY - {date_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Trades: {trades}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"{emoji} P&L: ₹{pnl:+,.0f}"
        )

        return await self.send_alert(message, AlertPriority.NORMAL)

    async def send_risk_alert(
        self,
        alert_type: str,
        message: str,
        current_value: float,
        threshold: float,
    ) -> bool:
        """Send risk alert.

        Args:
            alert_type: Type of risk alert
            message: Alert message
            current_value: Current value
            threshold: Threshold value

        Returns:
            True if sent
        """
        text = (
            f"⚠️ <b>RISK ALERT: {alert_type}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{message}\n\n"
            f"Current: {current_value:.2f}\n"
            f"Threshold: {threshold:.2f}"
        )

        return await self.send_alert(text, AlertPriority.CRITICAL)

    async def send_kill_switch_alert(self, reason: str) -> bool:
        """Send kill switch activation alert.

        Args:
            reason: Reason for activation

        Returns:
            True if sent
        """
        message = (
            f"🚨 <b>KILL SWITCH ACTIVATED</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Reason: {reason}\n\n"
            f"All trading has been halted.\n"
            f"Manual intervention required."
        )

        return await self.send_alert(message, AlertPriority.CRITICAL)

    async def send_system_status(
        self,
        status: str,
        details: dict[str, Any],
    ) -> bool:
        """Send system status alert.

        Args:
            status: System status
            details: Status details

        Returns:
            True if sent
        """
        details_text = "\n".join(f"  {k}: {v}" for k, v in details.items())

        message = (
            f"ℹ️ <b>SYSTEM STATUS: {status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{details_text}"
        )

        return await self.send_alert(message, AlertPriority.LOW)

    async def _send_telegram(
        self,
        message: str,
        parse_mode: str = "HTML",
    ) -> bool:
        """Send message via Telegram API.

        Args:
            message: Message text
            parse_mode: Parse mode

        Returns:
            True if successful
        """
        if not self._enabled:
            logger.debug("telegram_disabled", message=message[:50])
            return True

        if not self._bot_token or not self._chat_id:
            logger.warning("telegram_not_configured")
            return False

        url = self.TELEGRAM_API_URL.format(token=self._bot_token)

        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.debug("telegram_sent")
                        return True
                    else:
                        error = await response.text()
                        logger.error(
                            "telegram_error",
                            status=response.status,
                            error=error,
                        )
                        return False

        except Exception as e:
            logger.error("telegram_exception", error=str(e))
            return False

    def _should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent.

        Args:
            alert: Alert to check

        Returns:
            True if should send
        """
        if not self._enabled:
            return False

        # Priority check
        priority_order = [
            AlertPriority.LOW,
            AlertPriority.NORMAL,
            AlertPriority.HIGH,
            AlertPriority.CRITICAL,
        ]

        return priority_order.index(alert.priority) >= priority_order.index(
            self._min_priority
        )

    def _check_rate_limit(self, alert: Alert) -> bool:
        """Check rate limit.

        Args:
            alert: Alert to check

        Returns:
            True if within rate limit
        """
        # Critical alerts bypass rate limit
        if alert.priority == AlertPriority.CRITICAL and self.CRITICAL_BYPASS_RATE_LIMIT:
            return True

        # Clean old messages
        now = datetime.now(IST)
        one_minute_ago = now.replace(second=now.second - 60 if now.second >= 60 else 0)
        self._message_history = [
            t for t in self._message_history if t > one_minute_ago
        ]

        return len(self._message_history) < self.MAX_MESSAGES_PER_MINUTE

    async def process_pending(self) -> int:
        """Process pending alerts.

        Returns:
            Number of alerts processed
        """
        processed = 0

        while not self._pending_alerts.empty():
            try:
                alert = self._pending_alerts.get_nowait()

                if self._check_rate_limit(alert):
                    await self._send_telegram(alert.message)
                    processed += 1
                else:
                    # Put back if still rate limited
                    await self._pending_alerts.put(alert)
                    break

            except asyncio.QueueEmpty:
                break

        return processed

    def get_stats(self) -> dict[str, Any]:
        """Get alerter statistics.

        Returns:
            Stats dict
        """
        return {
            "enabled": self._enabled,
            "total_sent": len([a for a in self._alert_history if a.sent]),
            "total_failed": len([a for a in self._alert_history if not a.sent]),
            "pending": self._pending_alerts.qsize(),
            "messages_last_minute": len(self._message_history),
        }
