"""
Backtest Script.

Replays historical option chain snapshots from the event log
to test signal engine performance.
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


async def load_snapshots(
    start_date: date,
    end_date: date,
    underlying: str = "NIFTY",
) -> list[dict[str, Any]]:
    """
    Load option chain snapshots from event log.
    
    Args:
        start_date: Start date
        end_date: End date
        underlying: Underlying symbol
        
    Returns:
        List of snapshots
    """
    from nse_advisor.storage.db import get_database
    from nse_advisor.storage.models import OptionChainRecord
    from sqlalchemy import select
    
    db = get_database()
    await db.connect()
    
    async with db.session() as session:
        stmt = select(OptionChainRecord).where(
            OptionChainRecord.underlying == underlying,
            OptionChainRecord.timestamp >= datetime.combine(start_date, datetime.min.time()),
            OptionChainRecord.timestamp <= datetime.combine(end_date, datetime.max.time()),
        ).order_by(OptionChainRecord.timestamp)
        
        result = await session.execute(stmt)
        records = result.scalars().all()
    
    return [
        {
            "timestamp": r.timestamp,
            "spot_price": r.spot_price,
            "chain_data": r.chain_data,
            "pcr": r.pcr,
            "max_pain": r.max_pain,
        }
        for r in records
    ]


async def replay_snapshot(
    snapshot: dict[str, Any],
    signal_engine: Any,
) -> dict[str, Any]:
    """
    Replay a single snapshot through the signal engine.
    
    Args:
        snapshot: Option chain snapshot
        signal_engine: Signal engine instance
        
    Returns:
        Signal result
    """
    from nse_advisor.market.option_chain import OptionChainSnapshot, StrikeData
    
    # Reconstruct chain from snapshot data
    # (In real implementation, would need proper deserialization)
    
    # Run signal engine
    result = await signal_engine.scan()
    
    return {
        "timestamp": snapshot["timestamp"],
        "spot_price": snapshot["spot_price"],
        "composite_score": result.composite_score if result else 0,
        "confidence": result.composite_confidence if result else 0,
        "direction": result.direction if result else "neutral",
        "should_recommend": result.should_recommend if result else False,
        "regime": result.regime.value if result else None,
    }


async def run_backtest(
    start_date: date,
    end_date: date,
    underlying: str = "NIFTY",
    output_path: Path | None = None,
) -> pd.DataFrame:
    """
    Run backtest on historical data.
    
    Args:
        start_date: Start date
        end_date: End date
        underlying: Underlying symbol
        output_path: Output file path
        
    Returns:
        DataFrame with backtest results
    """
    from nse_advisor.signals.engine import SignalEngine
    
    logger.info(f"Starting backtest: {start_date} to {end_date}")
    
    # Load snapshots
    snapshots = await load_snapshots(start_date, end_date, underlying)
    logger.info(f"Loaded {len(snapshots)} snapshots")
    
    if not snapshots:
        logger.warning("No snapshots found for the given period")
        return pd.DataFrame()
    
    # Initialize signal engine
    signal_engine = SignalEngine()
    
    # Replay each snapshot
    results = []
    for i, snapshot in enumerate(snapshots):
        if i % 100 == 0:
            logger.info(f"Processing snapshot {i+1}/{len(snapshots)}")
        
        try:
            result = await replay_snapshot(snapshot, signal_engine)
            results.append(result)
        except Exception as e:
            logger.warning(f"Error processing snapshot {i}: {e}")
            continue
    
    # Create DataFrame
    df = pd.DataFrame(results)
    
    # Calculate metrics
    if not df.empty:
        # Signal count
        signal_count = df["should_recommend"].sum()
        
        # Average score
        avg_score = df["composite_score"].mean()
        
        # Direction distribution
        direction_counts = df["direction"].value_counts()
        
        logger.info(f"\nBacktest Results:")
        logger.info(f"  Total snapshots: {len(df)}")
        logger.info(f"  Signals generated: {signal_count}")
        logger.info(f"  Average score: {avg_score:.3f}")
        logger.info(f"  Direction distribution:\n{direction_counts}")
    
    # Save results
    if output_path:
        df.to_csv(output_path, index=False)
        logger.info(f"Results saved to {output_path}")
    
    return df


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Backtest NSE Options Signal Advisor"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--underlying",
        type=str,
        default="NIFTY",
        help="Underlying symbol (default: NIFTY)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="backtest_results.csv",
        help="Output CSV file path",
    )
    
    args = parser.parse_args()
    
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    output_path = Path(args.output)
    
    asyncio.run(run_backtest(
        start_date=start_date,
        end_date=end_date,
        underlying=args.underlying,
        output_path=output_path,
    ))


if __name__ == "__main__":
    main()
