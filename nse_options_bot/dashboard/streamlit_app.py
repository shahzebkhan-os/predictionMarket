"""Streamlit dashboard for paper trading.

Live monitoring dashboard for the trading bot.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st

# Note: This module requires streamlit to be installed
# Run with: streamlit run nse_options_bot/dashboard/streamlit_app.py


def get_sample_data() -> dict[str, Any]:
    """Get sample data for demonstration.

    Returns:
        Sample data dict
    """
    return {
        "portfolio": {
            "total_pnl": 12500.0,
            "daily_pnl": 3200.0,
            "open_trades": 2,
            "margin_used": 150000.0,
            "margin_available": 350000.0,
        },
        "trades": [
            {
                "id": "T001",
                "strategy": "SHORT_STRADDLE",
                "underlying": "NIFTY",
                "entry_time": "10:30",
                "pnl": 2100.0,
                "status": "OPEN",
            },
            {
                "id": "T002",
                "strategy": "IRON_CONDOR",
                "underlying": "BANKNIFTY",
                "entry_time": "11:15",
                "pnl": 1100.0,
                "status": "OPEN",
            },
        ],
        "signals": {
            "oi_analysis": 0.65,
            "iv_analysis": 0.45,
            "max_pain": 0.30,
            "vix": -0.20,
            "price_action": 0.55,
            "technicals": 0.40,
        },
        "regime": "RANGE_BOUND",
        "vix": 13.5,
        "spot": 22150.50,
    }


def create_dashboard() -> None:
    """Create the Streamlit dashboard."""
    st.set_page_config(
        page_title="NSE Options Bot",
        page_icon="📈",
        layout="wide",
    )

    st.title("📈 NSE Options Trading Bot")
    st.caption("Paper Trading Dashboard")

    # Get data
    data = get_sample_data()

    # Top metrics row
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "Daily P&L",
            f"₹{data['portfolio']['daily_pnl']:,.0f}",
            delta=f"{data['portfolio']['daily_pnl']/5000*100:.1f}%",
        )

    with col2:
        st.metric(
            "Total P&L",
            f"₹{data['portfolio']['total_pnl']:,.0f}",
        )

    with col3:
        st.metric(
            "Open Trades",
            data['portfolio']['open_trades'],
        )

    with col4:
        st.metric(
            "NIFTY",
            f"{data['spot']:,.2f}",
            delta="0.5%",
        )

    with col5:
        st.metric(
            "India VIX",
            f"{data['vix']:.2f}",
            delta="-0.3",
        )

    st.divider()

    # Main content area
    left_col, right_col = st.columns([2, 1])

    with left_col:
        st.subheader("📊 Open Positions")

        if data['trades']:
            trades_df = pd.DataFrame(data['trades'])
            trades_df['pnl'] = trades_df['pnl'].apply(lambda x: f"₹{x:+,.0f}")

            st.dataframe(
                trades_df,
                column_config={
                    "id": "Trade ID",
                    "strategy": "Strategy",
                    "underlying": "Symbol",
                    "entry_time": "Entry",
                    "pnl": st.column_config.TextColumn("P&L"),
                    "status": "Status",
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("No open positions")

        st.subheader("📈 P&L Chart")

        # Sample P&L data
        pnl_data = pd.DataFrame({
            "time": pd.date_range(start="09:15", periods=24, freq="15min"),
            "pnl": [0, 500, 800, 600, 1200, 1500, 1800, 2000, 2200, 2500,
                   2800, 3000, 2800, 3200, 3500, 3200, 3000, 3100, 3200,
                   3100, 3000, 3100, 3200, 3200],
        })

        st.line_chart(pnl_data.set_index("time"))

    with right_col:
        st.subheader("🎯 Signal Strength")

        for signal_name, value in data['signals'].items():
            # Create progress bar
            normalized = (value + 1) / 2  # Convert -1 to 1 range to 0 to 1
            color = "green" if value > 0 else "red" if value < 0 else "gray"

            st.write(f"**{signal_name.replace('_', ' ').title()}**")
            st.progress(normalized, text=f"{value:+.2f}")

        st.divider()

        st.subheader("🔄 Market Regime")

        regime = data['regime']
        regime_colors = {
            "RANGE_BOUND": "🟢",
            "TRENDING_UP": "🔵",
            "TRENDING_DOWN": "🔴",
            "HIGH_VOLATILITY": "🟡",
        }

        st.write(f"### {regime_colors.get(regime, '⚪')} {regime}")

        st.divider()

        st.subheader("💰 Margin")

        margin_used = data['portfolio']['margin_used']
        margin_total = margin_used + data['portfolio']['margin_available']
        margin_pct = margin_used / margin_total * 100

        st.progress(margin_pct / 100, text=f"₹{margin_used:,.0f} / ₹{margin_total:,.0f}")

    # Bottom section
    st.divider()

    tab1, tab2, tab3 = st.tabs(["📝 Recent Orders", "📊 Greeks", "⚙️ Settings"])

    with tab1:
        orders_data = [
            {"time": "11:15:32", "symbol": "NIFTY24D1922200CE", "type": "SELL",
             "qty": 50, "price": 125.50, "status": "FILLED"},
            {"time": "11:15:31", "symbol": "NIFTY24D1922200PE", "type": "SELL",
             "qty": 50, "price": 118.25, "status": "FILLED"},
            {"time": "10:30:15", "symbol": "NIFTY24D1922100CE", "type": "SELL",
             "qty": 50, "price": 185.00, "status": "FILLED"},
        ]

        st.dataframe(
            pd.DataFrame(orders_data),
            hide_index=True,
            use_container_width=True,
        )

    with tab2:
        greeks_data = {
            "Position": ["NIFTY Straddle", "BANKNIFTY Condor", "Total"],
            "Delta": [15.2, -8.5, 6.7],
            "Gamma": [2.1, 1.2, 3.3],
            "Theta": [-450, -280, -730],
            "Vega": [120, 85, 205],
        }

        st.dataframe(
            pd.DataFrame(greeks_data),
            hide_index=True,
            use_container_width=True,
        )

    with tab3:
        st.write("**Trading Settings**")

        col1, col2 = st.columns(2)

        with col1:
            st.toggle("Paper Trading Mode", value=True)
            st.slider("Max Lots per Trade", 1, 20, 5)
            st.slider("Max Daily Loss %", 1.0, 10.0, 5.0)

        with col2:
            st.toggle("Auto-Trading Enabled", value=False)
            st.selectbox("Default Strategy", ["SHORT_STRADDLE", "IRON_CONDOR", "BULL_CALL_SPREAD"])
            st.time_input("No Entry After", datetime.strptime("15:15", "%H:%M").time())

    # Sidebar
    with st.sidebar:
        st.header("🤖 Bot Status")

        st.write("**Mode:** Paper Trading")
        st.write(f"**Started:** {datetime.now().strftime('%H:%M')}")
        st.write("**Status:** 🟢 Running")

        st.divider()

        st.header("📊 Today's Stats")

        st.write(f"**Trades:** 5")
        st.write(f"**Win Rate:** 80%")
        st.write(f"**Avg P&L:** ₹640")

        st.divider()

        if st.button("🔄 Refresh"):
            st.rerun()

        if st.button("⏹️ Stop Bot"):
            st.warning("Bot stopped!")

        if st.button("🚨 Kill Switch"):
            st.error("KILL SWITCH ACTIVATED!")


def main() -> None:
    """Main entry point."""
    create_dashboard()


if __name__ == "__main__":
    main()
