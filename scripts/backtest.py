#!/usr/bin/env python3
"""
Backtest Script.

Replays saved option chain snapshots from event log for backtesting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, date, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.storage.db import init_database
from nse_advisor.storage.event_log import get_event_log, EventType
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.signals.engine import get_signal_engine
from nse_advisor.market.regime import get_regime_classifier


IST = ZoneInfo("Asia/Kolkata")


async def load_snapshots(
    start_date: date,
    end_date: date,
    underlying: str = "NIFTY",
) -> list[dict]:
    """
    Load option chain snapshots from event log.
    
    Args:
        start_date: Start date
        end_date: End date
        underlying: Underlying symbol
        
    Returns:
        List of snapshot events
    """
    event_log = get_event_log()
    
    start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=IST)
    
    events = event_log.get_events_since(start_dt, EventType.OPTION_CHAIN_SNAPSHOT)
    
    # Filter by date and underlying
    filtered = []
    for event in events:
        if event.timestamp.date() <= end_date:
            if event.underlying == underlying:
                filtered.append(event)
    
    return filtered


async def replay_signals(
    snapshots: list[dict],
    output_file: Path | None = None,
) -> dict:
    """
    Replay signals using historical snapshots.
    
    Args:
        snapshots: List of option chain snapshots
        output_file: Optional output file for results
        
    Returns:
        Backtest results
    """
    signal_engine = get_signal_engine()
    regime_classifier = get_regime_classifier()
    
    results = {
        "total_signals": 0,
        "bullish_signals": 0,
        "bearish_signals": 0,
        "neutral_signals": 0,
        "signals": [],
    }
    
    for event in snapshots:
        try:
            # Reconstruct chain from event payload
            payload = event.payload
            
            # Run signal engine
            aggregated = await signal_engine.scan()
            
            if aggregated:
                results["total_signals"] += 1
                
                if aggregated.is_bullish:
                    results["bullish_signals"] += 1
                elif aggregated.is_bearish:
                    results["bearish_signals"] += 1
                else:
                    results["neutral_signals"] += 1
                
                results["signals"].append({
                    "timestamp": event.timestamp.isoformat(),
                    "score": aggregated.composite_score,
                    "confidence": aggregated.composite_confidence,
                    "direction": aggregated.direction,
                    "should_recommend": aggregated.should_recommend,
                })
        except Exception as e:
            print(f"Error processing snapshot: {e}")
    
    # Write results
    if output_file:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results written to {output_file}")
    
    return results


def print_summary(results: dict) -> None:
    """Print backtest summary."""
    print("\n" + "=" * 50)
    print("BACKTEST SUMMARY")
    print("=" * 50)
    print(f"Total Signals: {results['total_signals']}")
    print(f"Bullish: {results['bullish_signals']} ({results['bullish_signals']/results['total_signals']*100:.1f}%)" if results['total_signals'] > 0 else "Bullish: 0")
    print(f"Bearish: {results['bearish_signals']} ({results['bearish_signals']/results['total_signals']*100:.1f}%)" if results['total_signals'] > 0 else "Bearish: 0")
    print(f"Neutral: {results['neutral_signals']} ({results['neutral_signals']/results['total_signals']*100:.1f}%)" if results['total_signals'] > 0 else "Neutral: 0")
    print("=" * 50)


async def main(args: argparse.Namespace) -> None:
    """Main backtest function."""
    # Initialize database
    await init_database()
    
    print(f"Loading snapshots from {args.start_date} to {args.end_date}...")
    snapshots = await load_snapshots(
        args.start_date,
        args.end_date,
        args.underlying,
    )
    
    print(f"Found {len(snapshots)} snapshots")
    
    if not snapshots:
        print("No snapshots found. Make sure event log has OPTION_CHAIN_SNAPSHOT events.")
        return
    
    print("Replaying signals...")
    results = await replay_signals(
        snapshots,
        Path(args.output) if args.output else None,
    )
    
    print_summary(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest signal engine")
    parser.add_argument(
        "--start-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today() - timedelta(days=30),
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--underlying",
        type=str,
        default="NIFTY",
        help="Underlying symbol",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results (JSON)",
    )
    
    args = parser.parse_args()
    asyncio.run(main(args))
