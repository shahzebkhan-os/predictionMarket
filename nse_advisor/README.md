# NSE Options Signal Advisor

A comprehensive signal-only options trading advisor for NSE indices (NIFTY, BANKNIFTY, FINNIFTY). This system generates trade recommendations displayed on a dashboard and sent via Telegram - no broker execution API required.

## Features

### 🎯 12-Signal Engine
- **OI Analysis**: PCR, OI buildup, OI walls
- **IV Analysis**: IVR, IVP, IV skew, term structure
- **Max Pain & GEX**: Gamma Exposure analysis
- **India VIX**: Volatility tracking
- **Price Action**: VWAP, opening range, gaps
- **Technicals**: Supertrend, RSI, Bollinger, EMA crossover
- **Global Cues**: GIFT Nifty, SPX, DXY, crude, USD/INR
- **FII/DII Flow**: Institutional activity tracking
- **Straddle Pricing**: Expected move analysis
- **News & Events**: Economic calendar, NSE announcements
- **Market Regime**: TRENDING/RANGE_BOUND/HIGH_VOLATILITY detection
- **Greeks Composite**: Portfolio Greeks aggregation

### 📊 Strategy Recommender
- **Short Straddle**: High IVR environments
- **Iron Condor**: Range-bound markets
- **Bull Call Spread**: Directional bullish
- **Bear Put Spread**: Directional bearish
- **Long Straddle**: Pre-event volatility plays

### 📈 Paper Trading
- Virtual portfolio tracking
- Slippage simulation
- Transaction cost modeling
- Performance analytics

### 🔔 Real-time Alerts
- Telegram notifications for new signals
- Exit alerts (stop loss, target, 75% profit)
- Risk alerts (circuit breaker, VIX spike)
- Daily performance reports

### 📱 Streamlit Dashboard
- Signal Hub with 12-signal breakdown
- Option Chain visualization with OI heatmap
- Position tracking with Greeks
- Paper trading performance
- Trade postmortem analysis

## Installation

### Prerequisites
- Python 3.11+
- Redis (for caching)
- PostgreSQL (optional, SQLite for development)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/nse-advisor.git
cd nse-advisor
```

2. Install dependencies:
```bash
pip install -r requirements.txt
# or with poetry
poetry install
```

3. Configure environment:
```bash
cp .env.example .env
# Edit .env with your settings
```

4. Initialize database:
```bash
python -c "from nse_advisor.storage.db import init_database; import asyncio; asyncio.run(init_database())"
```

5. Download historical IV data:
```bash
python scripts/download_historical_iv.py --days 252
```

## Configuration

Create a `.env` file with the following settings:

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# IndMoney (optional - for portfolio sync)
INDMONEY_BEARER_TOKEN=your_token

# Capital
PAPER_CAPITAL=500000

# Database
DATABASE_URL=sqlite+aiosqlite:///nse_advisor.db

# Risk Settings
MAX_LOSS_PER_TRADE_INR=3000
MAX_LOTS_PER_TRADE_NIFTY=10
```

See `.env.example` for all available settings.

## Usage

### Running the Advisor

Start the main application:
```bash
python -m nse_advisor.main
```

This will:
- Initialize all components
- Start the option chain refresh loop
- Start the signal scanning loop
- Start the position tracker
- Launch the Streamlit dashboard
- Begin monitoring for exit conditions

### Running the Dashboard Only

```bash
streamlit run nse_advisor/dashboard/streamlit_app.py
```

Access at http://localhost:8501

### Docker Deployment

```bash
docker-compose up -d
```

This starts:
- NSE Advisor main application
- Streamlit dashboard
- Redis cache
- PostgreSQL database

## Data Sources

| Data | Source | Update Frequency |
|------|--------|------------------|
| Option Chain | NSE API | Every 5 seconds |
| Indices | NSE API | Every 5 seconds |
| F&O Ban List | NSE API | Daily at 08:30 IST |
| Holidays | NSE API | On startup |
| FII/DII | NSE API | Daily at 18:30 IST |
| Historical OHLCV | yfinance | On startup |
| Global Cues | yfinance | Every 30 minutes |
| India VIX | yfinance | Every 5 seconds |

### NSE API Notes

NSE requires browser-like headers and session cookies:
- User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)
- Cookies auto-refreshed every 25 minutes
- Retry with exponential backoff on failures

## Trading Workflow

1. **Signal Generation**: 12 signals are computed and aggregated with regime-weighted scoring

2. **Recommendation**: If composite score ≥ 0.45 and confidence ≥ 60%, a trade recommendation is generated

3. **Logging**: Recommendations are auto-logged as paper trades

4. **Monitoring**: Position tracker monitors P&L and Greeks

5. **Exit Alerts**: Telegram alerts sent for stop loss, targets, and risk events

6. **Manual Execution**: User places orders manually based on recommendations

7. **Postmortem**: Closed trades analyzed for signal accuracy and P&L attribution

## Scripts

### Backtest
Replay historical option chain snapshots:
```bash
python scripts/backtest.py --start-date 2024-01-01 --end-date 2024-12-31
```

### Download IV History
Seed historical IV data for IVR/IVP:
```bash
python scripts/download_historical_iv.py --days 252 --output iv_history.csv
```

### Validation Report
Generate paper trading validation report:
```bash
python scripts/validation_report.py --days 30 --output report.json
```

## Testing

Run tests:
```bash
pytest tests/ -v
```

Run with coverage:
```bash
pytest tests/ --cov=nse_advisor --cov-report=html
```

## API Reference

### Signal Engine
```python
from nse_advisor.signals.engine import get_signal_engine

engine = get_signal_engine()
result = await engine.scan()

print(f"Score: {result.composite_score}")
print(f"Direction: {result.direction}")
print(f"Should recommend: {result.should_recommend}")
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

# Update prices
tracker.update_prices(chain_snapshot)

# Get portfolio Greeks
greeks = tracker.get_portfolio_greeks()
print(f"Net Delta: {greeks['delta']}")
```

## Architecture

```
nse_advisor/
├── main.py                 # Entry point
├── config.py               # Pydantic settings
├── data/                   # Data fetchers
│   ├── nse_session.py      # NSE session manager
│   ├── nse_fetcher.py      # Option chain, indices
│   ├── yfinance_fetcher.py # Historical data
│   └── indmoney_client.py  # Portfolio sync
├── market/                 # Market infrastructure
│   ├── nse_calendar.py     # Holidays, expiries
│   ├── instruments.py      # Lot sizes, tokens
│   ├── option_chain.py     # Chain snapshots
│   ├── ban_list.py         # F&O ban list
│   ├── circuit_breaker.py  # Halt detection
│   └── regime.py           # Market regime
├── signals/                # 12 signal modules
├── strategies/             # Trading strategies
├── recommender/            # Trade recommendations
├── paper/                  # Paper trading
├── tracker/                # Position monitoring
├── postmortem/             # Trade analysis
├── storage/                # Database layer
├── alerts/                 # Telegram alerts
├── dashboard/              # Streamlit UI
└── tests/                  # Test suite
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## Disclaimer

This software is for educational and informational purposes only. It does not constitute financial advice. Trading options involves significant risk. Always do your own research and consult a qualified financial advisor before trading.

## License

MIT License - see LICENSE file for details.
