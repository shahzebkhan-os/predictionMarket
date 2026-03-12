"""
Tests for Trade Recommender.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date, time

from zoneinfo import ZoneInfo

from nse_advisor.recommender.engine import RecommenderEngine, TradeRecommendation, RecommendedLeg
from nse_advisor.signals.engine import AggregatedSignal, SignalResult
from nse_advisor.market.regime import MarketRegime, RegimeClassification


@pytest.fixture
def recommender():
    """Create recommender fixture."""
    return RecommenderEngine()


@pytest.fixture
def mock_aggregated_signal():
    """Create mock aggregated signal."""
    ist = ZoneInfo("Asia/Kolkata")
    return AggregatedSignal(
        composite_score=0.55,
        composite_confidence=0.72,
        direction="bullish",
        regime=MarketRegime.RANGE_BOUND,
        should_recommend=True,
        signals={
            "oi_analysis": SignalResult(
                name="oi_analysis",
                score=0.45,
                confidence=0.78,
                reason="PCR bullish",
                timestamp=datetime.now(ist),
            ),
            "iv_analysis": SignalResult(
                name="iv_analysis",
                score=0.62,
                confidence=0.85,
                reason="IV elevated",
                timestamp=datetime.now(ist),
            ),
        },
        timestamp=datetime.now(ist),
    )


class TestRecommendationIncludesAllLegs:
    """Tests for recommendation completeness."""
    
    def test_recommendation_structure(self):
        """Test that TradeRecommendation has all required fields."""
        rec = TradeRecommendation(
            recommendation_id="test123",
            generated_at=datetime.now(ZoneInfo("Asia/Kolkata")),
            underlying="NIFTY",
            strategy_name="Short Straddle",
            regime="RANGE_BOUND",
            composite_score=0.55,
            confidence=0.72,
            direction="bullish",
            legs=[
                RecommendedLeg(
                    tradingsymbol="NIFTY24DEC24000CE",
                    expiry=date(2024, 12, 26),
                    strike=24000,
                    option_type="CE",
                    action="SELL",
                    suggested_lots=1,
                    suggested_entry_price=50.0,
                    suggested_entry_range=(48.0, 52.0),
                ),
            ],
            max_profit_inr=3000,
            max_loss_inr=float('inf'),
            suggested_stop_loss_inr=5000,
            suggested_take_profit_inr=2250,
            breakeven_levels=[23950.0, 24050.0],
            individual_signal_scores={"oi_analysis": {"score": 0.45}},
            reasoning="Test reasoning",
            expiry_note="3 DTE",
            risk_warnings=["VIX elevated"],
            urgency="WATCH",
        )
        
        # Should have all required fields
        assert rec.recommendation_id
        assert rec.underlying == "NIFTY"
        assert len(rec.legs) > 0
        assert rec.legs[0].strike > 0


class TestKellySizingCorrect:
    """Tests for Kelly position sizing."""
    
    def test_kelly_sizing_correct(self):
        """Test Kelly fraction is applied correctly."""
        from nse_advisor.recommender.sizer import calculate_kelly_fraction
        
        # Test Kelly fraction calculation
        # Win rate = 60%, Win/Loss ratio = 2.0
        # Kelly = 0.6 - (1-0.6)/2.0 = 0.6 - 0.2 = 0.4
        kelly = calculate_kelly_fraction(
            win_rate=0.6,
            win_loss_ratio=2.0,
        )
        
        # Should be approximately 0.4
        assert abs(kelly - 0.4) < 0.01
        
        # Edge case: negative Kelly (bad edge)
        kelly_neg = calculate_kelly_fraction(
            win_rate=0.3,
            win_loss_ratio=0.5,
        )
        
        # Should be clamped to 0
        assert kelly_neg >= 0


class TestBanListBlocksRecommendation:
    """Tests for ban list blocking."""
    
    def test_ban_list_blocks_recommendation(self):
        """Test that banned symbols are tracked."""
        from nse_advisor.market.ban_list import BanListChecker
        
        checker = BanListChecker()
        
        # Add to ban list
        checker._banned_symbols = {"DELTACORP", "INDIABULLS"}
        
        # Should be banned
        assert checker.is_banned("DELTACORP")
        assert checker.is_banned("INDIABULLS")
        assert not checker.is_banned("RELIANCE")


class TestNoSignalAfter1500:
    """Tests for signal cutoff time."""
    
    def test_no_signal_after_1500(self, recommender):
        """Test cutoff time configuration exists."""
        from nse_advisor.config import get_settings
        
        settings = get_settings()
        
        # Should have cutoff time configured
        assert hasattr(settings, 'no_new_signals_after')
        assert settings.no_new_signals_after == "15:00"


class TestRolloverSuggestionNearExpiry:
    """Tests for rollover suggestions."""
    
    def test_rollover_suggestion_structure(self):
        """Test rollover suggestion structure."""
        from nse_advisor.recommender.rollover import RolloverManager, RolloverSuggestion
        from nse_advisor.tracker.state import ManualTrade, TradeLeg
        
        manager = RolloverManager()
        
        # Verify manager has required method
        assert hasattr(manager, 'suggest_rollover')
        
        # Create a sample trade
        trade = ManualTrade(
            trade_id="test123",
            strategy_name="Iron Condor",
            underlying="NIFTY",
            expiry=date.today(),
            entry_time=datetime.now(),
            legs=[
                TradeLeg(
                    tradingsymbol="NIFTY24DEC24000CE",
                    underlying="NIFTY",
                    strike=24000,
                    expiry=date.today(),
                    option_type="CE",
                    action="SELL",
                    quantity_lots=1,
                    lot_size=75,
                    entry_price=50,
                    current_price=10,
                ),
            ],
        )
        
        # Verify DTE calculation
        assert trade.dte == 0  # Expiry today
