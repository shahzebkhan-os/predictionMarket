#!/usr/bin/env python3
"""
Download Historical IV Script.

Seeds 252 days of IV history for IVR/IVP calculation.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, date, timedelta

import yfinance as yf
import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.storage.db import init_database, get_database
from nse_advisor.storage.models import IVHistory


IST = ZoneInfo("Asia/Kolkata")


async def download_vix_history(
    symbol: str = "^INDIAVIX",
    days: int = 252,
) -> pd.DataFrame:
    """
    Download VIX history from yfinance.
    
    Args:
        symbol: VIX symbol (^INDIAVIX for India VIX)
        days: Number of days to download
        
    Returns:
        DataFrame with VIX history
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days + 30)  # Extra buffer for holidays
    
    print(f"Downloading {symbol} from {start_date} to {end_date}...")
    
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date)
    
    if df.empty:
        print(f"No data found for {symbol}")
        return pd.DataFrame()
    
    print(f"Downloaded {len(df)} records")
    return df


def calculate_iv_stats(
    vix_data: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate IV statistics from VIX data.
    
    Args:
        vix_data: DataFrame with VIX OHLCV
        
    Returns:
        DataFrame with date, close, high, low
    """
    df = vix_data[["Close", "High", "Low"]].copy()
    df.columns = ["atm_iv", "iv_high", "iv_low"]
    df["date"] = df.index.date
    df["vix"] = df["atm_iv"]
    
    return df.reset_index(drop=True)


async def save_to_database(
    iv_data: pd.DataFrame,
    underlying: str = "NIFTY",
) -> int:
    """
    Save IV history to database.
    
    Args:
        iv_data: DataFrame with IV data
        underlying: Underlying symbol
        
    Returns:
        Number of records saved
    """
    db = get_database()
    saved = 0
    
    async with db.session() as session:
        for _, row in iv_data.iterrows():
            record = IVHistory(
                date=row["date"],
                underlying=underlying,
                atm_iv=row["atm_iv"],
                iv_high=row["iv_high"],
                iv_low=row["iv_low"],
                vix=row["vix"],
            )
            session.add(record)
            saved += 1
        
        await session.commit()
    
    return saved


async def main(args: argparse.Namespace) -> None:
    """Main function."""
    # Initialize database
    await init_database()
    
    # Download VIX history
    vix_data = await download_vix_history(
        symbol=args.symbol,
        days=args.days,
    )
    
    if vix_data.empty:
        print("Failed to download VIX data")
        return
    
    # Calculate IV stats
    print("Calculating IV statistics...")
    iv_stats = calculate_iv_stats(vix_data)
    
    # Print summary
    print(f"\nIV Statistics ({args.days} days):")
    print(f"  Min IV: {iv_stats['atm_iv'].min():.2f}")
    print(f"  Max IV: {iv_stats['atm_iv'].max():.2f}")
    print(f"  Mean IV: {iv_stats['atm_iv'].mean():.2f}")
    print(f"  Current IV: {iv_stats['atm_iv'].iloc[-1]:.2f}")
    
    # Calculate current IVR
    current = iv_stats['atm_iv'].iloc[-1]
    min_iv = iv_stats['atm_iv'].min()
    max_iv = iv_stats['atm_iv'].max()
    ivr = ((current - min_iv) / (max_iv - min_iv)) * 100
    print(f"  Current IVR: {ivr:.1f}%")
    
    # Calculate IVP
    below_current = (iv_stats['atm_iv'] < current).sum()
    ivp = (below_current / len(iv_stats)) * 100
    print(f"  Current IVP: {ivp:.1f}%")
    
    # Save to database
    if not args.dry_run:
        print(f"\nSaving to database...")
        saved = await save_to_database(iv_stats, args.underlying)
        print(f"Saved {saved} records")
    else:
        print("\nDry run - not saving to database")
    
    # Export to CSV if requested
    if args.output:
        iv_stats.to_csv(args.output, index=False)
        print(f"Exported to {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download historical IV data for IVR/IVP calculation"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="^INDIAVIX",
        help="VIX symbol to download",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=252,
        help="Number of trading days to download",
    )
    parser.add_argument(
        "--underlying",
        type=str,
        default="NIFTY",
        help="Underlying to associate IV data with",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output CSV file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save to database",
    )
    
    args = parser.parse_args()
    asyncio.run(main(args))
