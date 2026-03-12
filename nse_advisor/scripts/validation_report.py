"""
Validation Report Script.

Generates a validation report after paper trading to determine
if the system is ready for live trading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


# Validation thresholds
MIN_TRADES = 20
MIN_WIN_RATE = 0.55
MIN_SHARPE_RATIO = 1.0
MAX_DRAWDOWN_PCT = 0.15
MIN_PROFIT_FACTOR = 1.2
MIN_SIGNAL_ACCURACY = 0.55


async def get_paper_trades(
    lookback_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Get paper trades from database.
    
    Args:
        lookback_days: Number of days to look back
        
    Returns:
        List of paper trades
    """
    from nse_advisor.storage.db import get_database
    from nse_advisor.storage.models import Trade
    from sqlalchemy import select
    
    cutoff = datetime.now(IST) - timedelta(days=lookback_days)
    
    db = get_database()
    await db.connect()
    
    async with db.session() as session:
        stmt = select(Trade).where(
            Trade.paper_mode == True,
            Trade.status == "CLOSED",
            Trade.exit_time >= cutoff,
        ).order_by(Trade.exit_time)
        
        result = await session.execute(stmt)
        trades = result.scalars().all()
    
    return [
        {
            "trade_id": t.id,
            "strategy": t.strategy_name,
            "underlying": t.underlying,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "realized_pnl": t.realized_pnl or 0,
            "max_profit": t.max_profit,
            "max_loss": t.max_loss,
        }
        for t in trades
    ]


def calculate_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calculate validation metrics from trades.
    
    Args:
        trades: List of trades
        
    Returns:
        Dict of metrics
    """
    if not trades:
        return {
            "total_trades": 0,
            "valid": False,
            "reason": "No trades found",
        }
    
    df = pd.DataFrame(trades)
    
    # Basic stats
    total_trades = len(df)
    winning_trades = len(df[df["realized_pnl"] > 0])
    losing_trades = len(df[df["realized_pnl"] < 0])
    
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    
    # P&L metrics
    total_pnl = df["realized_pnl"].sum()
    avg_pnl = df["realized_pnl"].mean()
    
    gross_profit = df[df["realized_pnl"] > 0]["realized_pnl"].sum()
    gross_loss = abs(df[df["realized_pnl"] < 0]["realized_pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    
    # Drawdown
    df = df.sort_values("exit_time")
    df["cumulative_pnl"] = df["realized_pnl"].cumsum()
    df["running_max"] = df["cumulative_pnl"].cummax()
    df["drawdown"] = df["cumulative_pnl"] - df["running_max"]
    max_drawdown = abs(df["drawdown"].min())
    max_drawdown_pct = max_drawdown / df["running_max"].max() if df["running_max"].max() > 0 else 0
    
    # Sharpe ratio (simplified)
    if df["realized_pnl"].std() > 0:
        sharpe = (df["realized_pnl"].mean() / df["realized_pnl"].std()) * (252 ** 0.5)
    else:
        sharpe = 0
    
    # Strategy breakdown
    strategy_stats = {}
    for strategy in df["strategy"].unique():
        strategy_df = df[df["strategy"] == strategy]
        strategy_wins = len(strategy_df[strategy_df["realized_pnl"] > 0])
        strategy_stats[strategy] = {
            "trades": len(strategy_df),
            "win_rate": strategy_wins / len(strategy_df) if len(strategy_df) > 0 else 0,
            "total_pnl": strategy_df["realized_pnl"].sum(),
        }
    
    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe,
        "strategy_stats": strategy_stats,
    }


def validate_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """
    Validate metrics against thresholds.
    
    Args:
        metrics: Calculated metrics
        
    Returns:
        Validation results
    """
    issues = []
    warnings = []
    
    # Check minimum trades
    if metrics["total_trades"] < MIN_TRADES:
        issues.append(f"Insufficient trades: {metrics['total_trades']} < {MIN_TRADES} required")
    
    # Check win rate
    if metrics["win_rate"] < MIN_WIN_RATE:
        issues.append(f"Win rate below threshold: {metrics['win_rate']:.1%} < {MIN_WIN_RATE:.1%}")
    
    # Check Sharpe ratio
    if metrics["sharpe_ratio"] < MIN_SHARPE_RATIO:
        warnings.append(f"Sharpe ratio below threshold: {metrics['sharpe_ratio']:.2f} < {MIN_SHARPE_RATIO}")
    
    # Check max drawdown
    if metrics["max_drawdown_pct"] > MAX_DRAWDOWN_PCT:
        issues.append(f"Max drawdown exceeded: {metrics['max_drawdown_pct']:.1%} > {MAX_DRAWDOWN_PCT:.1%}")
    
    # Check profit factor
    if metrics["profit_factor"] < MIN_PROFIT_FACTOR:
        warnings.append(f"Profit factor below threshold: {metrics['profit_factor']:.2f} < {MIN_PROFIT_FACTOR}")
    
    # Determine validation result
    is_valid = len(issues) == 0 and metrics["total_trades"] >= MIN_TRADES
    
    return {
        "is_valid": is_valid,
        "issues": issues,
        "warnings": warnings,
        "recommendation": "GO LIVE" if is_valid else "CONTINUE PAPER TRADING",
    }


def generate_report(
    metrics: dict[str, Any],
    validation: dict[str, Any],
    lookback_days: int,
    output_path: Path | None = None,
) -> str:
    """
    Generate validation report.
    
    Args:
        metrics: Calculated metrics
        validation: Validation results
        lookback_days: Number of days analyzed
        output_path: Output file path
        
    Returns:
        Report text
    """
    now = datetime.now(IST)
    
    report = f"""
================================================================================
                   NSE OPTIONS SIGNAL ADVISOR - VALIDATION REPORT
================================================================================

Report Generated: {now.strftime('%Y-%m-%d %H:%M IST')}
Period Analyzed: Last {lookback_days} days

================================================================================
                                 SUMMARY
================================================================================

Recommendation: {validation['recommendation']}
Total Trades: {metrics['total_trades']}
Win Rate: {metrics['win_rate']:.1%}
Total P&L: ₹{metrics['total_pnl']:,.0f}

================================================================================
                             DETAILED METRICS
================================================================================

TRADE STATISTICS:
  - Total Trades: {metrics['total_trades']}
  - Winning Trades: {metrics['winning_trades']}
  - Losing Trades: {metrics['losing_trades']}
  - Win Rate: {metrics['win_rate']:.1%}

P&L METRICS:
  - Total P&L: ₹{metrics['total_pnl']:,.0f}
  - Average P&L per Trade: ₹{metrics['avg_pnl']:,.0f}
  - Profit Factor: {metrics['profit_factor']:.2f}

RISK METRICS:
  - Maximum Drawdown: ₹{metrics['max_drawdown']:,.0f} ({metrics['max_drawdown_pct']:.1%})
  - Sharpe Ratio (annualized): {metrics['sharpe_ratio']:.2f}

================================================================================
                          STRATEGY BREAKDOWN
================================================================================
"""
    
    for strategy, stats in metrics.get("strategy_stats", {}).items():
        report += f"""
{strategy}:
  - Trades: {stats['trades']}
  - Win Rate: {stats['win_rate']:.1%}
  - Total P&L: ₹{stats['total_pnl']:,.0f}
"""
    
    report += f"""
================================================================================
                           VALIDATION RESULTS
================================================================================

ISSUES (must fix before going live):
"""
    
    if validation["issues"]:
        for issue in validation["issues"]:
            report += f"  ❌ {issue}\n"
    else:
        report += "  ✅ No critical issues\n"
    
    report += f"""
WARNINGS (monitor closely):
"""
    
    if validation["warnings"]:
        for warning in validation["warnings"]:
            report += f"  ⚠️ {warning}\n"
    else:
        report += "  ✅ No warnings\n"
    
    report += f"""
================================================================================
                            THRESHOLDS USED
================================================================================

  - Minimum Trades: {MIN_TRADES}
  - Minimum Win Rate: {MIN_WIN_RATE:.1%}
  - Minimum Sharpe Ratio: {MIN_SHARPE_RATIO}
  - Maximum Drawdown: {MAX_DRAWDOWN_PCT:.1%}
  - Minimum Profit Factor: {MIN_PROFIT_FACTOR}

================================================================================
"""
    
    # Save report
    if output_path:
        with open(output_path, "w") as f:
            f.write(report)
        
        # Also save as JSON
        json_path = output_path.with_suffix(".json")
        with open(json_path, "w") as f:
            json.dump({
                "timestamp": now.isoformat(),
                "lookback_days": lookback_days,
                "metrics": metrics,
                "validation": validation,
            }, f, indent=2, default=str)
        
        logger.info(f"Report saved to {output_path}")
        logger.info(f"JSON saved to {json_path}")
    
    return report


async def run_validation(
    lookback_days: int = 30,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Run full validation process.
    
    Args:
        lookback_days: Number of days to analyze
        output_path: Output file path
        
    Returns:
        Validation results
    """
    logger.info(f"Running validation for last {lookback_days} days...")
    
    # Get trades
    trades = await get_paper_trades(lookback_days)
    logger.info(f"Found {len(trades)} paper trades")
    
    # Calculate metrics
    metrics = calculate_metrics(trades)
    
    # Validate
    validation = validate_metrics(metrics)
    
    # Generate report
    report = generate_report(metrics, validation, lookback_days, output_path)
    print(report)
    
    return {
        "metrics": metrics,
        "validation": validation,
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate validation report for paper trading"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to analyze (default: 30)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="validation_report.txt",
        help="Output file path",
    )
    
    args = parser.parse_args()
    
    output_path = Path(args.output)
    
    asyncio.run(run_validation(
        lookback_days=args.days,
        output_path=output_path,
    ))


if __name__ == "__main__":
    main()
