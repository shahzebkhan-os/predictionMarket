"""
Max Pain & GEX Signal.

Signal 3: Max Pain calculation and Gamma Exposure analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from zoneinfo import ZoneInfo

from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class MaxPainGEXMetrics:
    """Max Pain and GEX metrics."""
    max_pain: float
    max_pain_distance: float  # % from spot
    gex: float
    gex_sign: str  # "POSITIVE" or "NEGATIVE"
    spot_price: float
    is_near_expiry: bool  # DTE <= 2


class MaxPainGEXAnalyzer:
    """
    Analyzes Max Pain and Gamma Exposure.
    
    Max Pain:
    - Strike where option writers incur minimum loss
    - Near expiry (DTE < 2), index gravitates to max pain
    - Distance from max pain indicates pull direction
    
    GEX (Gamma Exposure):
    - GEX = Σ(CE_gamma × CE_OI − PE_gamma × PE_OI) × spot × lot_size
    - Positive GEX → Range-bound (dealers hedge pins price)
    - Negative GEX → Trending/volatile
    - GEX sign flip → Volatility expansion signal
    """
    
    # Thresholds
    NEAR_EXPIRY_DTE = 2
    MAX_PAIN_INFLUENCE_DTE = 3  # Max pain most influential within 3 DTE
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._prev_gex_sign: str | None = None
    
    def analyze(
        self,
        chain: OptionChainSnapshot,
        dte: int = 5
    ) -> MaxPainGEXMetrics:
        """
        Analyze max pain and GEX from option chain.
        
        Args:
            chain: Option chain snapshot
            dte: Days to expiry
            
        Returns:
            MaxPainGEXMetrics with analysis results
        """
        spot = chain.spot_price
        
        # Calculate max pain
        max_pain = chain.get_max_pain()
        
        # Distance from spot
        max_pain_distance = ((max_pain - spot) / spot) * 100 if spot > 0 else 0
        
        # Calculate GEX
        gex = chain.get_gex()
        gex_sign = "POSITIVE" if gex >= 0 else "NEGATIVE"
        
        # Check for GEX flip
        if self._prev_gex_sign is not None and self._prev_gex_sign != gex_sign:
            logger.info(f"GEX sign flipped from {self._prev_gex_sign} to {gex_sign}")
        
        self._prev_gex_sign = gex_sign
        
        return MaxPainGEXMetrics(
            max_pain=max_pain,
            max_pain_distance=max_pain_distance,
            gex=gex,
            gex_sign=gex_sign,
            spot_price=spot,
            is_near_expiry=dte <= self.NEAR_EXPIRY_DTE,
        )
    
    def compute_signal(
        self,
        chain: OptionChainSnapshot,
        dte: int = 5,
        **kwargs
    ) -> SignalResult:
        """
        Compute max pain & GEX signal.
        
        Returns:
            SignalResult with score from -1 to +1
        """
        now = datetime.now(self._ist)
        
        if not chain.is_valid:
            return SignalResult(
                name="max_pain_gex",
                score=0.0,
                confidence=0.0,
                reason="Invalid/stale option chain",
                timestamp=now,
            )
        
        metrics = self.analyze(chain, dte)
        
        score = 0.0
        reasons = []
        confidence = 0.5
        
        # Max pain influence (stronger near expiry)
        if dte <= self.MAX_PAIN_INFLUENCE_DTE:
            # Score based on distance to max pain
            # If spot < max pain → expect pull up (bullish)
            # If spot > max pain → expect pull down (bearish)
            
            if abs(metrics.max_pain_distance) > 0.1:  # More than 0.1% away
                max_pain_score = -metrics.max_pain_distance / 2  # Pull towards max pain
                max_pain_score = max(-0.5, min(0.5, max_pain_score))
                
                score += max_pain_score * (1 - dte / self.MAX_PAIN_INFLUENCE_DTE)
                
                if metrics.max_pain_distance > 0:
                    reasons.append(f"Max pain {metrics.max_pain:.0f} below spot (bearish pull)")
                else:
                    reasons.append(f"Max pain {metrics.max_pain:.0f} above spot (bullish pull)")
                
                confidence += 0.2
        
        # GEX influence
        if metrics.gex_sign == "POSITIVE":
            # Positive GEX = range-bound, mean reversion
            # Score towards 0 (neutral, range-bound)
            gex_score = 0.0
            reasons.append("Positive GEX (range-bound expected)")
        else:
            # Negative GEX = trending/volatile
            # Don't add directional bias, but flag volatility
            gex_score = 0.0
            reasons.append("Negative GEX (volatility expansion)")
            confidence += 0.1
        
        score += gex_score
        
        # Near expiry flag
        if metrics.is_near_expiry:
            confidence += 0.2
            reasons.append(f"Near expiry (DTE={dte})")
        
        return SignalResult(
            name="max_pain_gex",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "Max pain/GEX neutral",
            timestamp=now,
        )


# Global instance
_max_pain_analyzer: MaxPainGEXAnalyzer | None = None


def get_max_pain_analyzer() -> MaxPainGEXAnalyzer:
    """Get or create global max pain analyzer."""
    global _max_pain_analyzer
    if _max_pain_analyzer is None:
        _max_pain_analyzer = MaxPainGEXAnalyzer()
    return _max_pain_analyzer


async def compute_max_pain_signal(
    chain: OptionChainSnapshot | None,
    dte: int = 5,
    **kwargs
) -> SignalResult:
    """Compute max pain & GEX signal (async wrapper)."""
    analyzer = get_max_pain_analyzer()
    
    if chain is None:
        return SignalResult(
            name="max_pain_gex",
            score=0.0,
            confidence=0.0,
            reason="No option chain data",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
    
    return analyzer.compute_signal(chain, dte, **kwargs)
