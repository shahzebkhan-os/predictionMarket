# Prediction Market - NSE Options Trading System

A comprehensive options trading system for India's National Stock Exchange (NSE), consisting of two main components: **NSE Advisor** (signal-only advisory) and **NSE Options Bot** (automated trading execution).

---

## 📖 Table of Contents

1. [Project Overview](#project-overview)
2. [System Architecture](#system-architecture)
3. [Module Descriptions](#module-descriptions)
4. [Prerequisites](#prerequisites)
5. [Installation](#installation)
6. [Configuration](#configuration)
7. [How to Execute](#how-to-execute)
8. [Data Flow](#data-flow)
9. [Signal Engine](#signal-engine)
10. [Trading Strategies](#trading-strategies)
11. [Testing](#testing)
12. [Docker Deployment](#docker-deployment)
13. [Scripts](#scripts)
14. [API Reference](#api-reference)
15. [Disclaimer](#disclaimer)

---

## 🎯 Project Overview

This repository contains a Python-based options trading system designed for NSE indices (NIFTY, BANKNIFTY, FINNIFTY). The system provides:

### What This System Does

| Feature | Description |
|---------|-------------|
| **Signal Generation** | 12-signal composite engine analyzing OI, IV, price action, technicals, and global cues |
| **Strategy Recommendation** | Suggests optimal options strategies based on market regime |
| **Paper Trading** | Virtual portfolio with realistic slippage and transaction cost simulation |
| **Real-time Alerts** | Telegram notifications for signals, exits, and risk events |
| **Live Dashboard** | Streamlit-based UI for monitoring signals and positions |
| **Automated Execution** | Optional broker integration via Kite Connect API |

### Key Concepts for AI Understanding

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PREDICTION MARKET SYSTEM                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  [Data Sources]                                                      │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐          │
│  │ NSE API     │ →  │ 12-Signal    │ →  │ Strategy       │          │
│  │ yfinance    │    │ Engine       │    │ Recommender    │          │
│  │ Global Cues │    └──────────────┘    └────────────────┘          │
│  └─────────────┘           │                    │                   │
│                            ▼                    ▼                   │
│                    ┌───────────────────────────────────┐            │
│                    │ Trade Recommendation              │            │
│                    │ - Entry/Exit strikes              │            │
│                    │ - Lot sizing (Kelly criterion)    │            │
│                    │ - Stop loss / Target levels       │            │
│                    └───────────────────────────────────┘            │
│                                    │                                │
│                    ┌───────────────┴───────────────┐                │
│                    ▼                               ▼                │
│           ┌───────────────┐               ┌───────────────┐        │
│           │ nse_advisor   │               │ nse_options_bot│        │
│           │ (Signal Only) │               │ (Execution)    │        │
│           │               │               │                │        │
│           │ • Dashboard   │               │ • Kite API     │        │
│           │ • Telegram    │               │ • Order Mgmt   │        │
│           │ • Paper Trade │               │ • Live Trading │        │
│           └───────────────┘               └───────────────┘        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🏗️ System Architecture

### Directory Structure

```
predictionMarket/
├── .env.example              # Environment template with all config options
├── pyproject.toml            # Python project configuration (pip/poetry)
├── requirements.txt          # Pip requirements file
│
├── nse_advisor/              # SIGNAL-ONLY ADVISOR (no execution)
│   ├── main.py               # Entry point - orchestrates all loops
│   ├── config.py             # Pydantic settings (all thresholds)
│   ├── docker-compose.yml    # Docker setup with Redis/PostgreSQL
│   │
│   ├── data/                 # Data fetchers
│   │   ├── nse_session.py    # NSE session/cookie management
│   │   ├── nse_fetcher.py    # Option chain, indices fetching
│   │   └── yfinance_fetcher.py # Historical OHLCV, VIX
│   │
│   ├── market/               # Market infrastructure
│   │   ├── nse_calendar.py   # Holidays, expiry dates
│   │   ├── instruments.py    # Lot sizes, instrument tokens
│   │   ├── option_chain.py   # Chain snapshots with Greeks
│   │   ├── ban_list.py       # F&O ban list checker
│   │   ├── circuit_breaker.py # Market halt detection
│   │   └── regime.py         # TRENDING/RANGE_BOUND/HIGH_VOL
│   │
│   ├── signals/              # 12-signal modules
│   │   ├── engine.py         # Signal aggregator
│   │   ├── oi_analysis.py    # PCR, OI buildup, walls
│   │   ├── iv_analysis.py    # IVR, IVP, skew, term structure
│   │   ├── max_pain.py       # Max pain calculation
│   │   ├── technicals.py     # Supertrend, RSI, BB, EMA
│   │   ├── price_action.py   # VWAP, opening range, gaps
│   │   ├── global_cues.py    # GIFT Nifty, SPX, DXY
│   │   ├── fii_dii.py        # FII/DII flow tracking
│   │   ├── straddle_pricing.py # Expected move analysis
│   │   ├── greeks_signal.py  # Portfolio Greeks composite
│   │   └── news_scanner.py   # Economic calendar, events
│   │
│   ├── strategies/           # Trading strategies
│   │   ├── short_straddle.py # Sell ATM CE + PE
│   │   ├── iron_condor.py    # Bull put + Bear call spreads
│   │   ├── bull_call_spread.py
│   │   ├── bear_put_spread.py
│   │   └── long_straddle.py  # Pre-event volatility plays
│   │
│   ├── recommender/          # Trade recommendation engine
│   ├── paper/                # Paper trading simulation
│   ├── tracker/              # Position monitoring + exits
│   ├── postmortem/           # Trade analysis
│   ├── storage/              # SQLAlchemy models, event log
│   ├── alerts/               # Telegram integration
│   ├── dashboard/            # Streamlit UI
│   └── tests/                # Test suite
│
├── nse_options_bot/          # AUTOMATED EXECUTION BOT
│   ├── main.py               # Entry point
│   ├── config.py             # Pydantic settings
│   │
│   ├── brokers/              # Broker integrations
│   │   └── kite_client.py    # Kite Connect API
│   │
│   ├── execution/            # Order management
│   │   ├── executor.py       # Order placement
│   │   ├── risk.py           # Risk limits
│   │   └── sizer.py          # Position sizing
│   │
│   ├── watcher/              # Trade monitoring
│   │   ├── watcher.py        # Exit condition checker
│   │   └── state.py          # Trade state machine
│   │
│   └── [other modules...]    # Similar structure to nse_advisor
│
└── scripts/                  # Utility scripts
    ├── backtest.py           # Signal backtesting
    ├── download_historical_iv.py # IV history seeding
    └── validation_report.py  # Paper trading validation
```

---

## 📦 Module Descriptions

### 1. `nse_advisor` - Signal-Only Advisor

**Purpose**: Generates trading signals and recommendations WITHOUT executing trades.

**Use Case**: Manual traders who want algorithmic signal generation but prefer to execute orders themselves.

**Features**:
- Fetches live option chains from NSE every 5 seconds
- Runs 12-signal composite analysis every 60 seconds
- Sends recommendations via Telegram
- Displays signals on Streamlit dashboard
- Paper trades recommendations automatically

### 2. `nse_options_bot` - Automated Execution Bot

**Purpose**: Fully automated trading bot with broker integration.

**Use Case**: Algorithmic traders who want end-to-end automated execution.

**Features**:
- Integrates with Kite Connect API (Zerodha)
- Automated order placement and management
- Position monitoring with auto-exits
- Risk management with kill switches
- Paper trading mode for testing

---

## ⚙️ Prerequisites

### Required Software

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.11+ | Runtime environment |
| Redis | 7.x | Tick data caching |
| PostgreSQL | 15+ | Production database (optional, SQLite for dev) |
| Docker | 24+ | Container deployment (optional) |

### Required Accounts

| Service | Purpose | Required For |
|---------|---------|--------------|
| Telegram Bot | Alert notifications | Both modules |
| Kite Connect | Broker API | `nse_options_bot` only |
| IndMoney | Portfolio sync | Optional cross-validation |

---

## 🚀 Installation

### Option 1: Standard Installation

```bash
# Clone repository
git clone https://github.com/yourusername/predictionMarket.git
cd predictionMarket

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev,test]"
```

### Option 2: Using pip requirements

```bash
pip install -r requirements.txt
```

### Option 3: Docker

```bash
cd nse_advisor
docker-compose up -d
```

---

## 🔧 Configuration

### Step 1: Create Environment File

```bash
cp .env.example .env
```

### Step 2: Configure Required Variables

Edit `.env` with your credentials:

```env
# === REQUIRED FOR BOTH MODULES ===

# Telegram Alerts (create bot via @BotFather)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Database (SQLite for development)
DATABASE_URL=sqlite+aiosqlite:///nse_advisor.db

# === REQUIRED FOR nse_options_bot ONLY ===

# Kite Connect API (https://kite.trade)
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret

# === OPTIONAL SETTINGS ===

# Trading Mode
BOT_MODE=paper  # "paper" or "live"

# Capital
PAPER_CAPITAL=500000

# Risk Parameters
MAX_LOSS_PER_TRADE_INR=3000
MAX_LOTS_PER_TRADE_NIFTY=10

# Signal Thresholds
MIN_COMPOSITE_SCORE=0.45
MIN_CONFIDENCE=0.60
MIN_IVR_FOR_SELLING=50.0
```

### Step 3: Initialize Database

```bash
python -c "from nse_advisor.storage.db import init_database; import asyncio; asyncio.run(init_database())"
```

### Step 4: Download Historical Data (Recommended)

```bash
python scripts/download_historical_iv.py --days 252
```

---

## ▶️ How to Execute

### Running NSE Advisor (Signal-Only)

```bash
# Start the full system (signals + dashboard)
python -m nse_advisor.main

# Or run dashboard only
streamlit run nse_advisor/dashboard/streamlit_app.py
```

**What happens**:
1. Initializes NSE session with browser-like headers
2. Downloads holiday calendar and instrument master
3. Fetches F&O ban list
4. Backfills 5 days of OHLCV data
5. Starts option chain refresh loop (every 5 seconds)
6. Starts signal scanning loop (every 60 seconds)
7. Launches Streamlit dashboard on port 8501

**Access the dashboard**: http://localhost:8501

### Running NSE Options Bot (Automated)

```bash
# Paper trading mode
BOT_MODE=paper python -m nse_options_bot.main

# Live trading mode (CAUTION!)
BOT_MODE=live python -m nse_options_bot.main
```

**What happens**:
1. Connects to broker (Paper or Kite)
2. Loads instrument data
3. Starts trading loop during market hours (09:15-15:30 IST)
4. Generates signals and executes trades automatically
5. Monitors positions with auto-exit triggers

### Docker Deployment

```bash
cd nse_advisor
docker-compose up -d

# View logs
docker-compose logs -f advisor
```

Services started:
- `advisor`: Main application
- `dashboard`: Streamlit UI (port 8501)
- `postgres`: PostgreSQL database (port 5432)
- `redis`: Redis cache (port 6379)

---

## 🔄 Data Flow

### Signal Generation Flow

```
1. NSE Option Chain API
   │
   ├─► OI Analysis ──────────┐
   ├─► IV Analysis ──────────┤
   ├─► Max Pain ─────────────┤
   ├─► Technicals ───────────┤
   ├─► Price Action ─────────┤
   ├─► Straddle Pricing ─────┤──► Signal Aggregator
   ├─► Greeks Composite ─────┤     │
   ├─► India VIX ────────────┤     │
   ├─► Global Cues ──────────┤     ▼
   ├─► FII/DII Flow ─────────┤  Regime-Weighted Score
   └─► News/Events ──────────┘     │
                                   ▼
                            Strategy Recommender
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              Short Straddle  Iron Condor   Directional
               (IVR > 50)   (Range Bound)    Spreads
```

### Trade Lifecycle

```
SIGNAL
  │
  ▼
RECOMMENDATION ──► [Telegram Alert]
  │
  ▼
PAPER/LIVE ORDER
  │
  ▼
POSITION TRACKING ◄──┐
  │                   │
  ├─► Update Prices (5s loop)
  │                   │
  └─► Update Greeks (60s loop)
  │
  ▼
EXIT CONDITIONS
  ├─► Stop Loss Hit
  ├─► Target Reached (75% of max profit)
  ├─► Delta Hedge Needed
  ├─► Time-based Exit (3:00 PM cutoff)
  └─► Manual Exit
  │
  ▼
POSTMORTEM ANALYSIS
```

---

## 📊 Signal Engine

The system uses a 12-signal composite engine with regime-adaptive weighting:

### Signal Components

| Signal | Description | Weight (Range) | Weight (Trend) |
|--------|-------------|----------------|----------------|
| OI Analysis | PCR, OI buildup, walls | 20% | 15% |
| IV Analysis | IVR, IVP, skew | 20% | 7% |
| Max Pain | Distance to max pain | 15% | - |
| Straddle | Expected move | 15% | - |
| Greeks | Portfolio Greeks | 10% | - |
| Price Action | VWAP, gaps, ranges | 8% | 25% |
| Technicals | Supertrend, RSI, BB | 5% | 20% |
| VIX | Volatility level | 7% | 8% |
| Global Cues | SPX, GIFT, DXY | - | 15% |
| FII/DII | Institutional flow | - | 10% |

### Market Regimes

```
RANGE_BOUND ──► Favor credit strategies (Short Straddle, Iron Condor)
TRENDING    ──► Favor directional spreads
HIGH_VOL    ──► Reduce position sizes, widen stops
```

---

## 📈 Trading Strategies

### Available Strategies

| Strategy | Direction | Risk Profile | Best Regime |
|----------|-----------|--------------|-------------|
| Short Straddle | Neutral | Undefined | Range Bound + High IVR |
| Iron Condor | Neutral | Defined | Range Bound |
| Bull Call Spread | Bullish | Defined | Trending Up |
| Bear Put Spread | Bearish | Defined | Trending Down |
| Long Straddle | Neutral | Defined | Pre-Event |

### Position Sizing

Uses Kelly Criterion with configurable fraction:
```
Position Size = (Kelly Fraction × Edge) / Odds
```

Default: `KELLY_FRACTION=0.5` (half-Kelly for safety)

---

## 🧪 Testing

### Run Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=nse_advisor --cov-report=html

# Specific module
pytest nse_advisor/tests/ -v
```

### Backtest Signals

```bash
python scripts/backtest.py \
    --start-date 2024-01-01 \
    --end-date 2024-12-31 \
    --output backtest_results.json
```

### Validation Report

```bash
python scripts/validation_report.py --days 30 --output report.json
```

---

## 🐳 Docker Deployment

### Quick Start

```bash
cd nse_advisor
docker-compose up -d
```

### Services

| Service | Port | Purpose |
|---------|------|---------|
| advisor | - | Main application |
| dashboard | 8501 | Streamlit UI |
| postgres | 5432 | PostgreSQL database |
| redis | 6379 | Cache layer |

### Environment Variables (Docker)

Pass via docker-compose environment section or `.env` file:
```yaml
environment:
  - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
  - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
  - PAPER_CAPITAL=500000
```

---

## 🔧 Scripts

### `scripts/backtest.py`
Replay historical option chain snapshots to test signal accuracy.

```bash
python scripts/backtest.py --start-date 2024-01-01 --end-date 2024-12-31
```

### `scripts/download_historical_iv.py`
Seed 252 days of India VIX history for IVR/IVP calculation.

```bash
python scripts/download_historical_iv.py --days 252 --output iv_history.csv
```

### `scripts/validation_report.py`
Generate paper trading performance report.

```bash
python scripts/validation_report.py --days 30
```

---

## 📚 API Reference

### Signal Engine

```python
from nse_advisor.signals.engine import get_signal_engine

engine = get_signal_engine()
result = await engine.scan()

print(f"Score: {result.composite_score}")        # 0.0 - 1.0
print(f"Confidence: {result.composite_confidence}")  # 0.0 - 1.0
print(f"Direction: {result.direction}")          # BULLISH/BEARISH/NEUTRAL
print(f"Should Recommend: {result.should_recommend}")  # True/False
```

### Trade Recommendation

```python
from nse_advisor.recommender.engine import get_recommender_engine

recommender = get_recommender_engine()
rec = await recommender.generate_recommendation(
    underlying="NIFTY",
    aggregated_signal=result,
)

for leg in rec.legs:
    print(f"{leg.action} {leg.tradingsymbol} @{leg.suggested_entry_price}")
```

### Position Tracking

```python
from nse_advisor.tracker.position_tracker import get_position_tracker

tracker = get_position_tracker()
tracker.add_trade(trade)

# Get portfolio Greeks
greeks = tracker.get_portfolio_greeks()
print(f"Net Delta: {greeks['delta']}")
print(f"Net Theta: {greeks['theta']}")
```

---

## ⚠️ Disclaimer

**This software is for educational and informational purposes only.**

- It does NOT constitute financial advice
- Trading options involves significant risk of loss
- Past performance does not guarantee future results
- Always do your own research
- Consult a qualified financial advisor before trading
- The authors are not responsible for any financial losses

---

## 📄 License

MIT License - see LICENSE file for details.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`pytest tests/ -v`)
5. Commit changes (`git commit -m 'Add amazing feature'`)
6. Push to branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

---

## 📞 Support

For issues and feature requests, please open a GitHub issue.

---

## 🔑 Quick Reference Card

```
# Start NSE Advisor (signal-only)
python -m nse_advisor.main

# Start dashboard only
streamlit run nse_advisor/dashboard/streamlit_app.py

# Start with Docker
cd nse_advisor && docker-compose up -d

# Run tests
pytest tests/ -v

# Backtest signals
python scripts/backtest.py --start-date 2024-01-01 --end-date 2024-12-31

# Download IV history
python scripts/download_historical_iv.py --days 252
```
