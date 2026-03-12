"""
Signals API Routes.

Endpoints for signal data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


class SignalScore(BaseModel):
    """Individual signal score."""
    name: str
    score: float
    confidence: float
    reason: str


class AggregatedSignal(BaseModel):
    """Aggregated signal response."""
    timestamp: str
    underlying: str
    regime: str
    composite_score: float
    composite_confidence: float
    should_recommend: bool
    signals: list[SignalScore]


class SignalHistoryItem(BaseModel):
    """Signal history item."""
    timestamp: str
    composite_score: float
    composite_confidence: float
    regime: str


@router.get("/latest", response_model=AggregatedSignal)
async def get_latest_signal() -> AggregatedSignal:
    """
    Get the latest aggregated signal.
    
    Returns:
        AggregatedSignal with composite score, confidence, and individual signals
    """
    from nse_advisor.signals.engine import get_signal_engine
    from nse_advisor.market.regime import get_regime_classifier
    
    settings = get_settings()
    
    # Get signal engine state
    signal_engine = get_signal_engine()
    latest = signal_engine.get_latest_result()
    
    # Get regime
    classifier = get_regime_classifier()
    current_regime = classifier.get_current_regime()
    regime = current_regime.regime.value if current_regime else "UNKNOWN"
    
    if not latest:
        # Return empty signal if no data yet
        return AggregatedSignal(
            timestamp=datetime.now(IST).isoformat(),
            underlying=settings.primary_underlying,
            regime=regime,
            composite_score=0.0,
            composite_confidence=0.0,
            should_recommend=False,
            signals=[],
        )
    
    # Build signal list
    signals = []
    for signal in latest.signals:
        signals.append(SignalScore(
            name=signal.name,
            score=signal.score,
            confidence=signal.confidence,
            reason=signal.reason,
        ))
    
    return AggregatedSignal(
        timestamp=latest.timestamp.isoformat() if latest.timestamp else datetime.now(IST).isoformat(),
        underlying=settings.primary_underlying,
        regime=regime,
        composite_score=latest.composite_score,
        composite_confidence=latest.composite_confidence,
        should_recommend=latest.should_recommend,
        signals=signals,
    )


@router.get("/history", response_model=list[SignalHistoryItem])
async def get_signal_history(
    n: int = Query(default=50, ge=1, le=500),
) -> list[SignalHistoryItem]:
    """
    Get signal history.
    
    Args:
        n: Number of historical signals to return
        
    Returns:
        List of historical signal summaries
    """
    from nse_advisor.signals.engine import get_signal_engine
    
    signal_engine = get_signal_engine()
    history = signal_engine.get_history(n)
    
    return [
        SignalHistoryItem(
            timestamp=item.timestamp.isoformat() if item.timestamp else "",
            composite_score=item.composite_score,
            composite_confidence=item.composite_confidence,
            regime=item.regime if hasattr(item, 'regime') else "UNKNOWN",
        )
        for item in history
    ]


@router.get("/recommendations")
async def get_recent_recommendations(
    n: int = Query(default=10, ge=1, le=50),
) -> list[dict[str, Any]]:
    """
    Get recent trade recommendations.
    
    Args:
        n: Number of recommendations to return
        
    Returns:
        List of recent recommendations
    """
    from nse_advisor.recommender.engine import get_recommender
    
    recommender = get_recommender()
    recommendations = recommender.get_recent(n)
    
    return [
        {
            "id": rec.recommendation_id,
            "timestamp": rec.timestamp.isoformat() if rec.timestamp else None,
            "strategy": rec.strategy_name,
            "underlying": rec.underlying,
            "expiry": rec.expiry.isoformat() if rec.expiry else None,
            "urgency": rec.urgency.value,
            "regime": rec.regime_at_generation,
            "composite_score": rec.composite_score,
            "legs": [
                {
                    "symbol": leg.tradingsymbol,
                    "action": leg.action,
                    "strike": leg.strike,
                    "option_type": leg.option_type,
                    "expiry": leg.expiry.isoformat(),
                    "suggested_price": leg.suggested_entry_price,
                    "lots": leg.suggested_lots,
                }
                for leg in rec.legs
            ],
            "max_profit": rec.max_profit,
            "max_loss": rec.max_loss,
            "breakevens": rec.breakevens,
            "risk_warnings": rec.risk_warnings,
        }
        for rec in recommendations
    ]
