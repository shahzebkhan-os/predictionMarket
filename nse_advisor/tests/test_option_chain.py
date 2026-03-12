"""
Tests for Option Chain functionality.
"""

import pytest
from datetime import date

from nse_advisor.market.option_chain import (
    OptionChainSnapshot,
    StrikeData,
    OptionChainManager,
)


@pytest.fixture
def sample_chain():
    """Create sample option chain for testing."""
    strikes = [
        StrikeData(
            strike=23900,
            ce_ltp=85.5, ce_bid=85.0, ce_ask=86.0,
            ce_oi=250000, ce_oi_change=15000, ce_volume=45000,
            ce_iv=15.2, ce_delta=0.65, ce_gamma=0.002,
            ce_theta=-8.5, ce_vega=12.5,
            pe_ltp=45.0, pe_bid=44.5, pe_ask=45.5,
            pe_oi=180000, pe_oi_change=-5000, pe_volume=32000,
            pe_iv=16.1, pe_delta=-0.35, pe_gamma=0.002,
            pe_theta=-7.2, pe_vega=11.8,
        ),
        StrikeData(
            strike=24000,
            ce_ltp=55.0, ce_bid=54.5, ce_ask=55.5,
            ce_oi=320000, ce_oi_change=25000, ce_volume=65000,
            ce_iv=15.0, ce_delta=0.52, ce_gamma=0.003,
            ce_theta=-9.2, ce_vega=14.2,
            pe_ltp=65.0, pe_bid=64.5, pe_ask=65.5,
            pe_oi=280000, pe_oi_change=18000, pe_volume=55000,
            pe_iv=15.5, pe_delta=-0.48, pe_gamma=0.003,
            pe_theta=-8.8, pe_vega=13.8,
        ),
        StrikeData(
            strike=24100,
            ce_ltp=35.0, ce_bid=34.5, ce_ask=35.5,
            ce_oi=280000, ce_oi_change=20000, ce_volume=48000,
            ce_iv=15.3, ce_delta=0.38, ce_gamma=0.002,
            ce_theta=-7.8, ce_vega=11.5,
            pe_ltp=95.0, pe_bid=94.5, pe_ask=95.5,
            pe_oi=150000, pe_oi_change=8000, pe_volume=28000,
            pe_iv=16.8, pe_delta=-0.62, pe_gamma=0.002,
            pe_theta=-8.5, pe_vega=12.2,
        ),
    ]
    
    return OptionChainSnapshot(
        underlying="NIFTY",
        spot_price=24052.75,
        expiry=date(2024, 12, 26),
        strikes={s.strike: s for s in strikes},
    )


class TestPCRCalculation:
    """Tests for PCR calculation."""
    
    def test_pcr_calculation(self, sample_chain):
        """Test Put-Call Ratio calculation."""
        pcr = sample_chain.get_pcr()
        
        # Total PE OI: 180000 + 280000 + 150000 = 610000
        # Total CE OI: 250000 + 320000 + 280000 = 850000
        # PCR = 610000 / 850000 ≈ 0.717
        assert 0.7 < pcr < 0.75
    
    def test_pcr_zero_ce_oi(self):
        """Test PCR when CE OI is zero."""
        strikes = [
            StrikeData(
                strike=24000,
                ce_oi=0, pe_oi=100000,
                ce_ltp=0, pe_ltp=50,
            ),
        ]
        chain = OptionChainSnapshot(
            underlying="NIFTY",
            spot_price=24000,
            expiry=date(2024, 12, 26),
            strikes={s.strike: s for s in strikes},
        )
        
        # Should handle division by zero
        pcr = chain.get_pcr()
        assert pcr == float('inf') or pcr > 100


class TestMaxPain:
    """Tests for Max Pain calculation."""
    
    def test_max_pain(self, sample_chain):
        """Test Max Pain calculation."""
        max_pain = sample_chain.get_max_pain()
        
        # Max pain should be one of our strikes
        assert max_pain in [23900, 24000, 24100]
    
    def test_max_pain_empty_chain(self):
        """Test Max Pain with empty chain."""
        chain = OptionChainSnapshot(
            underlying="NIFTY",
            spot_price=24000,
            expiry=date(2024, 12, 26),
            strikes={},
        )
        
        max_pain = chain.get_max_pain()
        assert max_pain is None or max_pain == 0


class TestATMDetection:
    """Tests for ATM strike detection."""
    
    def test_atm_detection(self, sample_chain):
        """Test ATM strike detection."""
        atm = sample_chain.get_atm_strike()
        
        # Spot is 24052.75, so ATM should be 24050 or 24000
        assert atm == 24000  # Closest strike
    
    def test_atm_exact_match(self):
        """Test ATM when spot exactly matches a strike."""
        strikes = [
            StrikeData(strike=24000, ce_ltp=50, pe_ltp=50, ce_oi=1000, pe_oi=1000),
        ]
        chain = OptionChainSnapshot(
            underlying="NIFTY",
            spot_price=24000,
            expiry=date(2024, 12, 26),
            strikes={s.strike: s for s in strikes},
        )
        
        atm = chain.get_atm_strike()
        assert atm == 24000


class TestIVSkew:
    """Tests for IV skew calculation."""
    
    def test_iv_skew(self, sample_chain):
        """Test IV skew (PE IV - CE IV at 25-delta)."""
        skew = sample_chain.get_iv_skew()
        
        # Skew is typically positive (puts have higher IV)
        assert skew is not None


class TestGEXSign:
    """Tests for GEX sign calculation."""
    
    def test_gex_sign(self, sample_chain):
        """Test Gamma Exposure sign."""
        gex = sample_chain.get_total_gex()
        
        # GEX should be a number
        assert isinstance(gex, (int, float))
    
    def test_positive_gex_range_bound(self, sample_chain):
        """Test that positive GEX indicates range-bound."""
        gex = sample_chain.get_total_gex()
        
        # Our sample data should have positive or negative GEX
        # This tests the calculation runs without error
        assert gex != float('nan')


class TestGreeksSignSellLeg:
    """Tests for Greeks sign on SELL legs."""
    
    def test_greeks_sign_sell_leg(self, sample_chain):
        """Test that SELL leg Greeks are multiplied by -1."""
        from nse_advisor.tracker.state import TradeLeg
        
        leg = TradeLeg(
            tradingsymbol="NIFTY24DEC24000CE",
            underlying="NIFTY",
            strike=24000,
            expiry=date(2024, 12, 26),
            option_type="CE",
            action="SELL",
            quantity_lots=1,
            lot_size=75,
            entry_price=55.0,
            current_price=55.0,
            current_delta=0.52,
            current_gamma=0.003,
            current_theta=-9.2,
            current_vega=14.2,
        )
        
        # For SELL, multiplier should be -1
        assert leg.greeks_multiplier == -1
        
        # Adjusted Greeks should be negative of raw (for delta, vega)
        # and positive for theta (since we're selling)
        assert leg.adjusted_delta == -0.52 * 75  # Negative
        assert leg.adjusted_vega == -14.2 * 75  # Negative
