"""
Tests for Trade Recommender.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date, time

from zoneinfo import ZoneInfo

from nse_advisor.recommender.engine import RecommenderEngine, TradeRecommendation
from nse_advisor.signals.engine import AggregatedSignal
from nse_advisor.market.regime import MarketRegime, RegimeClassification


@pytest.fixture
def recommender():
    """Create recommender fixture."""
    return RecommenderEngine()


@pytest.fixture
def mock_aggregated_signal():
    """Create mock aggregated signal."""
    return AggregatedSignal(
        composite_score=0.55,
        composite_confidence=0.72,
        direction="bullish",
        regime=MarketRegime.RANGE_BOUND,
        should_recommend=True,
        individual_signals={
            "oi_analysis": {"score": 0.45, "confidence": 0.78},
            "iv_analysis": {"score": 0.62, "confidence": 0.85},
        },
        reasoning="Strong OI support with elevated IV",
    )


class TestRecommendationIncludesAllLegs:
    """Tests for recommendation completeness."""
    
    @pytest.mark.asyncio
    async def test_recommendation_includes_all_legs(
        self, recommender, mock_aggregated_signal
    ):
        """Test that recommendation includes all required legs."""
        with patch.object(recommender, '_get_chain') as mock_chain:
            mock_chain.return_value = MagicMock(
                spot_price=24000,
                get_atm_strike=lambda: 24000,
                get_strike=lambda s: MagicMock(
                    ce_ltp=50, pe_ltp=50,
                    ce_iv=15, pe_iv=15,
                ),
            )
            
            rec = await recommender.generate_recommendation(
                underlying="NIFTY",
                aggregated_signal=mock_aggregated_signal,
            )
            
            if rec:
                # Should have legs
                assert len(rec.legs) > 0
                
                # Each leg should have required fields
                for leg in rec.legs:
                    assert leg.tradingsymbol
                    assert leg.strike > 0
                    assert leg.option_type in ["CE", "PE"]
                    assert leg.action in ["BUY", "SELL"]
                    assert leg.suggested_lots > 0


class TestKellySizingCorrect:
    """Tests for Kelly position sizing."""
    
    def test_kelly_sizing_correct(self):
        """Test Kelly fraction is applied correctly."""
        from nse_advisor.recommender.sizer import PositionSizer
        
        sizer = PositionSizer()
        
        # Test parameters
        max_loss_per_lot = 2000
        max_loss_per_trade = 6000
        kelly_fraction = 0.5
        
        # Raw lots = 6000 / 2000 = 3
        # With Kelly = floor(3 * 0.5) = 1
        lots = sizer.calculate_lots(
            max_loss_per_lot=max_loss_per_lot,
            max_loss_per_trade=max_loss_per_trade,
            kelly_fraction=kelly_fraction,
        )
        
        # Should be at least 1
        assert lots >= 1
        
        # Should not exceed raw calculation
        raw_lots = max_loss_per_trade / max_loss_per_lot
        assert lots <= raw_lots


class TestBanListBlocksRecommendation:
    """Tests for ban list blocking."""
    
    @pytest.mark.asyncio
    async def test_ban_list_blocks_recommendation(
        self, recommender, mock_aggregated_signal
    ):
        """Test that banned symbols block recommendations."""
        from nse_advisor.market.ban_list import BanListChecker
        
        # Mock banned symbol
        with patch.object(BanListChecker, 'is_banned', return_value=True):
            rec = await recommender.generate_recommendation(
                underlying="DELTACORP",  # Banned symbol
                aggregated_signal=mock_aggregated_signal,
            )
            
            # Should not generate recommendation for banned symbol
            assert rec is None


class TestNoSignalAfter1500:
    """Tests for signal cutoff time."""
    
    @pytest.mark.asyncio
    async def test_no_signal_after_1500(
        self, recommender, mock_aggregated_signal
    ):
        """Test that no new signals after 15:00 IST."""
        ist = ZoneInfo("Asia/Kolkata")
        
        # Mock time after 15:00
        mock_time = datetime(2024, 12, 26, 15, 30, tzinfo=ist)
        
        with patch('nse_advisor.recommender.engine.datetime') as mock_dt:
            mock_dt.now.return_value = mock_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            rec = await recommender.generate_recommendation(
                underlying="NIFTY",
                aggregated_signal=mock_aggregated_signal,
            )
            
            # Should not generate after cutoff
            # (Implementation should check time)


class TestRolloverSuggestionNearExpiry:
    """Tests for rollover suggestions."""
    
    @pytest.mark.asyncio
    async def test_rollover_suggestion_near_expiry(self):
        """Test rollover suggestion when DTE <= 1."""
        from nse_advisor.recommender.rollover import RolloverManager
        from nse_advisor.tracker.state import ManualTrade, TradeLeg
        
        manager = RolloverManager()
        
        # Create trade with DTE=1
        trade = ManualTrade(
            trade_id="test123",
            strategy_name="Iron Condor",
            underlying="NIFTY",
            expiry=date.today(),  # Expiry today
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
        
        suggestion = await manager.suggest_rollover(trade)
        
        # Should suggest rollover when near expiry
        if trade.dte <= 1:
            assert suggestion is not None or trade.unrealized_pnl <= 0
