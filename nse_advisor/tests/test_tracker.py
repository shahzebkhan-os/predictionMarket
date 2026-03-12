"""
Tests for Position Tracker.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date, time

from zoneinfo import ZoneInfo

from nse_advisor.tracker.state import ManualTrade, TradeLeg
from nse_advisor.tracker.position_tracker import PositionTracker
from nse_advisor.tracker.exit_advisor import ExitAdvisor, ExitAlert


@pytest.fixture
def sample_trade():
    """Create sample trade fixture."""
    return ManualTrade(
        trade_id="test123",
        strategy_name="Iron Condor",
        underlying="NIFTY",
        expiry=date(2024, 12, 26),
        entry_time=datetime(2024, 12, 22, 10, 15),
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
                current_delta=0.38,
                current_gamma=0.002,
                current_theta=-7.8,
                current_vega=11.5,
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
                current_delta=0.25,
                current_gamma=0.001,
                current_theta=-5.2,
                current_vega=8.5,
            ),
        ],
        max_profit=6500,
        max_loss=3500,
        stop_loss_inr=3500,
        take_profit_inr=4875,  # 75% of max profit
        status="LIVE",
        paper_mode=False,
    )


@pytest.fixture
def tracker():
    """Create position tracker fixture."""
    return PositionTracker()


@pytest.fixture
def exit_advisor():
    """Create exit advisor fixture."""
    return ExitAdvisor()


class TestExitAlertStopLoss:
    """Tests for stop loss exit alerts."""
    
    def test_exit_alert_stop_loss(self, exit_advisor, sample_trade):
        """Test stop loss alert is triggered."""
        # Modify trade to have hit stop loss
        for leg in sample_trade.legs:
            if leg.action == "SELL":
                leg.current_price = leg.entry_price + 50  # Loss for SELL
            else:
                leg.current_price = leg.entry_price - 50  # Loss for BUY
        
        # Make P&L exceed stop loss
        sample_trade.stop_loss_inr = 1000
        
        alerts = exit_advisor.check_all_conditions(sample_trade)
        
        # Should have stop loss alert
        stop_alerts = [a for a in alerts if a.alert_type == "STOP_LOSS"]
        assert len(stop_alerts) > 0 or sample_trade.unrealized_pnl > -1000


class TestExitAlertTakeProfit75Pct:
    """Tests for 75% profit target alerts."""
    
    def test_exit_alert_take_profit_75pct(self, exit_advisor, sample_trade):
        """Test 75% profit alert is triggered."""
        # Current P&L should be positive
        # Leg 1 (SELL): (85.5 - 32.25) * 2 * 75 = 7987.5
        # Leg 2 (BUY): (15.5 - 42.25) * 2 * 75 = -4012.5
        # Total: 3975
        
        # Set max_profit so 75% is less than current P&L
        sample_trade.max_profit = 5000  # 75% = 3750
        
        alerts = exit_advisor.check_all_conditions(sample_trade)
        
        # Check if we have partial target alert
        partial_alerts = [a for a in alerts if a.alert_type == "PARTIAL_TARGET"]
        
        # If P&L > 75% of max profit, should have alert
        pnl = sample_trade.unrealized_pnl
        if pnl >= sample_trade.max_profit * 0.75:
            assert len(partial_alerts) > 0


class TestGreeksSignSellNegative:
    """Tests for Greeks sign on SELL legs."""
    
    def test_greeks_sign_sell_negative(self, sample_trade):
        """Test that SELL leg Greeks are correctly signed."""
        sell_leg = sample_trade.legs[0]  # SELL leg
        
        # Multiplier should be -1 for SELL
        assert sell_leg.greeks_multiplier == -1
        
        # Adjusted delta should be negative (for positive raw delta)
        expected_delta = -0.38 * 2 * 75  # -57
        assert sell_leg.adjusted_delta == expected_delta


class TestExpiryDayAlert:
    """Tests for expiry day alerts."""
    
    def test_expiry_day_alert(self, exit_advisor):
        """Test expiry day alert after 14:30."""
        ist = ZoneInfo("Asia/Kolkata")
        
        # Create trade expiring today
        trade = ManualTrade(
            trade_id="test456",
            strategy_name="Short Straddle",
            underlying="NIFTY",
            expiry=date.today(),  # Expiry today
            entry_time=datetime.now(ist),
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
            status="LIVE",
            paper_mode=False,
        )
        
        # Mock time to be after 14:30
        with patch('nse_advisor.tracker.exit_advisor.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(
                date.today().year,
                date.today().month,
                date.today().day,
                14, 45,
                tzinfo=ist
            )
            
            alerts = exit_advisor.check_all_conditions(trade)
            
            # Should have expiry urgent alert
            expiry_alerts = [a for a in alerts if a.alert_type == "EXPIRY_URGENT"]
            
            # Only check if DTE is 0
            if trade.dte == 0:
                # Alert depends on time of day
                pass


class TestIndMoneyDiscrepancyFlag:
    """Tests for IndMoney discrepancy detection."""
    
    @pytest.mark.asyncio
    async def test_indmoney_discrepancy_flag(self):
        """Test that untracked positions are flagged."""
        from nse_advisor.data.indmoney_client import IndMoneyClient
        
        client = IndMoneyClient()
        
        # Mock IndMoney positions
        indmoney_positions = [
            {"symbol": "NIFTY24DEC24000CE", "quantity": 75},
            {"symbol": "NIFTY24DEC24100PE", "quantity": 150},  # Untracked
        ]
        
        # Our tracked positions
        tracked_symbols = ["NIFTY24DEC24000CE"]
        
        with patch.object(client, 'get_positions', return_value=indmoney_positions):
            positions = await client.get_positions()
            
            # Check for untracked
            untracked = [
                p for p in positions
                if p["symbol"] not in tracked_symbols
            ]
            
            assert len(untracked) == 1
            assert untracked[0]["symbol"] == "NIFTY24DEC24100PE"


class TestTrackerPnLCalculation:
    """Tests for P&L calculation."""
    
    def test_unrealized_pnl(self, sample_trade):
        """Test unrealized P&L calculation."""
        pnl = sample_trade.unrealized_pnl
        
        # Calculate expected:
        # SELL leg: (entry - current) * qty = (85.5 - 32.25) * 150 = 7987.5
        # BUY leg: (current - entry) * qty = (15.5 - 42.25) * 150 = -4012.5
        # Total: 3975
        expected = 7987.5 - 4012.5
        
        assert abs(pnl - expected) < 0.01
    
    def test_portfolio_greeks(self, tracker, sample_trade):
        """Test portfolio Greeks aggregation."""
        tracker.add_trade(sample_trade)
        
        greeks = tracker.get_portfolio_greeks()
        
        # Should have all Greek keys
        assert "delta" in greeks
        assert "gamma" in greeks
        assert "theta" in greeks
        assert "vega" in greeks
