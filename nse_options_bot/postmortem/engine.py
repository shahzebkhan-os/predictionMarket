"""Postmortem engine - trade analysis and reporting.

Analyzes completed trades for P&L attribution and signal accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Any

import pytz
import structlog

from nse_options_bot.watcher.state import ExitReason, OptionsTradeState

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class TradeAnalysis:
    """Analysis of a completed trade."""

    trade_id: str
    strategy_type: str
    underlying: str

    # Entry/Exit
    entry_time: datetime
    exit_time: datetime
    duration_minutes: float

    # P&L
    gross_pnl: Decimal
    net_pnl: Decimal
    pnl_pct: float

    # Attribution
    delta_pnl: Decimal = Decimal("0")
    gamma_pnl: Decimal = Decimal("0")
    theta_pnl: Decimal = Decimal("0")
    vega_pnl: Decimal = Decimal("0")
    unexplained_pnl: Decimal = Decimal("0")

    # Execution
    slippage: Decimal = Decimal("0")
    commissions: Decimal = Decimal("0")

    # Signals
    entry_signals: dict[str, float] = field(default_factory=dict)
    exit_reason: str = ""

    # Market context
    entry_spot: Decimal = Decimal("0")
    exit_spot: Decimal = Decimal("0")
    spot_move_pct: float = 0.0
    entry_vix: float = 0.0
    exit_vix: float = 0.0
    entry_regime: str = ""
    exit_regime: str = ""


@dataclass
class DailyReport:
    """Daily trading report."""

    date: date
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    gross_pnl: Decimal
    net_pnl: Decimal
    max_drawdown: Decimal

    # By strategy
    strategy_pnl: dict[str, Decimal] = field(default_factory=dict)
    strategy_count: dict[str, int] = field(default_factory=dict)

    # By signal
    signal_contribution: dict[str, float] = field(default_factory=dict)

    # Greeks attribution
    delta_pnl: Decimal = Decimal("0")
    theta_pnl: Decimal = Decimal("0")
    vega_pnl: Decimal = Decimal("0")

    # Execution quality
    avg_slippage: Decimal = Decimal("0")
    total_commissions: Decimal = Decimal("0")


class PostmortemEngine:
    """Trade postmortem analysis engine.

    Provides:
    - Per-trade P&L attribution
    - Signal accuracy analysis
    - Strategy performance
    - Daily/weekly reports
    """

    # Commission rates
    BROKERAGE_PER_ORDER = Decimal("20")  # ₹20 flat
    STT_RATE = Decimal("0.000625")  # 0.0625% on sell side
    EXCHANGE_CHARGES = Decimal("0.00053")  # NSE charges

    def __init__(self) -> None:
        """Initialize engine."""
        self._trades: list[TradeAnalysis] = []
        self._daily_reports: dict[date, DailyReport] = {}

    def analyze_trade(
        self,
        trade: OptionsTradeState,
        entry_vix: float = 0.0,
        exit_vix: float = 0.0,
    ) -> TradeAnalysis:
        """Analyze a completed trade.

        Args:
            trade: Completed trade state
            entry_vix: VIX at entry
            exit_vix: VIX at exit

        Returns:
            TradeAnalysis
        """
        if not trade.exit_time or not trade.entry_time:
            raise ValueError("Trade must be completed")

        duration = (trade.exit_time - trade.entry_time).total_seconds() / 60

        # Calculate gross P&L
        gross_pnl = trade.total_pnl

        # Calculate commissions
        num_orders = len(trade.legs) * 2  # Entry + exit per leg
        commissions = self.BROKERAGE_PER_ORDER * Decimal(str(num_orders))

        # STT on sell side
        for leg in trade.legs:
            if not leg.is_long:  # Short leg = sell at entry, buy at exit
                sell_value = leg.entry_price * Decimal(str(leg.quantity))
                commissions += sell_value * self.STT_RATE
            else:  # Long leg = buy at entry, sell at exit
                if leg.exit_price:
                    sell_value = leg.exit_price * Decimal(str(leg.quantity))
                    commissions += sell_value * self.STT_RATE

        net_pnl = gross_pnl - commissions

        # P&L percentage
        pnl_pct = float(net_pnl / trade.capital_allocated * 100) if trade.capital_allocated > 0 else 0

        # Spot movement
        spot_move = trade.current_spot_price - trade.entry_spot_price
        spot_move_pct = float(spot_move / trade.entry_spot_price * 100) if trade.entry_spot_price > 0 else 0

        # Greeks attribution (simplified)
        delta_pnl = Decimal(str(trade.net_delta * float(spot_move)))
        theta_pnl = trade.actual_theta_today
        vega_pnl = Decimal("0")  # Need IV change data
        gamma_pnl = Decimal(str(0.5 * trade.net_gamma * float(spot_move) ** 2))

        unexplained = gross_pnl - delta_pnl - gamma_pnl - theta_pnl - vega_pnl

        analysis = TradeAnalysis(
            trade_id=trade.trade_id,
            strategy_type=trade.strategy_type,
            underlying=trade.underlying,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            duration_minutes=duration,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            pnl_pct=pnl_pct,
            delta_pnl=delta_pnl,
            gamma_pnl=gamma_pnl,
            theta_pnl=theta_pnl,
            vega_pnl=vega_pnl,
            unexplained_pnl=unexplained,
            commissions=commissions,
            entry_signals=trade.entry_signals,
            exit_reason=trade.exit_reason.value if trade.exit_reason else "",
            entry_spot=trade.entry_spot_price,
            exit_spot=trade.current_spot_price,
            spot_move_pct=spot_move_pct,
            entry_vix=entry_vix,
            exit_vix=exit_vix,
            entry_regime=trade.entry_regime,
        )

        self._trades.append(analysis)
        self._update_daily_report(analysis)

        logger.info(
            "trade_analyzed",
            trade_id=trade.trade_id,
            net_pnl=float(net_pnl),
            pnl_pct=pnl_pct,
            duration_min=duration,
        )

        return analysis

    def _update_daily_report(self, analysis: TradeAnalysis) -> None:
        """Update daily report with trade.

        Args:
            analysis: Trade analysis
        """
        trade_date = analysis.exit_time.date()

        if trade_date not in self._daily_reports:
            self._daily_reports[trade_date] = DailyReport(
                date=trade_date,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                gross_pnl=Decimal("0"),
                net_pnl=Decimal("0"),
                max_drawdown=Decimal("0"),
            )

        report = self._daily_reports[trade_date]

        report.total_trades += 1
        if analysis.net_pnl >= 0:
            report.winning_trades += 1
        else:
            report.losing_trades += 1

        report.win_rate = report.winning_trades / report.total_trades * 100

        report.gross_pnl += analysis.gross_pnl
        report.net_pnl += analysis.net_pnl
        report.total_commissions += analysis.commissions

        # Update strategy breakdown
        strategy = analysis.strategy_type
        report.strategy_pnl[strategy] = report.strategy_pnl.get(strategy, Decimal("0")) + analysis.net_pnl
        report.strategy_count[strategy] = report.strategy_count.get(strategy, 0) + 1

        # Update Greeks attribution
        report.delta_pnl += analysis.delta_pnl
        report.theta_pnl += analysis.theta_pnl
        report.vega_pnl += analysis.vega_pnl

    def generate_daily_report(self, report_date: date | None = None) -> DailyReport | None:
        """Generate daily report.

        Args:
            report_date: Report date (default: today)

        Returns:
            DailyReport or None
        """
        report_date = report_date or datetime.now(IST).date()
        return self._daily_reports.get(report_date)

    def generate_weekly_report(
        self,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """Generate weekly report.

        Args:
            end_date: Week ending date

        Returns:
            Weekly report dict
        """
        end_date = end_date or datetime.now(IST).date()
        start_date = end_date - timedelta(days=7)

        week_trades = [
            t for t in self._trades
            if start_date <= t.exit_time.date() <= end_date
        ]

        if not week_trades:
            return {"week_start": start_date.isoformat(), "week_end": end_date.isoformat(), "trades": 0}

        total_pnl = sum(t.net_pnl for t in week_trades)
        winning = [t for t in week_trades if t.net_pnl >= 0]
        losing = [t for t in week_trades if t.net_pnl < 0]

        # Best and worst trades
        best_trade = max(week_trades, key=lambda t: t.net_pnl)
        worst_trade = min(week_trades, key=lambda t: t.net_pnl)

        # Strategy breakdown
        strategy_pnl: dict[str, Decimal] = {}
        for t in week_trades:
            strategy_pnl[t.strategy_type] = strategy_pnl.get(t.strategy_type, Decimal("0")) + t.net_pnl

        # Exit reason breakdown
        exit_reasons: dict[str, int] = {}
        for t in week_trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        return {
            "week_start": start_date.isoformat(),
            "week_end": end_date.isoformat(),
            "total_trades": len(week_trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(week_trades) * 100,
            "total_pnl": float(total_pnl),
            "avg_pnl_per_trade": float(total_pnl / len(week_trades)),
            "best_trade": {
                "id": best_trade.trade_id,
                "pnl": float(best_trade.net_pnl),
                "strategy": best_trade.strategy_type,
            },
            "worst_trade": {
                "id": worst_trade.trade_id,
                "pnl": float(worst_trade.net_pnl),
                "strategy": worst_trade.strategy_type,
            },
            "strategy_pnl": {k: float(v) for k, v in strategy_pnl.items()},
            "exit_reasons": exit_reasons,
        }

    def get_signal_accuracy(
        self,
        lookback_trades: int = 100,
    ) -> dict[str, dict[str, Any]]:
        """Calculate signal accuracy.

        Args:
            lookback_trades: Number of trades to analyze

        Returns:
            Signal accuracy dict
        """
        recent_trades = self._trades[-lookback_trades:]

        if not recent_trades:
            return {}

        signal_stats: dict[str, dict[str, Any]] = {}

        for trade in recent_trades:
            is_winner = trade.net_pnl >= 0

            for signal_name, signal_value in trade.entry_signals.items():
                if signal_name not in signal_stats:
                    signal_stats[signal_name] = {
                        "count": 0,
                        "winners": 0,
                        "losers": 0,
                        "avg_value_winners": 0.0,
                        "avg_value_losers": 0.0,
                        "total_pnl": Decimal("0"),
                    }

                stats = signal_stats[signal_name]
                stats["count"] += 1

                if is_winner:
                    stats["winners"] += 1
                    stats["avg_value_winners"] += signal_value
                else:
                    stats["losers"] += 1
                    stats["avg_value_losers"] += signal_value

                stats["total_pnl"] += trade.net_pnl

        # Calculate averages
        for signal_name, stats in signal_stats.items():
            if stats["winners"] > 0:
                stats["avg_value_winners"] /= stats["winners"]
            if stats["losers"] > 0:
                stats["avg_value_losers"] /= stats["losers"]

            stats["accuracy"] = stats["winners"] / stats["count"] * 100 if stats["count"] > 0 else 0
            stats["total_pnl"] = float(stats["total_pnl"])

        return signal_stats

    def get_strategy_drift(
        self,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        """Detect strategy drift.

        Compares recent performance to historical baseline.

        Args:
            lookback_days: Days for comparison

        Returns:
            Drift analysis dict
        """
        cutoff = datetime.now(IST) - timedelta(days=lookback_days)

        recent = [t for t in self._trades if t.exit_time >= cutoff]
        historical = [t for t in self._trades if t.exit_time < cutoff]

        if not recent or not historical:
            return {"status": "insufficient_data"}

        # Compare win rates
        recent_wr = len([t for t in recent if t.net_pnl >= 0]) / len(recent) * 100
        hist_wr = len([t for t in historical if t.net_pnl >= 0]) / len(historical) * 100

        # Compare avg P&L
        recent_avg = sum(t.net_pnl for t in recent) / len(recent)
        hist_avg = sum(t.net_pnl for t in historical) / len(historical)

        # Detect drift
        wr_drift = recent_wr - hist_wr
        pnl_drift = float(recent_avg - hist_avg)

        is_drifting = abs(wr_drift) > 10 or abs(pnl_drift) > float(hist_avg) * 0.5

        return {
            "is_drifting": is_drifting,
            "recent_win_rate": recent_wr,
            "historical_win_rate": hist_wr,
            "win_rate_drift": wr_drift,
            "recent_avg_pnl": float(recent_avg),
            "historical_avg_pnl": float(hist_avg),
            "pnl_drift": pnl_drift,
            "recent_trades": len(recent),
            "historical_trades": len(historical),
        }

    def get_performance_summary(self) -> dict[str, Any]:
        """Get overall performance summary.

        Returns:
            Summary dict
        """
        if not self._trades:
            return {"total_trades": 0}

        total_pnl = sum(t.net_pnl for t in self._trades)
        winning = [t for t in self._trades if t.net_pnl >= 0]
        losing = [t for t in self._trades if t.net_pnl < 0]

        avg_win = sum(t.net_pnl for t in winning) / len(winning) if winning else Decimal("0")
        avg_loss = sum(t.net_pnl for t in losing) / len(losing) if losing else Decimal("0")

        profit_factor = abs(float(avg_win * len(winning)) / float(avg_loss * len(losing))) if losing and avg_loss != 0 else 0

        # Greeks contribution
        total_delta_pnl = sum(t.delta_pnl for t in self._trades)
        total_theta_pnl = sum(t.theta_pnl for t in self._trades)
        total_vega_pnl = sum(t.vega_pnl for t in self._trades)

        return {
            "total_trades": len(self._trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(self._trades) * 100,
            "total_pnl": float(total_pnl),
            "avg_pnl": float(total_pnl / len(self._trades)),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "profit_factor": profit_factor,
            "delta_pnl": float(total_delta_pnl),
            "theta_pnl": float(total_theta_pnl),
            "vega_pnl": float(total_vega_pnl),
        }
