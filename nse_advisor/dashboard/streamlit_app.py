"""
Streamlit Dashboard.

Primary user interface for NSE Options Signal Advisor.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, date, timedelta
from typing import Any

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from zoneinfo import ZoneInfo

# Page config
st.set_page_config(
    page_title="NSE Options Signal Advisor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

IST = ZoneInfo("Asia/Kolkata")


def get_timestamp() -> str:
    """Get current IST timestamp."""
    return datetime.now(IST).strftime("%H:%M:%S IST")


def render_sidebar():
    """Render sidebar with controls and status."""
    with st.sidebar:
        st.title("🎯 NSE Advisor")
        st.caption(f"Last update: {get_timestamp()}")
        
        st.divider()
        
        # Underlying selector
        underlying = st.selectbox(
            "Underlying",
            ["NIFTY", "BANKNIFTY", "FINNIFTY"],
            index=0,
        )
        
        # Auto-refresh toggle
        auto_refresh = st.checkbox("Auto-refresh (5s)", value=True)
        
        st.divider()
        
        # Quick stats
        st.subheader("📊 Quick Stats")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Open Trades", "2")
            st.metric("Today's P&L", "₹2,450", "+12%")
        with col2:
            st.metric("Win Rate", "62%")
            st.metric("Signals", "8")
        
        st.divider()
        
        # Market status
        now = datetime.now(IST)
        is_market_hours = now.weekday() < 5 and now.hour >= 9 and now.hour < 16
        
        if is_market_hours:
            st.success("🟢 Market Open")
        else:
            st.warning("🔴 Market Closed")
        
        return underlying, auto_refresh


def render_signal_hub():
    """Render Signal Hub tab."""
    st.header("📡 Signal Hub")
    
    # Regime badge
    col1, col2, col3 = st.columns(3)
    with col1:
        st.info("**Current Regime:** RANGE_BOUND")
    with col2:
        st.success("**Composite Score:** +0.52")
    with col3:
        st.info("**Confidence:** 72%")
    
    st.divider()
    
    # Active recommendation card
    st.subheader("🎯 Active Recommendation")
    
    with st.container():
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.markdown("""
            **Strategy:** Iron Condor on NIFTY  
            **Expiry:** 26-Dec-2024 (4 DTE)  
            **Direction:** Neutral  
            **Urgency:** ACT_NOW
            
            **Legs:**
            - SELL NIFTY 24100 CE @₹85.50 (2 lots)
            - BUY NIFTY 24200 CE @₹42.25 (2 lots)
            - SELL NIFTY 23900 PE @₹78.75 (2 lots)
            - BUY NIFTY 23800 PE @₹38.50 (2 lots)
            
            **Reasoning:** Market is range-bound with positive GEX. Iron Condor 
            captures theta decay while limiting tail risk.
            """)
        
        with col2:
            st.metric("Max Profit", "₹8,600")
            st.metric("Max Loss", "₹3,400")
            st.metric("Breakeven", "23,823 - 24,177")
            
            if st.button("📝 Log This Trade", type="primary"):
                st.success("Trade logged as paper trade!")
    
    st.divider()
    
    # Signal breakdown table
    st.subheader("📊 12-Signal Breakdown")
    
    signals_data = {
        "Signal": [
            "OI Analysis", "IV Analysis", "Max Pain & GEX", "India VIX",
            "Price Action", "Technicals", "Global Cues", "FII/DII",
            "Straddle Pricing", "News & Events", "Market Regime", "Greeks"
        ],
        "Score": [0.45, 0.62, 0.38, -0.15, 0.28, 0.55, -0.08, 0.42, 0.58, 0.0, 0.52, 0.35],
        "Confidence": [0.78, 0.85, 0.72, 0.90, 0.68, 0.82, 0.55, 0.65, 0.75, 0.50, 0.88, 0.70],
        "Reason": [
            "PCR rising, OI buildup at 24200 CE",
            "IVR at 68%, favorable for selling",
            "Max Pain at 24000, positive GEX",
            "VIX at 14.2, stable conditions",
            "Price within 0.2% of VWAP",
            "Supertrend bullish, RSI neutral",
            "GIFT premium +0.1%, SPX flat",
            "FII net buyers in cash, index futures",
            "Straddle overpriced vs HV20",
            "No major events today",
            "Range-bound regime detected",
            "Portfolio delta near neutral"
        ],
    }
    
    df = pd.DataFrame(signals_data)
    
    # Color code scores
    def color_score(val):
        if val > 0.3:
            return 'background-color: #c6efce'
        elif val < -0.3:
            return 'background-color: #ffc7ce'
        return ''
    
    st.dataframe(
        df.style.applymap(color_score, subset=['Score']),
        use_container_width=True,
        hide_index=True,
    )


def render_option_chain():
    """Render Option Chain tab."""
    st.header("📈 Option Chain Analysis")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Spot Price", "24,052.75", "+0.32%")
    with col2:
        st.metric("ATM Strike", "24,050")
    with col3:
        st.metric("PCR", "1.12", "+0.08")
    with col4:
        st.metric("Max Pain", "24,000")
    
    st.divider()
    
    # OI Heatmap
    st.subheader("🔥 OI Heatmap")
    
    # Sample data
    strikes = list(range(23800, 24300, 50))
    ce_oi = [125000, 189000, 245000, 312000, 425000, 356000, 278000, 198000, 145000, 89000]
    pe_oi = [89000, 145000, 198000, 278000, 356000, 425000, 312000, 245000, 189000, 125000]
    
    fig = go.Figure()
    fig.add_trace(go.Bar(name='CE OI', x=strikes, y=ce_oi, marker_color='red', opacity=0.7))
    fig.add_trace(go.Bar(name='PE OI', x=strikes, y=pe_oi, marker_color='green', opacity=0.7))
    fig.add_vline(x=24050, line_dash="dash", line_color="blue", annotation_text="Spot")
    fig.add_vline(x=24000, line_dash="dot", line_color="purple", annotation_text="Max Pain")
    fig.update_layout(barmode='group', height=400, title="OI by Strike")
    
    st.plotly_chart(fig, use_container_width=True)
    
    # IV Skew
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📉 IV Skew")
        iv_skew_data = {
            'Strike': strikes,
            'CE IV': [18.5, 17.8, 16.9, 15.8, 15.2, 15.5, 16.2, 17.1, 18.0, 19.2],
            'PE IV': [19.8, 18.5, 17.2, 16.1, 15.2, 15.8, 16.8, 17.9, 19.1, 20.5],
        }
        df_iv = pd.DataFrame(iv_skew_data)
        fig_iv = px.line(df_iv, x='Strike', y=['CE IV', 'PE IV'], title="IV Skew Curve")
        st.plotly_chart(fig_iv, use_container_width=True)
    
    with col2:
        st.subheader("📊 GEX Analysis")
        gex_data = {
            'Strike': strikes,
            'GEX': [15000, 28000, 45000, 62000, 85000, -45000, -32000, -18000, -8000, -2000],
        }
        df_gex = pd.DataFrame(gex_data)
        colors = ['green' if x > 0 else 'red' for x in df_gex['GEX']]
        fig_gex = go.Figure(go.Bar(x=df_gex['Strike'], y=df_gex['GEX'], marker_color=colors))
        fig_gex.update_layout(title="Gamma Exposure by Strike", height=350)
        st.plotly_chart(fig_gex, use_container_width=True)
    
    # PCR Trend
    st.subheader("📈 PCR Trend (Last 30 Snapshots)")
    pcr_data = {
        'Time': pd.date_range(end=datetime.now(), periods=30, freq='5T'),
        'PCR': [1.05 + 0.02 * i + 0.01 * (i % 3) for i in range(30)],
    }
    df_pcr = pd.DataFrame(pcr_data)
    fig_pcr = px.line(df_pcr, x='Time', y='PCR', title="PCR Evolution")
    fig_pcr.add_hline(y=1.0, line_dash="dash", annotation_text="Neutral")
    st.plotly_chart(fig_pcr, use_container_width=True)


def render_open_positions():
    """Render Open Positions tab."""
    st.header("📋 Open Positions")
    
    # Position table
    positions = [
        {
            "ID": "T001",
            "Strategy": "Iron Condor",
            "Underlying": "NIFTY",
            "Entry": "22-Dec 10:15",
            "P&L": 2450,
            "P&L %": "+42%",
            "DTE": 4,
            "Delta": -12,
            "Theta": 185,
            "Status": "🟢 On track",
        },
        {
            "ID": "T002",
            "Strategy": "Bull Call Spread",
            "Underlying": "BANKNIFTY",
            "Entry": "21-Dec 14:30",
            "P&L": -850,
            "P&L %": "-15%",
            "DTE": 2,
            "Delta": 45,
            "Theta": -42,
            "Status": "🟡 Monitor",
        },
    ]
    
    df = pd.DataFrame(positions)
    
    # Color code P&L
    def color_pnl(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return 'color: green'
            elif val < 0:
                return 'color: red'
        return ''
    
    st.dataframe(
        df.style.applymap(color_pnl, subset=['P&L']),
        use_container_width=True,
        hide_index=True,
    )
    
    st.divider()
    
    # Exit alerts
    st.subheader("🔔 Exit Alerts")
    
    with st.expander("🟡 EXIT CONSIDER: T001 Iron Condor", expanded=True):
        st.warning("""
        **75% of max profit reached!**
        
        Current P&L: ₹2,450 (75% of ₹3,200 max)
        
        Consider exiting to lock in gains. DTE=4, time decay slowing.
        """)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Mark as Closed", key="close_t001"):
                st.success("Trade marked as closed")
        with col2:
            if st.button("⏳ Keep Open", key="keep_t001"):
                st.info("Alert dismissed")
    
    st.divider()
    
    # Portfolio Greeks
    st.subheader("📊 Portfolio Greeks")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Net Delta", "33", "Within range")
    with col2:
        st.metric("Net Gamma", "-45", "Short gamma")
    with col3:
        st.metric("Net Theta", "143", "+₹143/day")
    with col4:
        st.metric("Net Vega", "-280", "Short vol")


def render_paper_performance():
    """Render Paper Performance tab."""
    st.header("📈 Paper Trading Performance")
    
    # Summary cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Paper Capital", "₹5,00,000")
    with col2:
        st.metric("Total P&L", "₹28,450", "+5.7%")
    with col3:
        st.metric("Win Rate", "62%", "15/24 trades")
    with col4:
        st.metric("Avg P&L/Trade", "₹1,185")
    
    st.divider()
    
    # P&L Chart
    st.subheader("📈 Cumulative P&L")
    
    dates = pd.date_range(end=date.today(), periods=30, freq='D')
    pnl = [0]
    for i in range(29):
        change = 1500 if i % 3 != 0 else -800
        pnl.append(pnl[-1] + change + (i * 50))
    
    df_pnl = pd.DataFrame({'Date': dates, 'Cumulative P&L': pnl})
    fig = px.line(df_pnl, x='Date', y='Cumulative P&L', title="Paper Trading P&L")
    fig.add_hline(y=0, line_dash="dash")
    st.plotly_chart(fig, use_container_width=True)
    
    # Trade history
    st.subheader("📋 Recent Trades")
    
    trades = [
        {"Date": "22-Dec", "Strategy": "Iron Condor", "Underlying": "NIFTY", "P&L": "+₹2,450", "Verdict": "✅ GOOD_TRADE"},
        {"Date": "21-Dec", "Strategy": "Bull Call Spread", "Underlying": "BANKNIFTY", "P&L": "-₹850", "Verdict": "⚠️ BAD_IV_TIMING"},
        {"Date": "20-Dec", "Strategy": "Short Straddle", "Underlying": "NIFTY", "P&L": "+₹3,200", "Verdict": "✅ GOOD_TRADE"},
        {"Date": "19-Dec", "Strategy": "Iron Condor", "Underlying": "NIFTY", "P&L": "+₹1,800", "Verdict": "🟡 GOOD_IDEA_BAD_EXIT"},
        {"Date": "18-Dec", "Strategy": "Bear Put Spread", "Underlying": "BANKNIFTY", "P&L": "-₹1,200", "Verdict": "❌ SIGNAL_FAILURE"},
    ]
    
    st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
    
    # Signal accuracy
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🎯 Signal Accuracy")
        accuracy_data = {
            'Signal': ['OI Analysis', 'IV Analysis', 'Technicals', 'Max Pain', 'VIX'],
            'Accuracy': [68, 72, 65, 58, 75],
        }
        df_acc = pd.DataFrame(accuracy_data)
        fig_acc = px.bar(df_acc, x='Signal', y='Accuracy', title="Signal Hit Rate (%)")
        fig_acc.add_hline(y=60, line_dash="dash", annotation_text="Target: 60%")
        st.plotly_chart(fig_acc, use_container_width=True)
    
    with col2:
        st.subheader("📊 Regime Performance")
        regime_data = {
            'Regime': ['RANGE_BOUND', 'TRENDING_UP', 'TRENDING_DOWN', 'HIGH_VOL'],
            'Win Rate': [72, 58, 55, 45],
        }
        df_regime = pd.DataFrame(regime_data)
        fig_regime = px.bar(df_regime, x='Regime', y='Win Rate', title="Win Rate by Regime (%)",
                          color='Win Rate', color_continuous_scale='RdYlGn')
        st.plotly_chart(fig_regime, use_container_width=True)


def render_postmortem():
    """Render Postmortem tab."""
    st.header("🔍 Trade Postmortem")
    
    # Trade selector
    trade_options = ["T001 - Iron Condor (22-Dec)", "T002 - Bull Call Spread (21-Dec)", "T003 - Short Straddle (20-Dec)"]
    selected_trade = st.selectbox("Select Trade", trade_options)
    
    st.divider()
    
    # Trade details
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("📋 Trade Details")
        st.markdown("""
        **Strategy:** Iron Condor  
        **Underlying:** NIFTY  
        **Entry:** 22-Dec-2024 10:15 IST  
        **Exit:** 24-Dec-2024 14:30 IST  
        **Regime at Entry:** RANGE_BOUND
        
        **Legs:**
        - SELL 24100 CE @₹85.50 → Exit @₹32.25
        - BUY 24200 CE @₹42.25 → Exit @₹15.50
        - SELL 23900 PE @₹78.75 → Exit @₹28.50
        - BUY 23800 PE @₹38.50 → Exit @₹12.25
        """)
    
    with col2:
        st.subheader("📊 P&L Summary")
        st.metric("Realized P&L", "₹2,450")
        st.metric("Max Favorable Excursion", "₹2,850")
        st.metric("Max Adverse Excursion", "-₹350")
        st.metric("Exit Quality", "86%")
    
    st.divider()
    
    # Greeks P&L Attribution
    st.subheader("📈 Greeks P&L Attribution")
    
    greeks_data = {
        'Component': ['Delta', 'Theta', 'Vega', 'Gamma', 'Residual', 'Total'],
        'P&L': [450, 1850, -280, 180, 250, 2450],
    }
    df_greeks = pd.DataFrame(greeks_data)
    
    # Waterfall chart
    fig = go.Figure(go.Waterfall(
        name="P&L Attribution",
        orientation="v",
        measure=["relative", "relative", "relative", "relative", "relative", "total"],
        x=df_greeks['Component'],
        y=df_greeks['P&L'],
        textposition="outside",
        connector={"line": {"color": "rgb(63, 63, 63)"}},
    ))
    fig.update_layout(title="Greeks P&L Waterfall", height=400)
    st.plotly_chart(fig, use_container_width=True)
    
    # Signal accuracy for this trade
    st.subheader("🎯 Signal Accuracy (This Trade)")
    
    signal_acc = {
        'Signal': ['OI Analysis', 'IV Analysis', 'Max Pain', 'Technicals', 'Straddle'],
        'Entry Score': [0.45, 0.62, 0.38, 0.55, 0.58],
        'Predicted': ['Bullish', 'Sell Premium', 'Range', 'Bullish', 'Overpriced'],
        'Actual': ['Correct', 'Correct', 'Correct', 'Partially', 'Correct'],
        'Accuracy': ['✅', '✅', '✅', '🟡', '✅'],
    }
    st.dataframe(pd.DataFrame(signal_acc), use_container_width=True, hide_index=True)
    
    # Verdict
    st.subheader("📜 Verdict")
    st.success("""
    **✅ GOOD_TRADE**
    
    Trade executed well with positive outcome. Entry was timed correctly during 
    range-bound conditions with elevated IV. Exit captured 86% of maximum favorable 
    excursion. Theta contributed 75% of profits as expected for this strategy.
    """)


def main():
    """Main dashboard application."""
    # Sidebar
    underlying, auto_refresh = render_sidebar()
    
    # Main tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📡 Signal Hub",
        "📈 Option Chain",
        "📋 Open Positions",
        "📊 Paper Performance",
        "🔍 Postmortem",
    ])
    
    with tab1:
        render_signal_hub()
    
    with tab2:
        render_option_chain()
    
    with tab3:
        render_open_positions()
    
    with tab4:
        render_paper_performance()
    
    with tab5:
        render_postmortem()
    
    # Auto-refresh
    if auto_refresh:
        import time
        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
