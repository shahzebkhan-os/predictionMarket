"""
Telegram Alerts.

Sends alerts via Telegram Bot API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import aiohttp
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.recommender.engine import TradeRecommendation
from nse_advisor.tracker.exit_advisor import ExitAlert
from nse_advisor.postmortem.engine import NightlyReport
from nse_advisor.recommender.rollover import RolloverSuggestion

logger = logging.getLogger(__name__)


@dataclass
class TelegramMessage:
    """A Telegram message to send."""
    chat_id: str
    text: str
    parse_mode: str = "HTML"
    disable_notification: bool = False


class TelegramDispatcher:
    """
    Telegram alert dispatcher.
    
    Alert types:
    - 🎯 NEW SIGNAL: Strategy/score/legs/max loss
    - 🔴 EXIT NOW: Urgent exit alert
    - 🟡 EXIT CONSIDER: 75% target reached
    - ⚠️ RISK ALERT: Circuit breaker/VIX/blackout/ban
    - 📊 DAILY REPORT: End of day summary
    - 📋 ROLLOVER SUGGESTION: Near-expiry rollover option
    """
    
    BASE_URL = "https://api.telegram.org/bot{token}/{method}"
    
    def __init__(self) -> None:
        """Initialize Telegram dispatcher."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        self._session: aiohttp.ClientSession | None = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def send_message(self, message: TelegramMessage) -> bool:
        """
        Send a Telegram message.
        
        Args:
            message: Message to send
            
        Returns:
            True if sent successfully
        """
        if not self._settings.telegram_bot_token or not self._settings.telegram_chat_id:
            logger.warning("Telegram not configured, skipping message")
            return False
        
        url = self.BASE_URL.format(
            token=self._settings.telegram_bot_token,
            method="sendMessage"
        )
        
        payload = {
            "chat_id": message.chat_id or self._settings.telegram_chat_id,
            "text": message.text,
            "parse_mode": message.parse_mode,
            "disable_notification": message.disable_notification,
        }
        
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.debug("Telegram message sent successfully")
                    return True
                else:
                    error = await resp.text()
                    logger.error(f"Telegram send failed: {resp.status} - {error}")
                    return False
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False
    
    def _format_timestamp(self) -> str:
        """Format current timestamp for messages."""
        now = datetime.now(self._ist)
        return now.strftime("%H:%M IST")
    
    async def send_new_signal(self, rec: TradeRecommendation) -> bool:
        """
        Send new signal alert.
        
        Args:
            rec: Trade recommendation
            
        Returns:
            True if sent
        """
        legs_text = "\n".join(
            f"  • {leg.action} {leg.tradingsymbol} @₹{leg.suggested_entry_price:.2f}"
            for leg in rec.legs
        )
        
        text = f"""🎯 <b>NEW SIGNAL</b>

<b>Underlying:</b> {rec.underlying}
<b>Strategy:</b> {rec.strategy_name}
<b>Regime:</b> {rec.regime}
<b>Score:</b> {rec.composite_score:.2f} ({rec.confidence:.0%} confidence)
<b>Direction:</b> {rec.direction}
<b>Urgency:</b> {rec.urgency}

<b>Legs:</b>
{legs_text}

<b>P&L:</b>
  • Max Profit: ₹{rec.max_profit_inr:,.0f}
  • Max Loss: ₹{rec.max_loss_inr:,.0f}
  • Stop Loss: ₹{rec.suggested_stop_loss_inr:,.0f}
  • Target: ₹{rec.suggested_take_profit_inr:,.0f}

<b>Reasoning:</b> {rec.reasoning}

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
        ))
    
    async def send_exit_alert(self, alert: ExitAlert) -> bool:
        """
        Send exit alert.
        
        Args:
            alert: Exit alert
            
        Returns:
            True if sent
        """
        # Choose emoji based on urgency
        if alert.urgency == "CRITICAL":
            emoji = "🔴"
        elif alert.urgency == "WARNING":
            emoji = "🟡"
        else:
            emoji = "ℹ️"
        
        text = f"""{emoji} <b>{alert.alert_type}</b>

<b>Trade:</b> {alert.strategy_name} on {alert.underlying}
<b>Current P&L:</b> ₹{alert.current_pnl:,.0f}

<b>Action:</b> {alert.message}

<b>Reason:</b> {alert.reason}

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
            disable_notification=alert.urgency == "INFO",
        ))
    
    async def send_risk_alert(
        self,
        alert_type: str,
        message: str,
        details: str = ""
    ) -> bool:
        """
        Send risk alert.
        
        Args:
            alert_type: Type of risk alert
            message: Main message
            details: Additional details
            
        Returns:
            True if sent
        """
        text = f"""⚠️ <b>RISK ALERT: {alert_type}</b>

{message}

{details}

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
        ))
    
    async def send_daily_report(self, report: NightlyReport) -> bool:
        """
        Send daily report.
        
        Args:
            report: Nightly report
            
        Returns:
            True if sent
        """
        # Strategy performance
        strategy_text = "\n".join(
            f"  • {name}: {stats['win_rate']:.0%} win rate, ₹{stats['total_pnl']:,.0f}"
            for name, stats in report.strategy_stats.items()
        ) or "  No trades"
        
        text = f"""📊 <b>DAILY REPORT</b>

<b>Period:</b> Last {report.lookback_days} days

<b>Trades:</b>
  • Total: {report.total_trades}
  • Winners: {report.winning_trades}
  • Losers: {report.losing_trades}
  • Win Rate: {report.win_rate:.0%}

<b>P&L:</b>
  • Total: ₹{report.total_pnl:,.0f}
  • Average: ₹{report.avg_pnl_per_trade:,.0f}
  • Best: ₹{report.best_trade_pnl:,.0f}
  • Worst: ₹{report.worst_trade_pnl:,.0f}

<b>By Strategy:</b>
{strategy_text}

<b>Paper vs Actual:</b>
  • Paper: ₹{report.paper_pnl:,.0f}
  • Actual: ₹{report.actual_pnl:,.0f}

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
        ))
    
    async def send_rollover_suggestion(
        self,
        rollover: RolloverSuggestion,
        underlying: str
    ) -> bool:
        """
        Send rollover suggestion.
        
        Args:
            rollover: Rollover suggestion
            underlying: Underlying symbol
            
        Returns:
            True if sent
        """
        legs_text = "\n".join(
            f"  • {leg.old_tradingsymbol} → {leg.new_tradingsymbol} (cost: ₹{leg.roll_cost:,.0f})"
            for leg in rollover.legs
        )
        
        text = f"""📋 <b>ROLLOVER SUGGESTION</b>

<b>Underlying:</b> {underlying}
<b>Current Expiry:</b> {rollover.current_expiry}
<b>New Expiry:</b> {rollover.new_expiry}

<b>Legs:</b>
{legs_text}

<b>Total Roll Cost:</b> ₹{rollover.total_roll_cost:,.0f}
<b>Current P&L:</b> ₹{rollover.current_position_pnl:,.0f}

<b>Reasoning:</b> {rollover.reasoning}

<i>See dashboard for full details</i>

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
        ))
    
    async def send_ban_list_alert(self, banned_symbols: list[str]) -> bool:
        """
        Send ban list update alert.
        
        Args:
            banned_symbols: List of banned symbols
            
        Returns:
            True if sent
        """
        symbols_text = ", ".join(banned_symbols) if banned_symbols else "None"
        
        text = f"""⚠️ <b>F&O BAN LIST UPDATED</b>

<b>Banned Symbols:</b>
{symbols_text}

<i>New recommendations blocked for these symbols</i>

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
        ))
    
    async def send_circuit_breaker_alert(
        self,
        triggered: bool,
        level: str | None = None
    ) -> bool:
        """
        Send circuit breaker alert.
        
        Args:
            triggered: True if halt triggered, False if resumed
            level: Halt level (if triggered)
            
        Returns:
            True if sent
        """
        if triggered:
            text = f"""⚠️ <b>MARKET HALT DETECTED</b>

<b>Level:</b> {level or 'Unknown'}

All signals paused. Waiting for market to resume.

⏰ {self._format_timestamp()}"""
        else:
            text = f"""✅ <b>MARKET RESUMED</b>

Market trading has resumed. Signals active.

⏰ {self._format_timestamp()}"""
        
        return await self.send_message(TelegramMessage(
            chat_id=self._settings.telegram_chat_id,
            text=text,
        ))


# Global instance
_telegram_dispatcher: TelegramDispatcher | None = None


def get_telegram_dispatcher() -> TelegramDispatcher:
    """Get or create global Telegram dispatcher."""
    global _telegram_dispatcher
    if _telegram_dispatcher is None:
        _telegram_dispatcher = TelegramDispatcher()
    return _telegram_dispatcher


async def close_telegram_dispatcher() -> None:
    """Close Telegram dispatcher."""
    global _telegram_dispatcher
    if _telegram_dispatcher is not None:
        await _telegram_dispatcher.close()
        _telegram_dispatcher = None
