"""
Signal Engine Master Aggregator.

Aggregates all 12 signals with regime-weighted scoring.
Produces composite signal score and confidence for trade recommendations.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable, Awaitable

import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.market.ban_list import get_ban_list_checker
from nse_advisor.market.circuit_breaker import get_circuit_breaker
from nse_advisor.market.nse_calendar import get_nse_calendar
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.market.regime import MarketRegime, RegimeClassification

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """Result from a single signal computation."""
    name: str
    score: float  # -1.0 to +1.0
    confidence: float  # 0 to 1
    reason: str
    timestamp: datetime
    cached: bool = False


@dataclass
class AggregatedSignal:
    """Aggregated signal from all signal sources."""
    composite_score: float  # -1.0 to +1.0
    composite_confidence: float  # 0 to 1
    regime: MarketRegime
    signals: dict[str, SignalResult]
    direction: str  # "BULLISH", "BEARISH", "NEUTRAL"
    timestamp: datetime
    
    # Signal recommendation
    should_recommend: bool = False
    rejection_reason: str = ""
    
    @property
    def is_bullish(self) -> bool:
        """Check if signal is bullish."""
        return self.composite_score >= 0.3
    
    @property
    def is_bearish(self) -> bool:
        """Check if signal is bearish."""
        return self.composite_score <= -0.3
    
    def get_signal_breakdown(self) -> dict[str, dict]:
        """Get breakdown of all signals."""
        return {
            name: {
                "score": sig.score,
                "confidence": sig.confidence,
                "reason": sig.reason,
                "cached": sig.cached
            }
            for name, sig in self.signals.items()
        }


class SignalEngine:
    """
    Master signal aggregator.
    
    Combines 12 signal sources with regime-weighted scoring:
    1. OI Analysis
    2. IV Analysis
    3. Max Pain & GEX
    4. India VIX
    5. Price Action
    6. Technicals
    7. Global Cues
    8. FII/DII Flow
    9. Straddle Pricing
    10. News & Events
    11. Market Regime (meta-signal)
    12. Greeks Composite
    
    Weighting varies by regime:
    - RANGE_BOUND: OI, IV, max pain weighted higher
    - TRENDING: Price action, technicals weighted higher
    - HIGH_VOL: VIX, news, IV weighted higher
    """
    
    # Signal names
    SIGNAL_NAMES = [
        "oi_analysis",
        "iv_analysis",
        "max_pain_gex",
        "india_vix",
        "price_action",
        "technicals",
        "global_cues",
        "fii_dii",
        "straddle_pricing",
        "news_events",
        "regime",
        "greeks_composite",
    ]
    
    def __init__(self) -> None:
        """Initialize signal engine."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        
        # Signal cache for timeout fallback
        self._signal_cache: dict[str, SignalResult] = {}
        
        # Signal compute functions (to be registered)
        self._signal_funcs: dict[str, Callable[..., Awaitable[SignalResult]]] = {}
    
    def register_signal(
        self,
        name: str,
        compute_func: Callable[..., Awaitable[SignalResult]]
    ) -> None:
        """Register a signal computation function."""
        self._signal_funcs[name] = compute_func
    
    def get_weights(self, regime: MarketRegime) -> dict[str, float]:
        """
        Get signal weights for a regime.
        
        Weights sum to 1.0 for each regime.
        """
        settings = self._settings
        
        if regime == MarketRegime.RANGE_BOUND:
            return {
                "oi_analysis": settings.weight_oi_range,
                "iv_analysis": settings.weight_iv_range,
                "max_pain_gex": settings.weight_max_pain_range,
                "straddle_pricing": settings.weight_straddle_range,
                "greeks_composite": settings.weight_greeks_range,
                "price_action": settings.weight_price_action_range,
                "technicals": settings.weight_technicals_range,
                "india_vix": settings.weight_vix_range,
                "global_cues": 0.0,
                "fii_dii": 0.0,
                "news_events": 0.0,
                "regime": 0.0,
            }
        
        elif regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            return {
                "price_action": settings.weight_price_action_trend,
                "technicals": settings.weight_technicals_trend,
                "global_cues": settings.weight_global_trend,
                "oi_analysis": settings.weight_oi_trend,
                "fii_dii": settings.weight_fii_trend,
                "india_vix": settings.weight_vix_trend,
                "iv_analysis": settings.weight_iv_trend,
                "max_pain_gex": 0.0,
                "straddle_pricing": 0.0,
                "greeks_composite": 0.0,
                "news_events": 0.0,
                "regime": 0.0,
            }
        
        elif regime == MarketRegime.HIGH_VOLATILITY:
            return {
                "india_vix": settings.weight_vix_highvol,
                "news_events": settings.weight_news_highvol,
                "iv_analysis": settings.weight_iv_highvol,
                "straddle_pricing": settings.weight_straddle_highvol,
                "greeks_composite": settings.weight_greeks_highvol,
                "oi_analysis": settings.weight_oi_highvol,
                "price_action": 0.0,
                "technicals": 0.0,
                "global_cues": 0.0,
                "fii_dii": 0.0,
                "max_pain_gex": 0.0,
                "regime": 0.0,
            }
        
        else:
            # Default equal weights
            weight = 1.0 / len(self.SIGNAL_NAMES)
            return {name: weight for name in self.SIGNAL_NAMES}
    
    async def compute_all_signals(
        self,
        underlying: str,
        chain: OptionChainSnapshot | None,
        price_data: pd.DataFrame,
        regime: RegimeClassification,
        **kwargs
    ) -> AggregatedSignal:
        """
        Compute all signals and aggregate.
        
        Args:
            underlying: Underlying symbol
            chain: Option chain snapshot
            price_data: Price OHLCV data
            regime: Current market regime
            **kwargs: Additional data passed to signals
            
        Returns:
            AggregatedSignal with composite score
        """
        now = datetime.now(self._ist)
        signals: dict[str, SignalResult] = {}
        
        # Check pre-conditions
        rejection = self._check_preconditions(underlying, now)
        if rejection:
            return AggregatedSignal(
                composite_score=0.0,
                composite_confidence=0.0,
                regime=regime.regime,
                signals={},
                direction="NEUTRAL",
                timestamp=now,
                should_recommend=False,
                rejection_reason=rejection,
            )
        
        # Compute each signal with timeout
        timeout = self._settings.scan_interval_seconds * 0.8
        
        for name in self.SIGNAL_NAMES:
            try:
                if name in self._signal_funcs:
                    result = await asyncio.wait_for(
                        self._signal_funcs[name](
                            underlying=underlying,
                            chain=chain,
                            price_data=price_data,
                            regime=regime,
                            **kwargs
                        ),
                        timeout=timeout / len(self.SIGNAL_NAMES)
                    )
                    signals[name] = result
                    self._signal_cache[name] = result
                else:
                    # Use neutral signal if not registered
                    signals[name] = SignalResult(
                        name=name,
                        score=0.0,
                        confidence=0.0,
                        reason="Signal not configured",
                        timestamp=now,
                    )
                    
            except asyncio.TimeoutError:
                logger.warning(f"Signal {name} timed out, using cache")
                if name in self._signal_cache:
                    cached = self._signal_cache[name]
                    cached.cached = True
                    signals[name] = cached
                else:
                    signals[name] = SignalResult(
                        name=name,
                        score=0.0,
                        confidence=0.0,
                        reason="Timeout, no cache",
                        timestamp=now,
                    )
                    
            except Exception as e:
                logger.error(f"Signal {name} failed: {e}")
                signals[name] = SignalResult(
                    name=name,
                    score=0.0,
                    confidence=0.0,
                    reason=f"Error: {str(e)[:50]}",
                    timestamp=now,
                )
        
        # Aggregate with regime weights
        weights = self.get_weights(regime.regime)
        
        composite_score = 0.0
        total_weight = 0.0
        confidence_sum = 0.0
        
        for name, signal in signals.items():
            weight = weights.get(name, 0.0)
            if weight > 0:
                composite_score += signal.score * weight * signal.confidence
                confidence_sum += signal.confidence * weight
                total_weight += weight
        
        if total_weight > 0:
            composite_score /= total_weight
            composite_confidence = confidence_sum / total_weight
        else:
            composite_score = 0.0
            composite_confidence = 0.0
        
        # Determine direction
        if composite_score >= 0.3:
            direction = "BULLISH"
        elif composite_score <= -0.3:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"
        
        # Check if should recommend
        should_recommend = (
            abs(composite_score) >= self._settings.min_composite_score
            and composite_confidence >= self._settings.min_confidence
        )
        
        rejection_reason = ""
        if not should_recommend:
            if abs(composite_score) < self._settings.min_composite_score:
                rejection_reason = f"Score {composite_score:.2f} below threshold"
            elif composite_confidence < self._settings.min_confidence:
                rejection_reason = f"Confidence {composite_confidence:.2f} below threshold"
        
        return AggregatedSignal(
            composite_score=composite_score,
            composite_confidence=composite_confidence,
            regime=regime.regime,
            signals=signals,
            direction=direction,
            timestamp=now,
            should_recommend=should_recommend,
            rejection_reason=rejection_reason,
        )
    
    def _check_preconditions(
        self,
        underlying: str,
        now: datetime
    ) -> str | None:
        """
        Check preconditions for signal generation.
        
        Returns rejection reason or None if OK.
        """
        # Check ban list
        ban_checker = get_ban_list_checker()
        if ban_checker.is_banned(underlying):
            return f"{underlying} is in F&O ban list"
        
        # Check circuit breaker
        circuit_breaker = get_circuit_breaker()
        if circuit_breaker.is_halted:
            return "Market is halted (circuit breaker)"
        
        # Check event blackout
        calendar = get_nse_calendar()
        if calendar.is_event_blackout():
            return "Event blackout period active"
        
        # Check time cutoff
        cutoff_parts = self._settings.no_new_signals_after.split(":")
        cutoff = time(int(cutoff_parts[0]), int(cutoff_parts[1]))
        
        if now.time() >= cutoff:
            return f"Past signal cutoff time ({self._settings.no_new_signals_after})"
        
        return None


# Global instance
_signal_engine: SignalEngine | None = None


def get_signal_engine() -> SignalEngine:
    """Get or create global signal engine."""
    global _signal_engine
    if _signal_engine is None:
        _signal_engine = SignalEngine()
    return _signal_engine
