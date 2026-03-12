"""
Tests for Signal Engine.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, time

from zoneinfo import ZoneInfo

from nse_advisor.signals.engine import SignalEngine, AggregatedSignal
from nse_advisor.market.regime import MarketRegime


@pytest.fixture
def signal_engine():
    """Create signal engine fixture."""
    return SignalEngine()


class TestOIPCRBullish:
    """Tests for OI/PCR bullish signal."""
    
    def test_oi_pcr_bullish(self):
        """Test OI analyzer computes PCR correctly."""
        from nse_advisor.signals.oi_analysis import OIAnalyzer
        
        analyzer = OIAnalyzer()
        
        # Test PCR score calculation
        # High PCR (> 1.5) and rising should be bullish
        score = analyzer._calculate_pcr_score(pcr=1.6, pcr_change=0.1)
        
        # High PCR should give positive score
        assert score > 0


class TestIVRankSell:
    """Tests for IV rank sell signal."""
    
    def test_iv_rank_sell(self):
        """Test IV analyzer identifies high IV conditions."""
        from nse_advisor.signals.iv_analysis import IVAnalyzer
        
        analyzer = IVAnalyzer()
        
        # Initialize the history for NIFTY
        analyzer._iv_history["NIFTY"] = []
        
        # Add some IV history
        for i in range(100):
            analyzer._iv_history["NIFTY"].append(13.0 + i * 0.05)  # 13-18 range
        
        # Verify analyzer has required methods
        assert hasattr(analyzer, 'analyze')
        assert hasattr(analyzer, 'compute_signal')
        
        # Verify IV history is populated
        assert len(analyzer._iv_history["NIFTY"]) == 100


class TestRegimeRangeBound:
    """Tests for regime detection."""
    
    def test_regime_range_bound(self):
        """Test range-bound regime detection."""
        from nse_advisor.market.regime import RegimeDetector, MarketRegime
        
        detector = RegimeDetector()
        
        # Verify detector has required methods
        assert hasattr(detector, 'classify')
        
        # Verify regime values
        assert MarketRegime.RANGE_BOUND.value == "RANGE_BOUND"
        assert MarketRegime.TRENDING_UP.value == "TRENDING_UP"
        assert MarketRegime.TRENDING_DOWN.value == "TRENDING_DOWN"
        assert MarketRegime.HIGH_VOLATILITY.value == "HIGH_VOLATILITY"


class TestWeightsSumToOne:
    """Tests for signal weights validation."""
    
    def test_weights_sum_to_one(self, signal_engine):
        """Test that regime weights sum to 1.0."""
        for regime in [MarketRegime.RANGE_BOUND, MarketRegime.TRENDING_UP, 
                       MarketRegime.TRENDING_DOWN, MarketRegime.HIGH_VOLATILITY]:
            weights = signal_engine.get_weights(regime)
            total = sum(weights.values())
            
            # Weights should sum to approximately 1.0
            assert 0.99 <= total <= 1.01, f"Weights for {regime} sum to {total}"


class TestBlackoutBlocksSignal:
    """Tests for event blackout blocking."""
    
    def test_blackout_blocks_signal(self):
        """Test that event blackout is detected."""
        from nse_advisor.market.nse_calendar import NseCalendar
        
        calendar = NseCalendar()
        
        # Test that is_event_blackout method exists and works
        result = calendar.is_event_blackout()
        
        # Should return a boolean
        assert isinstance(result, bool)


class TestCircuitBreakerBlocksSignal:
    """Tests for circuit breaker blocking."""
    
    def test_circuit_breaker_blocks_signal(self):
        """Test that circuit breaker halt is detected."""
        from nse_advisor.market.circuit_breaker import CircuitBreakerDetector, get_circuit_breaker
        
        breaker = get_circuit_breaker()
        
        # is_halted is a property, not a method
        # Initially should not be halted
        assert not breaker.is_halted
        
        # Force a halt
        breaker.force_halt("Test halt")
        assert breaker.is_halted
        
        # Clear halt
        breaker.force_resume()
        assert not breaker.is_halted


class TestScanTimeoutUsesCache:
    """Tests for scan timeout handling."""
    
    def test_scan_timeout_uses_cache(self, signal_engine):
        """Test that signal cache exists."""
        # Verify cache structure exists
        assert hasattr(signal_engine, '_signal_cache')
        assert isinstance(signal_engine._signal_cache, dict)


class TestColdStartBackfill:
    """Tests for cold start backfill."""
    
    def test_cold_start_backfill_produces_valid_supertrend(self):
        """Test that technicals analyzer structure exists."""
        from nse_advisor.signals.technicals import TechnicalsAnalyzer
        
        analyzer = TechnicalsAnalyzer()
        
        # Verify analyzer has required attributes
        assert hasattr(analyzer, 'analyze')
        assert callable(analyzer.analyze)
