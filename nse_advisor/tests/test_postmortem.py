"""
Tests for Postmortem Engine.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date, timedelta

from zoneinfo import ZoneInfo

from nse_advisor.postmortem.engine import PostmortemEngine, TradePostmortem
from nse_advisor.tracker.state import ManualTrade, TradeLeg


@pytest.fixture
def postmortem_engine():
    """Create postmortem engine fixture."""
    return PostmortemEngine()


@pytest.fixture
def closed_trade():
    """Create closed trade fixture."""
    ist = ZoneInfo("Asia/Kolkata")
    
    trade = ManualTrade(
        trade_id="test123",
        strategy_name="Iron Condor",
        underlying="NIFTY",
        expiry=date(2024, 12, 26),
        entry_time=datetime(2024, 12, 22, 10, 15, tzinfo=ist),
        legs=[
            TradeLeg(
                tradingsymbol="NIFTY24DEC24100CE",
                underlying="NIFTY",
                strike=24100,
                expiry=date(2024, 12, 26),
                option_type="CE",
                action="SELL",
                quantity_lots=2,
                lot_size=75,
                entry_price=85.5,
                current_price=32.25,
                exit_price=32.25,
                exit_time=datetime(2024, 12, 24, 14, 30, tzinfo=ist),
            ),
            TradeLeg(
                tradingsymbol="NIFTY24DEC24200CE",
                underlying="NIFTY",
                strike=24200,
                expiry=date(2024, 12, 26),
                option_type="CE",
                action="BUY",
                quantity_lots=2,
                lot_size=75,
                entry_price=42.25,
                current_price=15.5,
                exit_price=15.5,
                exit_time=datetime(2024, 12, 24, 14, 30, tzinfo=ist),
            ),
        ],
        max_profit=6500,
        max_loss=3500,
        signal_scores_at_entry={
            "oi_analysis": {"score": 0.45, "confidence": 0.78},
            "iv_analysis": {"score": 0.62, "confidence": 0.85},
            "technicals": {"score": 0.35, "confidence": 0.70},
        },
        regime_at_entry="RANGE_BOUND",
        status="CLOSED",
        paper_mode=True,
        exit_time=datetime(2024, 12, 24, 14, 30, tzinfo=ist),
        exit_reason="75% target reached",
    )
    
    return trade


class TestGreeksAttributionSumsToTotal:
    """Tests for Greeks P&L attribution."""
    
    def test_greeks_attribution_sums_to_total(
        self, postmortem_engine, closed_trade
    ):
        """Test that Greeks P&L attribution sums to total P&L."""
        # Track some excursion data
        postmortem_engine.track_excursion(closed_trade.trade_id, 2000)
        postmortem_engine.track_excursion(closed_trade.trade_id, 4000)
        postmortem_engine.track_excursion(closed_trade.trade_id, 3975)
        
        result = postmortem_engine.analyze_trade(closed_trade)
        
        # Sum of Greeks attribution should equal total
        greeks_sum = (
            result.delta_pnl +
            result.theta_pnl +
            result.vega_pnl +
            result.gamma_pnl +
            result.residual_pnl
        )
        
        assert abs(greeks_sum - result.realized_pnl_inr) < 0.01


class TestVerdictUserOverride:
    """Tests for user override verdict."""
    
    def test_verdict_user_override(self, postmortem_engine):
        """Test USER_OVERRIDE verdict for trades without recommendation."""
        ist = ZoneInfo("Asia/Kolkata")
        
        # Create trade without linked recommendation
        trade = ManualTrade(
            trade_id="manual123",
            strategy_name="Custom Trade",
            underlying="NIFTY",
            expiry=date(2024, 12, 26),
            entry_time=datetime(2024, 12, 22, 10, 15, tzinfo=ist),
            legs=[
                TradeLeg(
                    tradingsymbol="NIFTY24DEC24000CE",
                    underlying="NIFTY",
                    strike=24000,
                    expiry=date(2024, 12, 26),
                    option_type="CE",
                    action="BUY",
                    quantity_lots=1,
                    lot_size=75,
                    entry_price=50,
                    current_price=30,
                    exit_price=30,
                    exit_time=datetime(2024, 12, 24, 14, 30, tzinfo=ist),
                ),
            ],
            linked_recommendation_id=None,  # No recommendation
            signal_scores_at_entry={},
            status="CLOSED",
            paper_mode=True,
            exit_time=datetime(2024, 12, 24, 14, 30, tzinfo=ist),
        )
        
        result = postmortem_engine.analyze_trade(trade)
        
        # Should be USER_OVERRIDE when no recommendation linked
        assert result.verdict == "USER_OVERRIDE"


class TestSignalAccuracyTable:
    """Tests for signal accuracy calculation."""
    
    def test_signal_accuracy_table(self, postmortem_engine, closed_trade):
        """Test signal accuracy is calculated correctly."""
        result = postmortem_engine.analyze_trade(closed_trade)
        
        # Should have accuracy for each signal
        assert "oi_analysis" in result.signal_accuracy
        assert "iv_analysis" in result.signal_accuracy
        assert "technicals" in result.signal_accuracy
        
        # Each accuracy should be 0 or 1
        for name, acc in result.signal_accuracy.items():
            if name != "composite":
                assert acc in [0.0, 1.0] or 0 <= acc <= 1


class TestPaperVsActualComparison:
    """Tests for paper vs actual comparison."""
    
    def test_paper_vs_actual_comparison(self, postmortem_engine):
        """Test paper vs actual P&L comparison in nightly report."""
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        
        # Create mix of paper and actual trades
        trades = []
        
        # Paper trades
        for i in range(3):
            trade = ManualTrade(
                trade_id=f"paper{i}",
                strategy_name="Iron Condor",
                underlying="NIFTY",
                expiry=date.today() + timedelta(days=7),
                entry_time=now - timedelta(days=i+1),
                legs=[
                    TradeLeg(
                        tradingsymbol=f"NIFTY{i}",
                        underlying="NIFTY",
                        strike=24000,
                        expiry=date.today() + timedelta(days=7),
                        option_type="CE",
                        action="SELL",
                        quantity_lots=1,
                        lot_size=75,
                        entry_price=50,
                        current_price=30,
                        exit_price=30,
                    ),
                ],
                status="CLOSED",
                paper_mode=True,
                exit_time=now - timedelta(hours=i+1),
            )
            trades.append(trade)
        
        # Actual trades
        for i in range(2):
            trade = ManualTrade(
                trade_id=f"actual{i}",
                strategy_name="Short Straddle",
                underlying="BANKNIFTY",
                expiry=date.today() + timedelta(days=7),
                entry_time=now - timedelta(days=i+1),
                legs=[
                    TradeLeg(
                        tradingsymbol=f"BANKNIFTY{i}",
                        underlying="BANKNIFTY",
                        strike=51000,
                        expiry=date.today() + timedelta(days=7),
                        option_type="CE",
                        action="SELL",
                        quantity_lots=1,
                        lot_size=30,
                        entry_price=100,
                        current_price=50,
                        exit_price=50,
                    ),
                ],
                status="CLOSED",
                paper_mode=False,  # Actual trade
                exit_time=now - timedelta(hours=i+1),
            )
            trades.append(trade)
        
        report = postmortem_engine.nightly_report(trades, lookback_days=30)
        
        # Should have separate paper and actual P&L
        assert "paper_pnl" in vars(report) or hasattr(report, "paper_pnl")
        assert "actual_pnl" in vars(report) or hasattr(report, "actual_pnl")


class TestExitQualityScore:
    """Tests for exit quality scoring."""
    
    def test_exit_quality_score(self, postmortem_engine, closed_trade):
        """Test exit quality is calculated correctly."""
        # Track MAE and MFE
        postmortem_engine.track_excursion(closed_trade.trade_id, -500)  # MAE
        postmortem_engine.track_excursion(closed_trade.trade_id, 4500)  # MFE
        postmortem_engine.track_excursion(closed_trade.trade_id, 3975)  # Exit
        
        result = postmortem_engine.analyze_trade(closed_trade)
        
        # Exit quality should be between 0 and 1
        assert 0 <= result.exit_quality_score <= 1
        
        # Should capture ~88% of MFE (3975/4500)
        expected_quality = 3975 / 4500
        assert abs(result.exit_quality_score - expected_quality) < 0.1
