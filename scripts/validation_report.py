#!/usr/bin/env python3
"""
Validation Report Script.

After 30 paper trading days, generates a comprehensive report
on signal accuracy and recommends whether to go live.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, date, timedelta
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.storage.db import init_database, get_database
from nse_advisor.postmortem.engine import get_postmortem_engine
from nse_advisor.tracker.state import ManualTrade


IST = ZoneInfo("Asia/Kolkata")


# Minimum thresholds for going live
MIN_TRADES = 20
MIN_WIN_RATE = 0.55
MIN_PROFIT_FACTOR = 1.3
MIN_SIGNAL_ACCURACY = 0.55
MAX_DRAWDOWN_PCT = 0.15


class ValidationReport:
    """Generates validation report for paper trading results."""
    
    def __init__(self, trades: list[ManualTrade]) -> None:
        """Initialize with trade history."""
        self.trades = [t for t in trades if t.paper_mode and not t.is_open]
        self.report: dict[str, Any] = {}
    
    def generate(self) -> dict[str, Any]:
        """Generate full validation report."""
        self.report = {
            "generated_at": datetime.now(IST).isoformat(),
            "period_start": None,
            "period_end": None,
            "total_trades": len(self.trades),
            "metrics": {},
            "signal_accuracy": {},
            "strategy_breakdown": {},
            "regime_breakdown": {},
            "recommendations": [],
            "go_live_approved": False,
        }
        
        if not self.trades:
            self.report["recommendations"].append("No paper trades found. Need at least 20 trades.")
            return self.report
        
        # Set period
        entry_times = [t.entry_time for t in self.trades]
        self.report["period_start"] = min(entry_times).isoformat()
        self.report["period_end"] = max(entry_times).isoformat()
        
        # Calculate metrics
        self._calculate_basic_metrics()
        self._calculate_signal_accuracy()
        self._calculate_strategy_breakdown()
        self._calculate_regime_breakdown()
        self._generate_recommendations()
        self._evaluate_go_live()
        
        return self.report
    
    def _calculate_basic_metrics(self) -> None:
        """Calculate basic trading metrics."""
        pnls = [t.realized_pnl or 0 for t in self.trades]
        
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        self.report["metrics"] = {
            "total_trades": len(self.trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(pnls) if pnls else 0,
            "total_pnl": sum(pnls),
            "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "profit_factor": abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float('inf'),
            "max_drawdown": self._calculate_max_drawdown(pnls),
            "sharpe_ratio": self._calculate_sharpe(pnls),
        }
    
    def _calculate_max_drawdown(self, pnls: list[float]) -> float:
        """Calculate maximum drawdown."""
        if not pnls:
            return 0
        
        cumulative = []
        total = 0
        for p in pnls:
            total += p
            cumulative.append(total)
        
        peak = cumulative[0]
        max_dd = 0
        
        for value in cumulative:
            if value > peak:
                peak = value
            drawdown = peak - value
            if drawdown > max_dd:
                max_dd = drawdown
        
        return max_dd
    
    def _calculate_sharpe(self, pnls: list[float], risk_free_rate: float = 0.068) -> float:
        """Calculate Sharpe ratio (annualized)."""
        if len(pnls) < 2:
            return 0
        
        df = pd.Series(pnls)
        mean_return = df.mean()
        std_return = df.std()
        
        if std_return == 0:
            return 0
        
        # Annualize (assuming ~250 trading days)
        daily_rf = risk_free_rate / 250
        sharpe = (mean_return - daily_rf) / std_return * (250 ** 0.5)
        
        return sharpe
    
    def _calculate_signal_accuracy(self) -> None:
        """Calculate per-signal accuracy."""
        signal_results: dict[str, dict] = {}
        
        for trade in self.trades:
            pnl = trade.realized_pnl or 0
            was_profitable = pnl > 0
            
            for signal_name, signal_data in trade.signal_scores_at_entry.items():
                if signal_name not in signal_results:
                    signal_results[signal_name] = {
                        "total": 0,
                        "correct": 0,
                        "total_contribution": 0,
                    }
                
                if isinstance(signal_data, dict):
                    score = signal_data.get("score", 0)
                else:
                    score = signal_data
                
                signal_results[signal_name]["total"] += 1
                
                # Signal correct if direction matched outcome
                predicted_bull = score > 0
                if (predicted_bull and was_profitable) or (not predicted_bull and not was_profitable):
                    signal_results[signal_name]["correct"] += 1
                
                signal_results[signal_name]["total_contribution"] += score
        
        # Calculate accuracy percentages
        for name, data in signal_results.items():
            data["accuracy"] = data["correct"] / data["total"] if data["total"] > 0 else 0
            data["avg_score"] = data["total_contribution"] / data["total"] if data["total"] > 0 else 0
        
        self.report["signal_accuracy"] = signal_results
    
    def _calculate_strategy_breakdown(self) -> None:
        """Calculate per-strategy performance."""
        strategy_results: dict[str, dict] = {}
        
        for trade in self.trades:
            strategy = trade.strategy_name
            pnl = trade.realized_pnl or 0
            
            if strategy not in strategy_results:
                strategy_results[strategy] = {
                    "total": 0,
                    "wins": 0,
                    "total_pnl": 0,
                }
            
            strategy_results[strategy]["total"] += 1
            strategy_results[strategy]["total_pnl"] += pnl
            if pnl > 0:
                strategy_results[strategy]["wins"] += 1
        
        # Calculate win rates
        for name, data in strategy_results.items():
            data["win_rate"] = data["wins"] / data["total"] if data["total"] > 0 else 0
            data["avg_pnl"] = data["total_pnl"] / data["total"] if data["total"] > 0 else 0
        
        self.report["strategy_breakdown"] = strategy_results
    
    def _calculate_regime_breakdown(self) -> None:
        """Calculate per-regime performance."""
        regime_results: dict[str, dict] = {}
        
        for trade in self.trades:
            regime = trade.regime_at_entry or "UNKNOWN"
            pnl = trade.realized_pnl or 0
            
            if regime not in regime_results:
                regime_results[regime] = {
                    "total": 0,
                    "wins": 0,
                    "total_pnl": 0,
                }
            
            regime_results[regime]["total"] += 1
            regime_results[regime]["total_pnl"] += pnl
            if pnl > 0:
                regime_results[regime]["wins"] += 1
        
        # Calculate win rates
        for name, data in regime_results.items():
            data["win_rate"] = data["wins"] / data["total"] if data["total"] > 0 else 0
        
        self.report["regime_breakdown"] = regime_results
    
    def _generate_recommendations(self) -> None:
        """Generate actionable recommendations."""
        recs = []
        metrics = self.report["metrics"]
        
        # Check minimum trades
        if metrics["total_trades"] < MIN_TRADES:
            recs.append(f"Need more trades: {metrics['total_trades']}/{MIN_TRADES} minimum")
        
        # Check win rate
        if metrics["win_rate"] < MIN_WIN_RATE:
            recs.append(f"Win rate below threshold: {metrics['win_rate']:.1%} < {MIN_WIN_RATE:.1%}")
        
        # Check profit factor
        if metrics["profit_factor"] < MIN_PROFIT_FACTOR:
            recs.append(f"Profit factor low: {metrics['profit_factor']:.2f} < {MIN_PROFIT_FACTOR}")
        
        # Check signal accuracy
        signal_acc = self.report["signal_accuracy"]
        for name, data in signal_acc.items():
            if data["total"] >= 10 and data["accuracy"] < MIN_SIGNAL_ACCURACY:
                recs.append(f"Signal '{name}' underperforming: {data['accuracy']:.1%} accuracy")
        
        # Check strategy performance
        strategy_stats = self.report["strategy_breakdown"]
        for name, data in strategy_stats.items():
            if data["total"] >= 5 and data["win_rate"] < 0.40:
                recs.append(f"Consider disabling '{name}': {data['win_rate']:.1%} win rate")
        
        # Check regime performance
        regime_stats = self.report["regime_breakdown"]
        for name, data in regime_stats.items():
            if data["total"] >= 5 and data["win_rate"] > 0.70:
                recs.append(f"Strong in {name} regime ({data['win_rate']:.1%}) - consider increasing allocation")
        
        self.report["recommendations"] = recs
    
    def _evaluate_go_live(self) -> None:
        """Evaluate if system is ready to go live."""
        metrics = self.report["metrics"]
        
        conditions = [
            metrics["total_trades"] >= MIN_TRADES,
            metrics["win_rate"] >= MIN_WIN_RATE,
            metrics["profit_factor"] >= MIN_PROFIT_FACTOR,
            metrics["total_pnl"] > 0,
        ]
        
        # All conditions must be met
        self.report["go_live_approved"] = all(conditions)
        
        if self.report["go_live_approved"]:
            self.report["recommendations"].append(
                "✅ System APPROVED for live trading with reduced position sizes"
            )
        else:
            self.report["recommendations"].append(
                "❌ System NOT READY for live trading - address above issues"
            )


def print_report(report: dict) -> None:
    """Pretty print the validation report."""
    print("\n" + "=" * 60)
    print("📊 PAPER TRADING VALIDATION REPORT")
    print("=" * 60)
    
    print(f"\nGenerated: {report['generated_at']}")
    print(f"Period: {report['period_start']} to {report['period_end']}")
    print(f"Total Trades: {report['total_trades']}")
    
    print("\n" + "-" * 40)
    print("PERFORMANCE METRICS")
    print("-" * 40)
    
    m = report["metrics"]
    print(f"Win Rate:      {m['win_rate']:.1%} ({m['winning_trades']}/{m['total_trades']})")
    print(f"Total P&L:     ₹{m['total_pnl']:,.0f}")
    print(f"Avg Win:       ₹{m['avg_win']:,.0f}")
    print(f"Avg Loss:      ₹{m['avg_loss']:,.0f}")
    print(f"Profit Factor: {m['profit_factor']:.2f}")
    print(f"Max Drawdown:  ₹{m['max_drawdown']:,.0f}")
    print(f"Sharpe Ratio:  {m['sharpe_ratio']:.2f}")
    
    print("\n" + "-" * 40)
    print("SIGNAL ACCURACY")
    print("-" * 40)
    
    for name, data in sorted(report["signal_accuracy"].items(), key=lambda x: x[1]["accuracy"], reverse=True):
        print(f"{name:20s}: {data['accuracy']:.1%} ({data['correct']}/{data['total']})")
    
    print("\n" + "-" * 40)
    print("STRATEGY BREAKDOWN")
    print("-" * 40)
    
    for name, data in sorted(report["strategy_breakdown"].items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        print(f"{name:20s}: {data['win_rate']:.1%} win rate, ₹{data['total_pnl']:,.0f} total")
    
    print("\n" + "-" * 40)
    print("RECOMMENDATIONS")
    print("-" * 40)
    
    for rec in report["recommendations"]:
        print(f"• {rec}")
    
    print("\n" + "=" * 60)
    if report["go_live_approved"]:
        print("🟢 GO LIVE: APPROVED")
    else:
        print("🔴 GO LIVE: NOT APPROVED")
    print("=" * 60)


async def main(args: argparse.Namespace) -> None:
    """Main function."""
    # Initialize database
    await init_database()
    
    # Load trades (would normally come from database)
    # For demo, create sample trades
    print("Loading paper trades...")
    
    # In real implementation:
    # from nse_advisor.tracker.position_tracker import get_position_tracker
    # tracker = get_position_tracker()
    # trades = tracker.get_all_trades()
    
    trades: list[ManualTrade] = []  # Would be loaded from DB
    
    print(f"Found {len(trades)} paper trades")
    
    # Generate report
    validator = ValidationReport(trades)
    report = validator.generate()
    
    # Print report
    print_report(report)
    
    # Export if requested
    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nReport exported to {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate validation report for paper trading results"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output JSON file for report",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback period in days",
    )
    
    args = parser.parse_args()
    asyncio.run(main(args))
