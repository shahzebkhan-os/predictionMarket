"""Tests for NSE Options Trading Bot.

Basic test suite for core functionality.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime, time
from decimal import Decimal

import pytz

IST = pytz.timezone("Asia/Kolkata")


# =============================================================================
# Config Tests
# =============================================================================


def test_settings_defaults():
    """Test Settings default values."""
    from nse_options_bot.config import Settings

    settings = Settings(
        kite_api_key="test",
        kite_api_secret="test",
    )

    assert settings.paper_trading is True
    assert settings.initial_capital == Decimal("500000")
    assert settings.max_daily_loss_pct == 5.0


# =============================================================================
# Calendar Tests
# =============================================================================


def test_nse_calendar_trading_day():
    """Test NSE calendar trading day detection."""
    from nse_options_bot.market.nse_calendar import NseCalendar

    calendar = NseCalendar()

    # Weekdays should generally be trading days (unless holiday)
    monday = date(2024, 12, 2)  # Monday
    saturday = date(2024, 12, 7)  # Saturday
    sunday = date(2024, 12, 8)  # Sunday

    assert calendar.is_trading_day(monday) is True
    assert calendar.is_trading_day(saturday) is False
    assert calendar.is_trading_day(sunday) is False


def test_nse_calendar_market_hours():
    """Test NSE calendar market hours."""
    from nse_options_bot.market.nse_calendar import NseCalendar

    calendar = NseCalendar()

    # Create datetime objects
    trading_day = date(2024, 12, 2)  # Monday

    # During market hours
    during_market = datetime.combine(trading_day, time(10, 30))
    during_market = IST.localize(during_market)

    # Before market
    before_market = datetime.combine(trading_day, time(9, 0))
    before_market = IST.localize(before_market)

    # After market
    after_market = datetime.combine(trading_day, time(16, 0))
    after_market = IST.localize(after_market)

    assert calendar.is_market_open(during_market) is True
    assert calendar.is_market_open(before_market) is False
    assert calendar.is_market_open(after_market) is False


# =============================================================================
# Strategy Tests
# =============================================================================


def test_short_straddle_legs():
    """Test Short Straddle leg building."""
    from nse_options_bot.strategies.short_straddle import ShortStraddle
    from nse_options_bot.brokers.base import OptionType, TransactionType

    expiry = date(2024, 12, 5)
    strategy = ShortStraddle(
        underlying="NIFTY",
        expiry=expiry,
        lot_size=25,
    )

    legs = strategy.build_legs(
        spot_price=Decimal("22000"),
        quantity=2,
    )

    assert len(legs) == 2

    # Should have one CE and one PE
    ce_legs = [l for l in legs if l.option_type == OptionType.CE]
    pe_legs = [l for l in legs if l.option_type == OptionType.PE]

    assert len(ce_legs) == 1
    assert len(pe_legs) == 1

    # Both should be SELL
    assert all(l.transaction_type == TransactionType.SELL for l in legs)

    # Same strike (ATM)
    assert ce_legs[0].strike == pe_legs[0].strike


def test_iron_condor_legs():
    """Test Iron Condor leg building."""
    from nse_options_bot.strategies.iron_condor import IronCondor
    from nse_options_bot.brokers.base import OptionType, TransactionType

    expiry = date(2024, 12, 5)
    strategy = IronCondor(
        underlying="NIFTY",
        expiry=expiry,
        lot_size=25,
    )

    legs = strategy.build_legs(
        spot_price=Decimal("22000"),
        quantity=1,
    )

    assert len(legs) == 4

    # Should have 2 CE and 2 PE
    ce_legs = [l for l in legs if l.option_type == OptionType.CE]
    pe_legs = [l for l in legs if l.option_type == OptionType.PE]

    assert len(ce_legs) == 2
    assert len(pe_legs) == 2

    # Should have 2 buys and 2 sells
    buy_legs = [l for l in legs if l.transaction_type == TransactionType.BUY]
    sell_legs = [l for l in legs if l.transaction_type == TransactionType.SELL]

    assert len(buy_legs) == 2
    assert len(sell_legs) == 2


def test_bull_call_spread_max_profit_loss():
    """Test Bull Call Spread max profit/loss calculation."""
    from nse_options_bot.strategies.bull_call_spread import BullCallSpread
    from nse_options_bot.brokers.base import TransactionType

    expiry = date(2024, 12, 5)
    strategy = BullCallSpread(
        underlying="NIFTY",
        expiry=expiry,
        lot_size=25,
    )

    legs = strategy.build_legs(
        spot_price=Decimal("22000"),
        quantity=1,
        long_strike=Decimal("22000"),
        short_strike=Decimal("22100"),
    )

    # Set entry prices
    for leg in legs:
        if leg.transaction_type == TransactionType.BUY:
            leg.entry_price = Decimal("150")  # Buy at 150
        else:
            leg.entry_price = Decimal("100")  # Sell at 100

    max_profit = strategy.calculate_max_profit(legs)
    max_loss = strategy.calculate_max_loss(legs)

    # Net debit = 150 - 100 = 50
    # Max profit = (22100 - 22000 - 50) * 25 = 1250
    # Max loss = 50 * 25 = 1250

    assert max_profit == Decimal("1250")
    assert max_loss == Decimal("1250")


# =============================================================================
# Signal Tests
# =============================================================================


def test_signal_creation():
    """Test signal creation."""
    from nse_options_bot.signals.engine import SignalType, create_signal

    signal = create_signal(
        signal_type=SignalType.OI_ANALYSIS,
        score=0.65,
        confidence=0.8,
        reason="PCR bullish",
    )

    assert signal.signal_type == SignalType.OI_ANALYSIS
    assert signal.score == 0.65
    assert signal.confidence == 0.8
    assert signal.reason == "PCR bullish"


def test_iv_analysis_ivr_calculation():
    """Test IVR calculation."""
    from nse_options_bot.signals.iv_analysis import IVAnalyzer

    analyzer = IVAnalyzer()

    # IVR = (current - 52wk_low) / (52wk_high - 52wk_low) * 100
    ivr = analyzer.calculate_ivr(
        current_iv=15.0,
        high_52w=20.0,
        low_52w=10.0,
    )

    # (15 - 10) / (20 - 10) * 100 = 50
    assert ivr == 50.0


def test_max_pain_calculation():
    """Test max pain calculation."""
    from nse_options_bot.signals.max_pain import MaxPainCalculator
    from decimal import Decimal

    calculator = MaxPainCalculator()

    # Simple test data
    chain_data = {
        Decimal("22000"): {"ce_oi": 100000, "pe_oi": 50000},
        Decimal("22100"): {"ce_oi": 80000, "pe_oi": 70000},
        Decimal("22200"): {"ce_oi": 60000, "pe_oi": 100000},
    }

    max_pain = calculator.calculate(
        chain_data=chain_data,
        spot_price=Decimal("22100"),
    )

    # Max pain should be one of the strikes
    assert max_pain in [Decimal("22000"), Decimal("22100"), Decimal("22200")]


# =============================================================================
# Paper Trading Tests
# =============================================================================


def test_paper_ledger():
    """Test paper trading ledger."""
    from nse_options_bot.paper.paper_ledger import PaperLedger

    ledger = PaperLedger(initial_capital=Decimal("500000"))

    assert ledger.cash == Decimal("500000")
    assert ledger.realized_pnl == Decimal("0")

    # Add margin
    ledger.add_margin(Decimal("50000"))
    assert ledger.margin_used == Decimal("50000")
    assert ledger.available_cash == Decimal("450000")

    # Release margin with profit
    ledger.release_margin(Decimal("50000"), Decimal("5000"))
    assert ledger.margin_used == Decimal("0")
    assert ledger.realized_pnl == Decimal("5000")


def test_slippage_model():
    """Test slippage model."""
    from nse_options_bot.paper.slippage_model import SlippageModel

    model = SlippageModel()

    # ATM options should have less slippage
    atm_slippage = model.calculate(
        symbol="NIFTY24D1922000CE",
        quantity=50,
        order_type="LIMIT",
        is_buy=True,
        distance_from_atm=0,
    )

    # OTM options should have more slippage
    otm_slippage = model.calculate(
        symbol="NIFTY24D1923000CE",
        quantity=50,
        order_type="LIMIT",
        is_buy=True,
        distance_from_atm=1000,
    )

    # Market orders should have more slippage than limit
    market_slippage = model.calculate(
        symbol="NIFTY24D1922000CE",
        quantity=50,
        order_type="MARKET",
        is_buy=True,
        distance_from_atm=0,
    )

    assert otm_slippage > atm_slippage
    assert market_slippage > atm_slippage


# =============================================================================
# Risk Tests
# =============================================================================


def test_risk_manager_entry_check():
    """Test risk manager entry checks."""
    from nse_options_bot.execution.risk import RiskManager, RiskLimits
    from nse_options_bot.strategies.base_strategy import StrategyType

    risk_manager = RiskManager(
        capital=Decimal("500000"),
        limits=RiskLimits(
            max_capital_per_trade_pct=5.0,
            max_loss_per_trade_pct=2.0,
        ),
    )

    # Should allow reasonable trade
    allowed, reason = risk_manager.check_entry_allowed(
        strategy_type=StrategyType.SHORT_STRADDLE,
        required_margin=Decimal("20000"),
        max_loss=Decimal("5000"),
        num_lots=2,
        underlying="NIFTY",
    )

    assert allowed is True

    # Should reject oversized trade
    allowed, reason = risk_manager.check_entry_allowed(
        strategy_type=StrategyType.SHORT_STRADDLE,
        required_margin=Decimal("100000"),  # 20% of capital
        max_loss=Decimal("50000"),
        num_lots=10,
        underlying="NIFTY",
    )

    assert allowed is False
    assert "exceeds" in reason.lower()


def test_position_sizer_kelly():
    """Test Kelly criterion sizing."""
    from nse_options_bot.execution.sizer import PositionSizer

    sizer = PositionSizer(
        capital=Decimal("500000"),
        max_loss_pct_per_trade=2.0,
    )

    # Test Kelly calculation
    kelly = sizer.calculate_kelly(
        win_rate=0.6,
        reward_risk_ratio=1.5,
    )

    # Kelly should be positive for edge
    assert kelly > 0
    assert kelly < 0.5  # Should be capped


# =============================================================================
# Watcher Tests
# =============================================================================


def test_exit_condition_stop_loss():
    """Test stop loss exit condition."""
    from nse_options_bot.watcher.exits import ExitConditionChecker
    from nse_options_bot.watcher.state import OptionsTradeState, TradeStatus, ExitReason

    checker = ExitConditionChecker()

    trade = OptionsTradeState(
        trade_id="T001",
        strategy_type="SHORT_STRADDLE",
        underlying="NIFTY",
        expiry_date="2024-12-05",
        status=TradeStatus.OPEN,
        max_loss_amount=Decimal("10000"),
        stop_loss_pct=50.0,
    )

    # Mock P&L to trigger stop loss
    # Need to add a leg with negative P&L
    from nse_options_bot.watcher.state import TradeLegState

    leg = TradeLegState(
        leg_id="L1",
        tradingsymbol="NIFTY24D1922000CE",
        entry_price=Decimal("100"),
        current_price=Decimal("200"),  # Loss for short
        is_long=False,
        quantity=50,
    )
    trade.add_leg(leg)

    signals = checker.check_all_conditions(trade)

    # Should have stop loss signal
    sl_signals = [s for s in signals if s.reason == ExitReason.STOP_LOSS]
    assert len(sl_signals) == 1


# =============================================================================
# Greek P&L Tests
# =============================================================================


def test_greek_pnl_attribution():
    """Test Greek P&L attribution."""
    from nse_options_bot.postmortem.greek_pnl import quick_greek_attribution

    breakdown = quick_greek_attribution(
        entry_spot=Decimal("22000"),
        exit_spot=Decimal("22100"),
        entry_iv=15.0,
        exit_iv=14.0,
        entry_delta=0.5,
        entry_gamma=0.01,
        entry_theta=-50.0,
        entry_vega=100.0,
        days_held=1.0,
        actual_pnl=Decimal("2500"),
    )

    # Delta P&L = 0.5 * 100 = 50
    assert breakdown.delta_pnl == Decimal("50.0")

    # Theta P&L = -50 * 1 = -50
    assert breakdown.theta_pnl == Decimal("-50.0")

    # Total should equal actual (with some unexplained)
    assert breakdown.total_pnl == Decimal("2500")


# =============================================================================
# Event Log Tests
# =============================================================================


def test_event_log():
    """Test event logging."""
    from nse_options_bot.storage.event_log import EventLog, EventType

    log = EventLog(max_memory_events=100)

    # Log some events
    event = log.append(
        event_type=EventType.TRADE_ENTRY,
        data={"strategy": "SHORT_STRADDLE"},
        trade_id="T001",
        underlying="NIFTY",
    )

    assert event.event_type == EventType.TRADE_ENTRY
    assert event.trade_id == "T001"

    # Get events
    events = log.get_events(trade_id="T001")
    assert len(events) == 1

    log.close()


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_paper_broker_order():
    """Test paper broker order placement."""
    from nse_options_bot.paper.paper_broker import PaperBroker
    from nse_options_bot.paper.paper_ledger import PaperLedger
    from nse_options_bot.brokers.base import TransactionType, OrderType, ProductType

    ledger = PaperLedger(initial_capital=Decimal("500000"))
    broker = PaperBroker(ledger=ledger)

    order_id = await broker.place_order(
        tradingsymbol="NIFTY24D1922000CE",
        exchange="NFO",
        transaction_type=TransactionType.SELL,
        quantity=50,
        order_type=OrderType.LIMIT,
        product=ProductType.NRML,
        price=100.0,
    )

    assert order_id is not None
    assert order_id.startswith("PAPER_")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
