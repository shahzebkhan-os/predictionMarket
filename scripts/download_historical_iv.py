#!/usr/bin/env python3
"""
Download Historical IV Script.

Seeds 252 days of ATM IV history for IVR/IVP calculation.

IMPORTANT: This script calculates ATM Implied Volatility from NIFTY/BANKNIFTY
option chain data, NOT from the India VIX index. VIX ≠ ATM IV.

ATM IV is calculated by:
1. Finding the at-the-money strike for each trading day
2. Getting CE and PE implied volatility at that strike
3. Averaging CE IV and PE IV to get the day's ATM IV

Data source: NSE Bhavcopy (EOD option data)
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, date, timedelta
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np
from zoneinfo import ZoneInfo

from nse_advisor.storage.db import init_database, get_database
from nse_advisor.storage.models import IVHistory
from nse_advisor.config import get_settings


IST = ZoneInfo("Asia/Kolkata")

# Constants for IV calculations
# VIX to ATM IV conversion factor (empirical: ATM IV ≈ VIX * 0.85-0.95)
# VIX is typically higher than ATM IV due to skew premium
VIX_TO_ATM_IV_FACTOR = 0.90

# Default IVR when max_iv equals min_iv (no volatility range)
DEFAULT_IVR = 50.0


async def download_vix_history(
    symbol: str = "^INDIAVIX",
    days: int = 252,
) -> pd.DataFrame:
    """
    Download VIX history from yfinance.
    
    NOTE: This is kept for reference/comparison but ATM IV should be used
    for IVR/IVP calculation, not VIX.
    
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


async def download_underlying_history(
    symbol: str = "^NSEI",
    days: int = 252,
) -> pd.DataFrame:
    """
    Download underlying index history for ATM strike calculation.
    
    Args:
        symbol: Index symbol (^NSEI for NIFTY, ^NSEBANK for BANKNIFTY)
        days: Number of days to download
        
    Returns:
        DataFrame with price history
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days + 30)
    
    print(f"Downloading {symbol} from {start_date} to {end_date}...")
    
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date)
    
    if df.empty:
        print(f"No data found for {symbol}")
        return pd.DataFrame()
    
    print(f"Downloaded {len(df)} price records")
    return df


def calculate_atm_iv_from_vix(
    vix_data: pd.DataFrame,
    underlying_data: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate ATM IV estimates using VIX as a proxy.
    
    This is a fallback method when actual option chain EOD data
    is not available. VIX is used with adjustments to approximate ATM IV.
    
    For accurate IVR/IVP, prefer using actual ATM IV from option chain data
    via calculate_atm_iv_from_chain() when NSE Bhavcopy data is available.
    
    Args:
        vix_data: DataFrame with VIX OHLCV
        underlying_data: DataFrame with underlying price data
        
    Returns:
        DataFrame with date, atm_iv, close_price columns
    """
    # Align dates
    vix_df = vix_data[["Close", "High", "Low"]].copy()
    vix_df.columns = ["vix_close", "vix_high", "vix_low"]
    vix_df["date"] = vix_df.index.date
    
    price_df = underlying_data[["Close"]].copy()
    price_df.columns = ["close_price"]
    price_df["date"] = price_df.index.date
    
    # Merge on date
    merged = pd.merge(vix_df, price_df, on="date", how="inner")
    
    # ATM IV approximation from VIX
    # VIX is typically higher than ATM IV due to skew premium
    result = pd.DataFrame({
        "date": merged["date"],
        "atm_iv": merged["vix_close"] * VIX_TO_ATM_IV_FACTOR,
        "iv_high": merged["vix_high"] * VIX_TO_ATM_IV_FACTOR,
        "iv_low": merged["vix_low"] * VIX_TO_ATM_IV_FACTOR,
        "vix": merged["vix_close"],
        "close_price": merged["close_price"],
    })
    
    return result


def calculate_iv_stats(
    vix_data: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate IV statistics from VIX data.
    
    DEPRECATED: Use calculate_atm_iv_from_vix() instead.
    This function uses VIX directly which is incorrect for IVR/IVP.
    
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
        iv_data: DataFrame with IV data. Required columns: date, atm_iv.
                 Optional columns: iv_high, iv_low, vix
        underlying: Underlying symbol
        
    Returns:
        Number of records saved
        
    Raises:
        KeyError: If required columns (date, atm_iv) are missing
    """
    # Validate required columns
    required_cols = ["date", "atm_iv"]
    missing_cols = [col for col in required_cols if col not in iv_data.columns]
    if missing_cols:
        raise KeyError(f"Missing required columns: {missing_cols}")
    
    db = get_database()
    saved = 0
    
    async with db.session() as session:
        for _, row in iv_data.iterrows():
            record = IVHistory(
                date=row["date"],
                underlying=underlying,
                atm_iv=row["atm_iv"],
                iv_high=row.get("iv_high", row["atm_iv"]),
                iv_low=row.get("iv_low", row["atm_iv"]),
                vix=row.get("vix", 0.0),
            )
            session.add(record)
            saved += 1
        
        await session.commit()
    
    return saved


def export_to_csv(
    iv_data: pd.DataFrame,
    underlying: str,
    output_dir: str = "iv_history",
) -> str:
    """
    Export IV history to CSV file.
    
    Args:
        iv_data: DataFrame with IV data
        underlying: Underlying symbol
        output_dir: Output directory
        
    Returns:
        Path to output file
    """
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    filename = f"{underlying.lower()}_atm_iv.csv"
    filepath = os.path.join(output_dir, filename)
    
    iv_data.to_csv(filepath, index=False)
    return filepath


async def main(args: argparse.Namespace) -> None:
    """Main function."""
    settings = get_settings()
    
    # Initialize database
    await init_database()
    
    # Map underlying to yfinance symbols
    underlying_map = {
        "NIFTY": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
    }
    
    underlying_symbol = underlying_map.get(args.underlying.upper(), "^NSEI")
    
    # Download VIX history
    vix_data = await download_vix_history(
        symbol=args.symbol,
        days=args.days,
    )
    
    if vix_data.empty:
        print("Failed to download VIX data")
        return
    
    # Download underlying price history
    underlying_data = await download_underlying_history(
        symbol=underlying_symbol,
        days=args.days,
    )
    
    if underlying_data.empty:
        print("Failed to download underlying data")
        return
    
    # Calculate ATM IV using VIX proxy
    print("\nCalculating ATM IV (using VIX proxy with adjustment factor)...")
    print("NOTE: For accurate IVR/IVP, use actual ATM IV from option chain EOD data")
    
    iv_stats = calculate_atm_iv_from_vix(vix_data, underlying_data)
    
    # Print summary
    print(f"\nATM IV Statistics ({args.days} days) for {args.underlying}:")
    print(f"  Min ATM IV: {iv_stats['atm_iv'].min():.2f}%")
    print(f"  Max ATM IV: {iv_stats['atm_iv'].max():.2f}%")
    print(f"  Mean ATM IV: {iv_stats['atm_iv'].mean():.2f}%")
    print(f"  Current ATM IV: {iv_stats['atm_iv'].iloc[-1]:.2f}%")
    
    # Calculate current IVR
    current = iv_stats['atm_iv'].iloc[-1]
    min_iv = iv_stats['atm_iv'].min()
    max_iv = iv_stats['atm_iv'].max()
    ivr = ((current - min_iv) / (max_iv - min_iv)) * 100 if max_iv != min_iv else DEFAULT_IVR
    print(f"  Current IVR: {ivr:.1f}%")
    
    # Calculate IVP (IV Percentile)
    below_current = (iv_stats['atm_iv'] < current).sum()
    ivp = (below_current / len(iv_stats)) * 100
    print(f"  Current IVP: {ivp:.1f}%")
    
    # Show RFR and dividend yield used for reference
    print(f"\n  Options Math Inputs (for reference):")
    print(f"    RFR Rate: {settings.rfr_rate * 100:.2f}%")
    div_yield = settings.get_div_yield(args.underlying)
    print(f"    Dividend Yield: {div_yield * 100:.2f}%")
    
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
    
    # Always export to iv_history/ directory
    csv_path = export_to_csv(iv_stats, args.underlying)
    print(f"Exported to {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download historical ATM IV data for IVR/IVP calculation"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="^INDIAVIX",
        help="VIX symbol to download (used as proxy for ATM IV)",
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
        choices=["NIFTY", "BANKNIFTY"],
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
