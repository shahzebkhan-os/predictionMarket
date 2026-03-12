"""Report generation for postmortem analysis.

Generates daily, weekly, and custom reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Any

import pytz
import structlog

from nse_options_bot.postmortem.engine import PostmortemEngine, TradeAnalysis

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class ReportConfig:
    """Report configuration."""

    include_trade_details: bool = True
    include_signal_analysis: bool = True
    include_greek_attribution: bool = True
    include_execution_quality: bool = True
    max_trades_detail: int = 20  # Max trades to show in detail


class ReportGenerator:
    """Generates trading reports.

    Report types:
    - Daily summary
    - Weekly performance
    - Strategy breakdown
    - Signal accuracy
    """

    def __init__(
        self,
        postmortem: PostmortemEngine,
        config: ReportConfig | None = None,
    ) -> None:
        """Initialize generator.

        Args:
            postmortem: Postmortem engine
            config: Report configuration
        """
        self._postmortem = postmortem
        self._config = config or ReportConfig()

    def generate_daily_text_report(
        self,
        report_date: date | None = None,
    ) -> str:
        """Generate daily report as text.

        Args:
            report_date: Report date

        Returns:
            Report text
        """
        report_date = report_date or datetime.now(IST).date()
        daily = self._postmortem.generate_daily_report(report_date)

        if not daily:
            return f"📊 Daily Report - {report_date}\n\nNo trades recorded."

        lines = [
            f"📊 DAILY TRADING REPORT - {report_date}",
            "=" * 40,
            "",
            "📈 PERFORMANCE SUMMARY",
            f"  Total Trades: {daily.total_trades}",
            f"  Winners: {daily.winning_trades} | Losers: {daily.losing_trades}",
            f"  Win Rate: {daily.win_rate:.1f}%",
            "",
            "💰 P&L SUMMARY",
            f"  Gross P&L: ₹{float(daily.gross_pnl):,.0f}",
            f"  Commissions: ₹{float(daily.total_commissions):,.0f}",
            f"  Net P&L: ₹{float(daily.net_pnl):,.0f}",
            "",
        ]

        # Strategy breakdown
        if daily.strategy_pnl:
            lines.extend([
                "📋 BY STRATEGY",
            ])
            for strategy, pnl in daily.strategy_pnl.items():
                count = daily.strategy_count.get(strategy, 0)
                lines.append(f"  {strategy}: ₹{float(pnl):,.0f} ({count} trades)")
            lines.append("")

        # Greeks attribution
        if self._config.include_greek_attribution:
            lines.extend([
                "🔬 GREEKS ATTRIBUTION",
                f"  Delta P&L: ₹{float(daily.delta_pnl):,.0f}",
                f"  Theta P&L: ₹{float(daily.theta_pnl):,.0f}",
                f"  Vega P&L: ₹{float(daily.vega_pnl):,.0f}",
                "",
            ])

        # Summary
        emoji = "🟢" if daily.net_pnl >= 0 else "🔴"
        lines.extend([
            f"{emoji} NET RESULT: ₹{float(daily.net_pnl):,.0f}",
        ])

        return "\n".join(lines)

    def generate_weekly_text_report(
        self,
        end_date: date | None = None,
    ) -> str:
        """Generate weekly report as text.

        Args:
            end_date: Week ending date

        Returns:
            Report text
        """
        weekly = self._postmortem.generate_weekly_report(end_date)

        if weekly.get("trades", 0) == 0:
            return "📊 Weekly Report\n\nNo trades this week."

        lines = [
            f"📊 WEEKLY TRADING REPORT",
            f"Period: {weekly['week_start']} to {weekly['week_end']}",
            "=" * 45,
            "",
            "📈 PERFORMANCE",
            f"  Total Trades: {weekly['total_trades']}",
            f"  Win Rate: {weekly['win_rate']:.1f}%",
            f"  Total P&L: ₹{weekly['total_pnl']:,.0f}",
            f"  Avg P&L/Trade: ₹{weekly['avg_pnl_per_trade']:,.0f}",
            "",
            "🏆 BEST TRADE",
            f"  {weekly['best_trade']['strategy']}: ₹{weekly['best_trade']['pnl']:,.0f}",
            "",
            "📉 WORST TRADE",
            f"  {weekly['worst_trade']['strategy']}: ₹{weekly['worst_trade']['pnl']:,.0f}",
            "",
        ]

        # Strategy breakdown
        if weekly.get("strategy_pnl"):
            lines.append("📋 BY STRATEGY")
            for strategy, pnl in weekly["strategy_pnl"].items():
                emoji = "✅" if pnl >= 0 else "❌"
                lines.append(f"  {emoji} {strategy}: ₹{pnl:,.0f}")
            lines.append("")

        # Exit reasons
        if weekly.get("exit_reasons"):
            lines.append("🚪 EXIT REASONS")
            for reason, count in weekly["exit_reasons"].items():
                lines.append(f"  {reason}: {count}")
            lines.append("")

        return "\n".join(lines)

    def generate_signal_report(self) -> str:
        """Generate signal accuracy report.

        Returns:
            Report text
        """
        accuracy = self._postmortem.get_signal_accuracy()

        if not accuracy:
            return "📊 Signal Report\n\nInsufficient data."

        lines = [
            "📊 SIGNAL ACCURACY REPORT",
            "=" * 40,
            "",
            "🎯 SIGNAL PERFORMANCE (sorted by accuracy)",
            "",
        ]

        # Sort by accuracy
        sorted_signals = sorted(
            accuracy.items(),
            key=lambda x: x[1].get("accuracy", 0),
            reverse=True,
        )

        for signal_name, stats in sorted_signals:
            acc = stats.get("accuracy", 0)
            count = stats.get("count", 0)
            emoji = "🟢" if acc >= 60 else "🟡" if acc >= 50 else "🔴"

            lines.append(f"{emoji} {signal_name}")
            lines.append(f"   Accuracy: {acc:.1f}% ({count} samples)")
            lines.append(f"   P&L Contribution: ₹{stats.get('total_pnl', 0):,.0f}")
            lines.append("")

        return "\n".join(lines)

    def generate_strategy_drift_report(self) -> str:
        """Generate strategy drift report.

        Returns:
            Report text
        """
        drift = self._postmortem.get_strategy_drift()

        if drift.get("status") == "insufficient_data":
            return "📊 Drift Report\n\nInsufficient data for drift analysis."

        lines = [
            "📊 STRATEGY DRIFT ANALYSIS",
            "=" * 40,
            "",
        ]

        if drift.get("is_drifting"):
            lines.append("⚠️ DRIFT DETECTED")
        else:
            lines.append("✅ NO SIGNIFICANT DRIFT")

        lines.extend([
            "",
            "📈 WIN RATE COMPARISON",
            f"  Recent: {drift['recent_win_rate']:.1f}%",
            f"  Historical: {drift['historical_win_rate']:.1f}%",
            f"  Drift: {drift['win_rate_drift']:+.1f}%",
            "",
            "💰 AVG P&L COMPARISON",
            f"  Recent: ₹{drift['recent_avg_pnl']:,.0f}",
            f"  Historical: ₹{drift['historical_avg_pnl']:,.0f}",
            f"  Drift: ₹{drift['pnl_drift']:,.0f}",
            "",
            f"Sample sizes: {drift['recent_trades']} recent, {drift['historical_trades']} historical",
        ])

        return "\n".join(lines)

    def generate_json_report(
        self,
        report_type: str = "daily",
        report_date: date | None = None,
    ) -> dict[str, Any]:
        """Generate report as JSON.

        Args:
            report_type: "daily", "weekly", "performance"
            report_date: Report date

        Returns:
            Report dict
        """
        if report_type == "daily":
            daily = self._postmortem.generate_daily_report(report_date)
            if not daily:
                return {"error": "No data"}

            return {
                "type": "daily",
                "date": daily.date.isoformat(),
                "trades": daily.total_trades,
                "win_rate": daily.win_rate,
                "gross_pnl": float(daily.gross_pnl),
                "net_pnl": float(daily.net_pnl),
                "commissions": float(daily.total_commissions),
                "strategy_breakdown": {
                    k: float(v) for k, v in daily.strategy_pnl.items()
                },
                "greek_attribution": {
                    "delta": float(daily.delta_pnl),
                    "theta": float(daily.theta_pnl),
                    "vega": float(daily.vega_pnl),
                },
            }

        elif report_type == "weekly":
            return self._postmortem.generate_weekly_report(report_date)

        elif report_type == "performance":
            return self._postmortem.get_performance_summary()

        else:
            return {"error": f"Unknown report type: {report_type}"}

    def generate_telegram_summary(
        self,
        report_date: date | None = None,
    ) -> str:
        """Generate concise summary for Telegram.

        Args:
            report_date: Report date

        Returns:
            Short summary text
        """
        report_date = report_date or datetime.now(IST).date()
        daily = self._postmortem.generate_daily_report(report_date)

        if not daily:
            return f"📊 {report_date}: No trades"

        emoji = "🟢" if daily.net_pnl >= 0 else "🔴"

        return (
            f"{emoji} {report_date}\n"
            f"Trades: {daily.total_trades} | Win: {daily.win_rate:.0f}%\n"
            f"P&L: ₹{float(daily.net_pnl):,.0f}"
        )
