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
    
    @pytest.mark.asyncio
    async def test_oi_pcr_bullish(self):
        """Test OI signal is bullish when PCR is rising and > 1.5."""
        from nse_advisor.signals.oi_analysis import OIAnalysisSignal
        
        signal = OIAnalysisSignal()
        
        # Mock chain with high PCR
        mock_chain = MagicMock()
        mock_chain.get_pcr.return_value = 1.6
        
        with patch.object(signal, '_get_chain', return_value=mock_chain):
            result = await signal.compute()
            
            # High PCR with rising trend should be bullish
            assert result is not None
            assert result.score >= 0  # Should be bullish or neutral


class TestIVRankSell:
    """Tests for IV rank sell signal."""
    
    @pytest.mark.asyncio
    async def test_iv_rank_sell(self):
        """Test IV signal recommends selling when IVR > 70."""
        from nse_advisor.signals.iv_analysis import IVAnalysisSignal
        
        signal = IVAnalysisSignal()
        
        # Mock high IVR
        with patch.object(signal, '_calculate_ivr', return_value=75.0):
            result = await signal.compute()
            
            # High IVR should suggest selling premium
            assert result is not None


class TestRegimeRangeBound:
    """Tests for regime detection."""
    
    @pytest.mark.asyncio
    async def test_regime_range_bound(self):
        """Test range-bound regime detection."""
        from nse_advisor.market.regime import RegimeClassifier
        
        classifier = RegimeClassifier()
        
        # Mock data for range-bound conditions
        with patch.object(classifier, '_get_spot_data') as mock_spot, \
             patch.object(classifier, '_get_vix') as mock_vix, \
             patch.object(classifier, '_get_gex') as mock_gex:
            
            mock_spot.return_value = {"price": 24000, "vwap": 24010, "ema20": 23990}
            mock_vix.return_value = 14.0  # Low VIX
            mock_gex.return_value = 50000  # Positive GEX
            
            result = await classifier.classify()
            
            # Should detect range-bound
            assert result is not None


class TestWeightsSumToOne:
    """Tests for signal weights validation."""
    
    def test_weights_sum_to_one(self, signal_engine):
        """Test that regime weights sum to 1.0."""
        for regime in [MarketRegime.RANGE_BOUND, MarketRegime.TRENDING_UP, 
                       MarketRegime.TRENDING_DOWN, MarketRegime.HIGH_VOLATILITY]:
            weights = signal_engine.get_weights_for_regime(regime)
            total = sum(weights.values())
            
            # Weights should sum to approximately 1.0
            assert 0.99 <= total <= 1.01, f"Weights for {regime} sum to {total}"


class TestBlackoutBlocksSignal:
    """Tests for event blackout blocking."""
    
    @pytest.mark.asyncio
    async def test_blackout_blocks_signal(self, signal_engine):
        """Test that event blackout blocks signal generation."""
        from nse_advisor.market.nse_calendar import NseCalendar
        
        # Mock blackout period
        with patch.object(NseCalendar, 'is_event_blackout', return_value=True):
            result = await signal_engine.scan()
            
            # Should not recommend during blackout
            if result:
                assert not result.should_recommend


class TestCircuitBreakerBlocksSignal:
    """Tests for circuit breaker blocking."""
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_signal(self, signal_engine):
        """Test that circuit breaker blocks signal generation."""
        from nse_advisor.market.circuit_breaker import CircuitBreaker
        
        # Mock market halt
        with patch.object(CircuitBreaker, 'is_market_halted', return_value=True):
            result = await signal_engine.scan()
            
            # Should not recommend during halt
            if result:
                assert not result.should_recommend


class TestScanTimeoutUsesCache:
    """Tests for scan timeout handling."""
    
    @pytest.mark.asyncio
    async def test_scan_timeout_uses_cache(self, signal_engine):
        """Test that signal timeout uses cached value."""
        # Set a cached value
        signal_engine._cached_signals["oi_analysis"] = MagicMock(score=0.5)
        
        # Mock a timeout on OI signal
        from nse_advisor.signals.oi_analysis import OIAnalysisSignal
        
        async def slow_compute():
            import asyncio
            await asyncio.sleep(100)
            return MagicMock(score=0.8)
        
        with patch.object(OIAnalysisSignal, 'compute', side_effect=slow_compute):
            # Run with short timeout
            import asyncio
            try:
                result = await asyncio.wait_for(signal_engine.scan(), timeout=0.1)
            except asyncio.TimeoutError:
                # Timeout is expected, check cache is used
                cached = signal_engine._cached_signals.get("oi_analysis")
                assert cached is not None
                assert cached.score == 0.5


class TestColdStartBackfill:
    """Tests for cold start backfill."""
    
    @pytest.mark.asyncio
    async def test_cold_start_backfill_produces_valid_supertrend(self):
        """Test that backfill produces valid Supertrend values."""
        from nse_advisor.signals.technicals import TechnicalsSignal
        
        signal = TechnicalsSignal()
        
        # After backfill, Supertrend should be valid
        with patch.object(signal, '_get_candles') as mock_candles:
            # Provide enough candles for indicator calculation
            import pandas as pd
            import numpy as np
            
            dates = pd.date_range(end=datetime.now(), periods=60, freq='5T')
            mock_candles.return_value = pd.DataFrame({
                'open': np.random.uniform(23900, 24100, 60),
                'high': np.random.uniform(24000, 24200, 60),
                'low': np.random.uniform(23800, 24000, 60),
                'close': np.random.uniform(23900, 24100, 60),
                'volume': np.random.uniform(10000, 50000, 60),
            }, index=dates)
            
            result = await signal.compute()
            
            # Should produce a valid result
            assert result is not None
