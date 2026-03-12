"""
Download Historical IV Data.

Seeds 252 days of IV history for IVR/IVP calculation.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


def download_vix_history(
    symbol: str = "^INDIAVIX",
    days: int = 252,
) -> pd.DataFrame:
    """
    Download India VIX history from yfinance.
    
    Args:
        symbol: VIX symbol
        days: Number of days
        
    Returns:
        DataFrame with VIX history
    """
    logger.info(f"Downloading {days} days of VIX history...")
    
    # Add buffer for weekends/holidays
    buffer_days = int(days * 1.5)
    
    end_date = datetime.now(IST).date()
    start_date = end_date - timedelta(days=buffer_days)
    
    # Download data
    ticker = yf.Ticker(symbol)
    df = ticker.history(
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        interval="1d",
    )
    
    if df.empty:
        logger.warning(f"No data returned for {symbol}")
        return pd.DataFrame()
    
    # Clean up
    df = df.reset_index()
    df = df.rename(columns={
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })
    
    # Keep only needed columns
    df = df[["date", "open", "high", "low", "close", "volume"]]
    
    # Convert to IST if needed
    if df["date"].dt.tz is None:
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize("UTC").dt.tz_convert(IST)
    else:
        df["date"] = pd.to_datetime(df["date"]).dt.tz_convert(IST)
    
    df["date"] = df["date"].dt.date
    
    # Take last N days
    df = df.tail(days)
    
    logger.info(f"Downloaded {len(df)} days of data")
    
    return df


def calculate_iv_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate IV metrics from VIX data.
    
    Args:
        df: DataFrame with VIX data
        
    Returns:
        DataFrame with IV metrics
    """
    if df.empty:
        return df
    
    # Use close as IV proxy
    df["iv"] = df["close"]
    
    # Calculate rolling metrics
    df["iv_52w_high"] = df["iv"].rolling(window=252, min_periods=20).max()
    df["iv_52w_low"] = df["iv"].rolling(window=252, min_periods=20).min()
    
    # IVR = (current - 52w_low) / (52w_high - 52w_low) * 100
    df["ivr"] = (
        (df["iv"] - df["iv_52w_low"]) / 
        (df["iv_52w_high"] - df["iv_52w_low"])
    ) * 100
    
    # IVP = percentile rank over last 252 days
    df["ivp"] = df["iv"].rolling(window=252, min_periods=20).apply(
        lambda x: (x.iloc[-1] > x.iloc[:-1]).sum() / len(x.iloc[:-1]) * 100,
        raw=False
    )
    
    return df


async def save_to_database(df: pd.DataFrame, underlying: str = "NIFTY") -> None:
    """
    Save IV history to database.
    
    Args:
        df: DataFrame with IV data
        underlying: Underlying symbol
    """
    from nse_advisor.storage.db import get_database
    from nse_advisor.storage.models import IVHistory
    
    db = get_database()
    await db.connect()
    
    async with db.session() as session:
        for _, row in df.iterrows():
            record = IVHistory(
                date=row["date"],
                underlying=underlying,
                atm_iv=row["iv"],
                iv_high=row.get("iv_52w_high"),
                iv_low=row.get("iv_52w_low"),
                vix=row["close"],
            )
            session.add(record)
        
        await session.commit()
    
    logger.info(f"Saved {len(df)} IV records to database")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download historical IV data for IVR/IVP calculation"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=252,
        help="Number of days to download (default: 252)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="iv_history.csv",
        help="Output CSV file path",
    )
    parser.add_argument(
        "--underlying",
        type=str,
        default="NIFTY",
        help="Underlying symbol for database (default: NIFTY)",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Save to database",
    )
    
    args = parser.parse_args()
    
    # Download VIX history
    df = download_vix_history(days=args.days)
    
    if df.empty:
        logger.error("No data downloaded")
        return
    
    # Calculate metrics
    df = calculate_iv_metrics(df)
    
    # Save to CSV
    output_path = Path(args.output)
    df.to_csv(output_path, index=False)
    logger.info(f"Saved to {output_path}")
    
    # Save to database if requested
    if args.save_db:
        asyncio.run(save_to_database(df, args.underlying))
    
    # Print summary
    logger.info("\nIV History Summary:")
    logger.info(f"  Date range: {df['date'].min()} to {df['date'].max()}")
    logger.info(f"  Current IV: {df['iv'].iloc[-1]:.2f}")
    logger.info(f"  52W High: {df['iv_52w_high'].iloc[-1]:.2f}")
    logger.info(f"  52W Low: {df['iv_52w_low'].iloc[-1]:.2f}")
    logger.info(f"  Current IVR: {df['ivr'].iloc[-1]:.1f}%")
    logger.info(f"  Current IVP: {df['ivp'].iloc[-1]:.1f}%")


if __name__ == "__main__":
    main()
