"""Master signal aggregator for the 12-signal engine.

All signals return: score(-1.0 to +1.0), confidence(0-1), reason string.
Regime-weighted composite scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.market.regime import MarketRegime, RegimeResult

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class SignalType(str, Enum):
    """Signal type enumeration."""

    OI_ANALYSIS = "OI_ANALYSIS"
    IV_ANALYSIS = "IV_ANALYSIS"
    MAX_PAIN_GEX = "MAX_PAIN_GEX"
    INDIA_VIX = "INDIA_VIX"
    PRICE_ACTION = "PRICE_ACTION"
    TECHNICALS = "TECHNICALS"
    GLOBAL_CUES = "GLOBAL_CUES"
    FII_DII = "FII_DII"
    STRADDLE_PRICING = "STRADDLE_PRICING"
    NEWS_EVENTS = "NEWS_EVENTS"
    MARKET_REGIME = "MARKET_REGIME"
    GREEKS_COMPOSITE = "GREEKS_COMPOSITE"


class SignalDirection(str, Enum):
    """Signal direction."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    SELL_PREMIUM = "SELL_PREMIUM"
    BUY_PREMIUM = "BUY_PREMIUM"


@dataclass
class Signal:
    """Individual signal result."""

    signal_type: SignalType
    score: float  # -1.0 to +1.0 (negative=bearish, positive=bullish)
    confidence: float  # 0 to 1
    direction: SignalDirection
    reason: str
    components: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(IST))

    @property
    def weighted_score(self) -> float:
        """Get confidence-weighted score."""
        return self.score * self.confidence

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "signal_type": self.signal_type.value,
            "score": self.score,
            "confidence": self.confidence,
            "direction": self.direction.value,
            "reason": self.reason,
            "components": self.components,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class CompositeSignal:
    """Composite signal from all signal engines."""

    overall_score: float  # -1.0 to +1.0
    overall_confidence: float  # 0 to 1
    direction: SignalDirection
    signals: dict[SignalType, Signal]
    regime: MarketRegime
    regime_confidence: float
    recommended_action: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(IST))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "overall_score": self.overall_score,
            "overall_confidence": self.overall_confidence,
            "direction": self.direction.value,
            "regime": self.regime.value,
            "regime_confidence": self.regime_confidence,
            "recommended_action": self.recommended_action,
            "signals": {k.value: v.to_dict() for k, v in self.signals.items()},
            "timestamp": self.timestamp.isoformat(),
        }


class SignalWeights:
    """Signal weights for different market regimes."""

    # Base weights (sum to 1.0)
    BASE_WEIGHTS = {
        SignalType.OI_ANALYSIS: 0.12,
        SignalType.IV_ANALYSIS: 0.12,
        SignalType.MAX_PAIN_GEX: 0.10,
        SignalType.INDIA_VIX: 0.08,
        SignalType.PRICE_ACTION: 0.10,
        SignalType.TECHNICALS: 0.10,
        SignalType.GLOBAL_CUES: 0.08,
        SignalType.FII_DII: 0.06,
        SignalType.STRADDLE_PRICING: 0.08,
        SignalType.NEWS_EVENTS: 0.04,
        SignalType.MARKET_REGIME: 0.06,
        SignalType.GREEKS_COMPOSITE: 0.06,
    }

    # Regime-specific adjustments (multipliers)
    REGIME_ADJUSTMENTS = {
        MarketRegime.TRENDING_UP: {
            SignalType.TECHNICALS: 1.5,
            SignalType.PRICE_ACTION: 1.3,
            SignalType.OI_ANALYSIS: 1.2,
            SignalType.IV_ANALYSIS: 0.8,
            SignalType.MAX_PAIN_GEX: 0.8,
        },
        MarketRegime.TRENDING_DOWN: {
            SignalType.TECHNICALS: 1.5,
            SignalType.PRICE_ACTION: 1.3,
            SignalType.OI_ANALYSIS: 1.2,
            SignalType.IV_ANALYSIS: 0.8,
            SignalType.MAX_PAIN_GEX: 0.8,
        },
        MarketRegime.RANGE_BOUND: {
            SignalType.IV_ANALYSIS: 1.5,
            SignalType.MAX_PAIN_GEX: 1.3,
            SignalType.OI_ANALYSIS: 1.2,
            SignalType.STRADDLE_PRICING: 1.2,
            SignalType.TECHNICALS: 0.8,
        },
        MarketRegime.HIGH_VOLATILITY: {
            SignalType.INDIA_VIX: 1.5,
            SignalType.NEWS_EVENTS: 1.5,
            SignalType.GREEKS_COMPOSITE: 1.3,
            SignalType.IV_ANALYSIS: 1.2,
            SignalType.TECHNICALS: 0.7,
            SignalType.OI_ANALYSIS: 0.8,
        },
    }

    @classmethod
    def get_weights(cls, regime: MarketRegime) -> dict[SignalType, float]:
        """Get regime-adjusted weights.

        Args:
            regime: Current market regime

        Returns:
            Dict of signal weights
        """
        weights = cls.BASE_WEIGHTS.copy()

        # Apply regime adjustments
        adjustments = cls.REGIME_ADJUSTMENTS.get(regime, {})
        for signal_type, multiplier in adjustments.items():
            weights[signal_type] = weights.get(signal_type, 0) * multiplier

        # Normalize to sum to 1.0
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}


class SignalAggregator:
    """Master signal aggregator.

    Combines all 12 signals with regime-weighted scoring.
    """

    # Thresholds for action recommendations
    STRONG_BULLISH_THRESHOLD = 0.5
    BULLISH_THRESHOLD = 0.2
    BEARISH_THRESHOLD = -0.2
    STRONG_BEARISH_THRESHOLD = -0.5
    MIN_CONFIDENCE_FOR_ACTION = 0.4

    def __init__(self) -> None:
        """Initialize aggregator."""
        self._signals: dict[SignalType, Signal] = {}
        self._last_composite: CompositeSignal | None = None

    def add_signal(self, signal: Signal) -> None:
        """Add or update a signal.

        Args:
            signal: Signal to add
        """
        self._signals[signal.signal_type] = signal
        logger.debug(
            "signal_added",
            signal_type=signal.signal_type.value,
            score=signal.score,
            confidence=signal.confidence,
        )

    def get_signal(self, signal_type: SignalType) -> Signal | None:
        """Get a specific signal.

        Args:
            signal_type: Signal type

        Returns:
            Signal or None
        """
        return self._signals.get(signal_type)

    def compute_composite(
        self, regime_result: RegimeResult | None = None
    ) -> CompositeSignal:
        """Compute composite signal from all signals.

        Args:
            regime_result: Current regime detection result

        Returns:
            CompositeSignal
        """
        regime = regime_result.regime if regime_result else MarketRegime.UNKNOWN
        regime_confidence = regime_result.confidence if regime_result else 0.0

        # Get regime-adjusted weights
        weights = SignalWeights.get_weights(regime)

        # Calculate weighted score
        total_score = 0.0
        total_confidence = 0.0
        weighted_signals = 0

        for signal_type, weight in weights.items():
            signal = self._signals.get(signal_type)
            if signal:
                total_score += signal.weighted_score * weight
                total_confidence += signal.confidence * weight
                weighted_signals += 1

        # Normalize
        if weighted_signals > 0:
            # Overall confidence is average of signal confidences
            overall_confidence = total_confidence
        else:
            total_score = 0.0
            overall_confidence = 0.0

        # Clamp score to [-1, 1]
        overall_score = max(-1.0, min(1.0, total_score))

        # Determine direction
        direction = self._determine_direction(overall_score)

        # Generate recommended action
        recommended_action = self._recommend_action(
            overall_score, overall_confidence, regime
        )

        composite = CompositeSignal(
            overall_score=overall_score,
            overall_confidence=overall_confidence,
            direction=direction,
            signals=self._signals.copy(),
            regime=regime,
            regime_confidence=regime_confidence,
            recommended_action=recommended_action,
        )

        self._last_composite = composite

        logger.info(
            "composite_signal_computed",
            overall_score=overall_score,
            overall_confidence=overall_confidence,
            direction=direction.value,
            regime=regime.value,
            recommended_action=recommended_action,
        )

        return composite

    def _determine_direction(self, score: float) -> SignalDirection:
        """Determine signal direction from score.

        Args:
            score: Composite score

        Returns:
            SignalDirection
        """
        if score >= self.STRONG_BULLISH_THRESHOLD:
            return SignalDirection.BULLISH
        elif score >= self.BULLISH_THRESHOLD:
            return SignalDirection.BULLISH
        elif score <= self.STRONG_BEARISH_THRESHOLD:
            return SignalDirection.BEARISH
        elif score <= self.BEARISH_THRESHOLD:
            return SignalDirection.BEARISH
        else:
            return SignalDirection.NEUTRAL

    def _recommend_action(
        self, score: float, confidence: float, regime: MarketRegime
    ) -> str:
        """Generate recommended action.

        Args:
            score: Composite score
            confidence: Overall confidence
            regime: Market regime

        Returns:
            Recommended action string
        """
        if confidence < self.MIN_CONFIDENCE_FOR_ACTION:
            return "WAIT - Low confidence"

        if regime == MarketRegime.HIGH_VOLATILITY:
            if abs(score) < self.BULLISH_THRESHOLD:
                return "AVOID - High volatility, wait for clarity"
            elif score > 0:
                return "CONSIDER - Long straddle if expecting continuation"
            else:
                return "CONSIDER - Long straddle if expecting reversal"

        if regime == MarketRegime.RANGE_BOUND:
            iv_signal = self._signals.get(SignalType.IV_ANALYSIS)
            if iv_signal and iv_signal.components.get("ivr", 0) > 70:
                return "EXECUTE - Short straddle/Iron condor (High IVR, Range-bound)"
            return "CONSIDER - Iron condor (Range-bound)"

        if score >= self.STRONG_BULLISH_THRESHOLD:
            return "EXECUTE - Bull call spread or Sell OTM PE"
        elif score >= self.BULLISH_THRESHOLD:
            return "CONSIDER - Bull call spread"
        elif score <= self.STRONG_BEARISH_THRESHOLD:
            return "EXECUTE - Bear put spread or Sell OTM CE"
        elif score <= self.BEARISH_THRESHOLD:
            return "CONSIDER - Bear put spread"
        else:
            return "WAIT - Neutral signal"

    def clear_signals(self) -> None:
        """Clear all signals."""
        self._signals.clear()
        self._last_composite = None

    @property
    def last_composite(self) -> CompositeSignal | None:
        """Get last computed composite signal."""
        return self._last_composite

    def get_signal_summary(self) -> dict[str, Any]:
        """Get summary of all signals.

        Returns:
            Summary dict
        """
        if not self._last_composite:
            return {"status": "No composite signal computed"}

        return {
            "overall_score": self._last_composite.overall_score,
            "overall_confidence": self._last_composite.overall_confidence,
            "direction": self._last_composite.direction.value,
            "regime": self._last_composite.regime.value,
            "recommended_action": self._last_composite.recommended_action,
            "signals": {
                k.value: {
                    "score": v.score,
                    "confidence": v.confidence,
                    "direction": v.direction.value,
                }
                for k, v in self._signals.items()
            },
        }


def create_signal(
    signal_type: SignalType,
    score: float,
    confidence: float,
    reason: str,
    components: dict[str, float] | None = None,
) -> Signal:
    """Helper to create a signal.

    Args:
        signal_type: Signal type
        score: Score (-1 to +1)
        confidence: Confidence (0 to 1)
        reason: Reason string
        components: Component scores

    Returns:
        Signal object
    """
    # Clamp values
    score = max(-1.0, min(1.0, score))
    confidence = max(0.0, min(1.0, confidence))

    # Determine direction
    if score > 0.1:
        direction = SignalDirection.BULLISH
    elif score < -0.1:
        direction = SignalDirection.BEARISH
    else:
        direction = SignalDirection.NEUTRAL

    return Signal(
        signal_type=signal_type,
        score=score,
        confidence=confidence,
        direction=direction,
        reason=reason,
        components=components or {},
    )
