"""
Exit Advisor.

Generates exit alerts based on P&L and market conditions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, date
from typing import Literal

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.market.nse_calendar import get_nse_calendar
from nse_advisor.market.regime import MarketRegime, RegimeClassification
from nse_advisor.tracker.state import ManualTrade

logger = logging.getLogger(__name__)


@dataclass
class ExitAlert:
    """An exit alert for a position."""
    trade_id: str
    underlying: str
    strategy_name: str
    alert_type: str
    urgency: Literal["INFO", "WARNING", "CRITICAL"]
    message: str
    current_pnl: float
    reason: str
    timestamp: datetime


class ExitAdvisor:
    """
    Generates exit alerts for tracked positions.
    
    Alert priority order:
    1. Stop loss hit → CRITICAL
    2. Take profit hit → WARNING
    3. 75% of max profit → INFO
    4. Vega sign flip → WARNING
    5. Regime change (short vega in high vol) → WARNING
    6. Expiry day after 14:30 → CRITICAL
    7. DTE=1 and OTM < ₹5 → INFO
    8. High event within 2h → WARNING
    9. DTE≤1 and profitable → INFO (rollover suggestion)
    """
    
    def __init__(self) -> None:
        """Initialize exit advisor."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        self._calendar = get_nse_calendar()
        
        # Track previous vega sign per trade
        self._prev_vega_sign: dict[str, int] = {}
    
    def check_all_conditions(
        self,
        trade: ManualTrade,
        regime: RegimeClassification | None = None,
    ) -> list[ExitAlert]:
        """
        Check all exit conditions for a trade.
        
        Args:
            trade: Trade to check
            regime: Current market regime
            
        Returns:
            List of exit alerts (empty if no alerts)
        """
        alerts = []
        now = datetime.now(self._ist)
        
        if not trade.is_open:
            return alerts
        
        pnl = trade.unrealized_pnl
        dte = trade.dte
        
        # 1. Stop loss
        if trade.stop_loss_inr > 0 and pnl <= -trade.stop_loss_inr:
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="STOP_LOSS",
                urgency="CRITICAL",
                message=f"🔴 STOP LOSS HIT — Exit now: {trade.strategy_name}",
                current_pnl=pnl,
                reason=f"P&L ₹{pnl:.0f} hit stop loss ₹{-trade.stop_loss_inr:.0f}",
                timestamp=now,
            ))
        
        # 2. Take profit
        if trade.take_profit_inr > 0 and pnl >= trade.take_profit_inr:
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="TAKE_PROFIT",
                urgency="WARNING",
                message=f"🟢 TARGET HIT — Consider exiting: {trade.strategy_name}",
                current_pnl=pnl,
                reason=f"P&L ₹{pnl:.0f} reached target ₹{trade.take_profit_inr:.0f}",
                timestamp=now,
            ))
        
        # 3. 75% of max profit
        elif trade.max_profit > 0 and pnl >= trade.max_profit * 0.75:
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="PARTIAL_TARGET",
                urgency="INFO",
                message=f"🟡 75% of max profit — Suggest exit: {trade.strategy_name}",
                current_pnl=pnl,
                reason=f"P&L ₹{pnl:.0f} is 75% of max profit ₹{trade.max_profit:.0f}",
                timestamp=now,
            ))
        
        # 4. Vega sign flip
        current_vega_sign = 1 if trade.net_vega >= 0 else -1
        prev_vega_sign = self._prev_vega_sign.get(trade.trade_id)
        
        if prev_vega_sign is not None and prev_vega_sign != current_vega_sign:
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="VEGA_FLIP",
                urgency="WARNING",
                message=f"⚠️ Vega flipped — Position character changed: {trade.strategy_name}",
                current_pnl=pnl,
                reason=f"Net vega changed from {'positive' if prev_vega_sign > 0 else 'negative'} to {'positive' if current_vega_sign > 0 else 'negative'}",
                timestamp=now,
            ))
        
        self._prev_vega_sign[trade.trade_id] = current_vega_sign
        
        # 5. Regime change (short vega in high vol)
        if regime and regime.regime == MarketRegime.HIGH_VOLATILITY and trade.net_vega < 0:
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="REGIME_WARNING",
                urgency="WARNING",
                message=f"⚠️ Regime change — Reduce short vega: {trade.strategy_name}",
                current_pnl=pnl,
                reason=f"High volatility regime detected with short vega position ({trade.net_vega:.0f})",
                timestamp=now,
            ))
        
        # 6. Expiry day after 14:30
        if dte == 0:
            current_time = now.time()
            if current_time >= time(14, 30):
                alerts.append(ExitAlert(
                    trade_id=trade.trade_id,
                    underlying=trade.underlying,
                    strategy_name=trade.strategy_name,
                    alert_type="EXPIRY_URGENT",
                    urgency="CRITICAL",
                    message=f"🔴 EXPIRY TODAY — Exit all legs before 15:20: {trade.strategy_name}",
                    current_pnl=pnl,
                    reason="Position open on expiry day after 14:30",
                    timestamp=now,
                ))
        
        # 7. DTE=1 and OTM near worthless
        if dte == 1:
            for leg in trade.legs:
                if leg.action == "BUY" and leg.current_price < 5:
                    alerts.append(ExitAlert(
                        trade_id=trade.trade_id,
                        underlying=trade.underlying,
                        strategy_name=trade.strategy_name,
                        alert_type="WORTHLESS_OPTION",
                        urgency="INFO",
                        message=f"🟡 Near worthless — Consider closing: {leg.tradingsymbol}",
                        current_pnl=pnl,
                        reason=f"{leg.tradingsymbol} LTP ₹{leg.current_price:.2f} with DTE=1",
                        timestamp=now,
                    ))
                    break  # One alert per trade
        
        # 8. High event within 2h
        if self._calendar.is_event_blackout(2):
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="EVENT_RISK",
                urgency="WARNING",
                message=f"⚠️ Event risk — Tighten stops or exit: {trade.strategy_name}",
                current_pnl=pnl,
                reason="High-impact event within 2 hours",
                timestamp=now,
            ))
        
        # 9. DTE≤1 and profitable → rollover suggestion
        if dte <= 1 and pnl > 0:
            alerts.append(ExitAlert(
                trade_id=trade.trade_id,
                underlying=trade.underlying,
                strategy_name=trade.strategy_name,
                alert_type="ROLLOVER_SUGGEST",
                urgency="INFO",
                message=f"📋 Rollover available — See dashboard: {trade.strategy_name}",
                current_pnl=pnl,
                reason=f"DTE={dte} with profit ₹{pnl:.0f}, consider rolling to next expiry",
                timestamp=now,
            ))
        
        return alerts
    
    def get_priority_alerts(
        self,
        trades: list[ManualTrade],
        regime: RegimeClassification | None = None,
    ) -> list[ExitAlert]:
        """
        Get all alerts across trades, sorted by urgency.
        
        Args:
            trades: List of trades to check
            regime: Current market regime
            
        Returns:
            List of alerts sorted by urgency (CRITICAL first)
        """
        all_alerts = []
        
        for trade in trades:
            alerts = self.check_all_conditions(trade, regime)
            all_alerts.extend(alerts)
        
        # Sort by urgency
        urgency_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        all_alerts.sort(key=lambda a: urgency_order.get(a.urgency, 3))
        
        return all_alerts


# Global instance
_exit_advisor: ExitAdvisor | None = None


def get_exit_advisor() -> ExitAdvisor:
    """Get or create global exit advisor."""
    global _exit_advisor
    if _exit_advisor is None:
        _exit_advisor = ExitAdvisor()
    return _exit_advisor
