"""
Market Regime Detector.

Classifies market into regimes: TRENDING_UP, TRENDING_DOWN, RANGE_BOUND, HIGH_VOLATILITY.
Each regime has different strategy recommendations and position sizing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.market.option_chain import OptionChainSnapshot

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeClassification:
    """Result of regime classification."""
    regime: MarketRegime
    confidence: float
    reasons: list[str]
    timestamp: datetime
    
    # Sub-scores
    trend_score: float  # -1 to +1 (negative = down, positive = up)
    volatility_score: float  # 0 to 1 (higher = more volatile)
    range_score: float  # 0 to 1 (higher = more range-bound)
    
    @property
    def size_multiplier(self) -> float:
        """Get position size multiplier for this regime."""
        multipliers = {
            MarketRegime.RANGE_BOUND: 1.0,
            MarketRegime.TRENDING_UP: 0.7,
            MarketRegime.TRENDING_DOWN: 0.7,
            MarketRegime.HIGH_VOLATILITY: 0.4,
            MarketRegime.UNKNOWN: 0.5,
        }
        return multipliers.get(self.regime, 0.5)


class RegimeDetector:
    """
    Detects current market regime.
    
    Classification criteria:
    
    TRENDING_UP:
    - Price > 20 EMA
    - Supertrend bullish
    - Above previous day high after 10:30
    - Strategy: Bull call spread, sell OTM PE
    
    TRENDING_DOWN:
    - Price < 20 EMA
    - Supertrend bearish
    - Strategy: Bear put spread, sell OTM CE
    
    RANGE_BOUND:
    - Price within ±0.3% of VWAP
    - Positive GEX
    - Strategy: Iron condor, short straddle (IVR > 60)
    
    HIGH_VOLATILITY:
    - VIX > 18 OR VIX spike > +10% intraday
    - Negative GEX
    - Strategy: Long straddle only, no short vega
    """
    
    # Thresholds
    VWAP_RANGE_PCT = 0.3  # ±0.3% for range-bound
    VIX_HIGH_THRESHOLD = 18.0
    VIX_SPIKE_THRESHOLD = 10.0  # +10% intraday
    
    def __init__(self) -> None:
        """Initialize regime detector."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        self._last_regime: MarketRegime = MarketRegime.UNKNOWN
        self._vix_open: float | None = None
    
    def classify(
        self,
        price_data: pd.DataFrame,
        chain: OptionChainSnapshot | None = None,
        vix: float = 0.0,
        vwap: float | None = None,
        prev_day_high: float | None = None,
        prev_day_low: float | None = None,
    ) -> RegimeClassification:
        """
        Classify current market regime.
        
        Args:
            price_data: OHLCV DataFrame with at least 20 rows
            chain: Option chain snapshot for GEX calculation
            vix: Current India VIX value
            vwap: Current VWAP
            prev_day_high: Previous day's high
            prev_day_low: Previous day's low
            
        Returns:
            RegimeClassification with regime and confidence
        """
        now = datetime.now(self._ist)
        reasons: list[str] = []
        
        # Track VIX for spike detection
        if self._vix_open is None and vix > 0:
            self._vix_open = vix
        
        # Calculate indicators
        trend_score = self._calculate_trend_score(price_data, prev_day_high)
        volatility_score = self._calculate_volatility_score(vix)
        range_score = self._calculate_range_score(
            price_data, vwap, chain
        )
        
        # Classify regime
        regime = MarketRegime.UNKNOWN
        confidence = 0.5
        
        # High volatility takes precedence
        if volatility_score > 0.7:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = volatility_score
            reasons.append(f"VIX elevated at {vix:.1f}")
            
            # Check VIX spike
            if self._vix_open and vix > 0:
                vix_change = ((vix - self._vix_open) / self._vix_open) * 100
                if vix_change >= self.VIX_SPIKE_THRESHOLD:
                    confidence = min(1.0, confidence + 0.2)
                    reasons.append(f"VIX spike +{vix_change:.1f}% intraday")
        
        # Check GEX for volatility/range
        elif chain is not None:
            gex = chain.get_gex()
            if gex < 0:
                regime = MarketRegime.HIGH_VOLATILITY
                confidence = 0.6
                reasons.append("Negative GEX indicates volatility")
            elif gex > 0 and range_score > 0.6:
                regime = MarketRegime.RANGE_BOUND
                confidence = range_score
                reasons.append("Positive GEX supports range-bound")
        
        # Trending detection
        if regime == MarketRegime.UNKNOWN:
            if trend_score > 0.3:
                regime = MarketRegime.TRENDING_UP
                confidence = abs(trend_score)
                reasons.append("Price above EMA with bullish structure")
            elif trend_score < -0.3:
                regime = MarketRegime.TRENDING_DOWN
                confidence = abs(trend_score)
                reasons.append("Price below EMA with bearish structure")
            else:
                regime = MarketRegime.RANGE_BOUND
                confidence = range_score
                reasons.append("No clear trend, range-bound")
        
        # Time-based adjustments
        hour = now.hour
        if hour < 10:
            # First hour: reduce confidence
            confidence *= 0.8
            reasons.append("Early session - reduced confidence")
        
        self._last_regime = regime
        
        return RegimeClassification(
            regime=regime,
            confidence=min(1.0, confidence),
            reasons=reasons,
            timestamp=now,
            trend_score=trend_score,
            volatility_score=volatility_score,
            range_score=range_score,
        )
    
    def _calculate_trend_score(
        self,
        df: pd.DataFrame,
        prev_high: float | None = None
    ) -> float:
        """
        Calculate trend score from -1 to +1.
        
        Factors:
        - Price vs 20 EMA
        - Price vs 9 EMA
        - Price vs previous day high/low
        """
        if df.empty or len(df) < 20:
            return 0.0
        
        score = 0.0
        current_price = df["close"].iloc[-1]
        
        # 20 EMA comparison
        ema20 = df["close"].ewm(span=20).mean().iloc[-1]
        if current_price > ema20:
            score += 0.4
        else:
            score -= 0.4
        
        # 9 EMA comparison
        ema9 = df["close"].ewm(span=9).mean().iloc[-1]
        if current_price > ema9:
            score += 0.2
        else:
            score -= 0.2
        
        # EMA crossover
        if ema9 > ema20:
            score += 0.2
        else:
            score -= 0.2
        
        # Prev day high/low
        if prev_high and current_price > prev_high:
            score += 0.2
        
        return max(-1.0, min(1.0, score))
    
    def _calculate_volatility_score(self, vix: float) -> float:
        """
        Calculate volatility score from 0 to 1.
        
        Higher score = more volatile environment.
        """
        if vix <= 0:
            return 0.5  # Unknown
        
        # VIX < 12: Low volatility (score 0.1-0.3)
        # VIX 12-18: Normal (score 0.3-0.5)
        # VIX 18-25: Elevated (score 0.5-0.8)
        # VIX > 25: High (score 0.8-1.0)
        
        if vix < 12:
            return 0.1 + (vix / 12) * 0.2
        elif vix < 18:
            return 0.3 + ((vix - 12) / 6) * 0.2
        elif vix < 25:
            return 0.5 + ((vix - 18) / 7) * 0.3
        else:
            return min(1.0, 0.8 + ((vix - 25) / 10) * 0.2)
    
    def _calculate_range_score(
        self,
        df: pd.DataFrame,
        vwap: float | None,
        chain: OptionChainSnapshot | None
    ) -> float:
        """
        Calculate range-bound score from 0 to 1.
        
        Higher score = more range-bound.
        """
        score = 0.5  # Neutral
        
        if df.empty:
            return score
        
        current_price = df["close"].iloc[-1]
        
        # VWAP proximity
        if vwap and vwap > 0:
            vwap_distance_pct = abs(current_price - vwap) / vwap * 100
            if vwap_distance_pct <= self.VWAP_RANGE_PCT:
                score += 0.3
            elif vwap_distance_pct <= 0.5:
                score += 0.15
        
        # GEX
        if chain:
            gex = chain.get_gex()
            if gex > 0:
                score += 0.2
        
        # Price range contraction (Bollinger squeeze)
        if len(df) >= 20:
            bb_std = df["close"].rolling(20).std().iloc[-1]
            bb_mean = df["close"].rolling(20).mean().iloc[-1]
            if bb_mean > 0:
                bandwidth = (bb_std / bb_mean) * 100
                if bandwidth < 1.0:  # Squeeze
                    score += 0.2
        
        return min(1.0, score)


# Global instance
_regime_detector: RegimeDetector | None = None


def get_regime_detector() -> RegimeDetector:
    """Get or create global regime detector."""
    global _regime_detector
    if _regime_detector is None:
        _regime_detector = RegimeDetector()
    return _regime_detector
