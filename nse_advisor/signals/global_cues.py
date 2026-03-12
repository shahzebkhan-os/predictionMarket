"""
Global Cues Signal.

Signal 7: GIFT Nifty, SPX, Nasdaq, DXY, Crude, USD/INR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time

from zoneinfo import ZoneInfo

from nse_advisor.data.yfinance_fetcher import GlobalCues, get_yfinance_fetcher
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class GlobalCuesMetrics:
    """Global cues analysis metrics."""
    gift_nifty_change: float | None
    spx_change: float
    nasdaq_change: float
    dxy: float
    dxy_change: float
    crude: float
    crude_change: float
    usdinr: float
    usdinr_change: float
    time_weight: float  # Higher weight early in session


class GlobalCuesAnalyzer:
    """
    Analyzes global market cues.
    
    Signal scoring:
    - GIFT Nifty premium/discount vs prev close
    - SPX/Nasdaq prev close impact
    - DXY rising → FII outflows (bearish)
    - Crude > $90 → Pressure (bearish)
    - USD/INR > 84 → FII selling (bearish)
    
    Time weighting:
    - 09:15-09:45: 2× weight (pre-market cues matter most)
    - 09:45-11:00: 1× weight (normal)
    - After 11:00: 0.5× weight (global cues lose relevance)
    """
    
    # Thresholds
    DXY_THRESHOLD = 105.0  # High DXY bearish for EM
    CRUDE_THRESHOLD = 90.0  # $90+ crude bearish for India
    USDINR_THRESHOLD = 84.0  # High USD/INR bearish
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._fetcher = get_yfinance_fetcher()
    
    def get_time_weight(self) -> float:
        """Get time-based weight for global cues."""
        now = datetime.now(self._ist).time()
        
        early_session_start = time(9, 15)
        early_session_end = time(9, 45)
        mid_session_end = time(11, 0)
        
        if now < early_session_start:
            return 2.0  # Pre-market
        elif now <= early_session_end:
            return 2.0  # Early session
        elif now <= mid_session_end:
            return 1.0  # Mid morning
        else:
            return 0.5  # Afternoon (less relevant)
    
    async def fetch_cues(self) -> GlobalCues:
        """Fetch global market cues."""
        return await self._fetcher.fetch_global_cues()
    
    def analyze(self, cues: GlobalCues) -> GlobalCuesMetrics:
        """
        Analyze global cues.
        
        Args:
            cues: GlobalCues data
            
        Returns:
            GlobalCuesMetrics with analysis
        """
        return GlobalCuesMetrics(
            gift_nifty_change=cues.gift_nifty_change_pct,
            spx_change=cues.spx_change_pct,
            nasdaq_change=cues.nasdaq_change_pct,
            dxy=cues.dxy,
            dxy_change=cues.dxy_change_pct,
            crude=cues.crude_wti,
            crude_change=cues.crude_change_pct,
            usdinr=cues.usdinr,
            usdinr_change=cues.usdinr_change_pct,
            time_weight=self.get_time_weight(),
        )
    
    def compute_signal(
        self,
        cues: GlobalCues | None,
        **kwargs
    ) -> SignalResult:
        """
        Compute global cues signal.
        
        Returns:
            SignalResult with score from -1 to +1
        """
        now = datetime.now(self._ist)
        
        if cues is None:
            return SignalResult(
                name="global_cues",
                score=0.0,
                confidence=0.0,
                reason="No global cues data",
                timestamp=now,
            )
        
        metrics = self.analyze(cues)
        
        score = 0.0
        reasons = []
        confidence = 0.4
        
        # GIFT Nifty (most direct indicator)
        if metrics.gift_nifty_change is not None:
            if metrics.gift_nifty_change > 0.3:
                score += 0.3
                reasons.append(f"GIFT Nifty +{metrics.gift_nifty_change:.1f}%")
            elif metrics.gift_nifty_change < -0.3:
                score -= 0.3
                reasons.append(f"GIFT Nifty {metrics.gift_nifty_change:.1f}%")
            confidence += 0.1
        
        # US markets
        us_avg = (metrics.spx_change + metrics.nasdaq_change) / 2
        if us_avg > 0.5:
            score += 0.2
            reasons.append(f"US markets positive ({us_avg:.1f}%)")
        elif us_avg < -0.5:
            score -= 0.2
            reasons.append(f"US markets negative ({us_avg:.1f}%)")
        
        # DXY (Dollar index)
        if metrics.dxy > self.DXY_THRESHOLD:
            score -= 0.15
            reasons.append(f"High DXY ({metrics.dxy:.1f}) → FII outflow risk")
        if metrics.dxy_change > 0.5:
            score -= 0.1
            reasons.append(f"DXY rising +{metrics.dxy_change:.1f}%")
        elif metrics.dxy_change < -0.5:
            score += 0.1
            reasons.append(f"DXY falling {metrics.dxy_change:.1f}%")
        
        # Crude
        if metrics.crude > self.CRUDE_THRESHOLD:
            score -= 0.15
            reasons.append(f"High crude ${metrics.crude:.0f}")
        if metrics.crude_change > 2:
            score -= 0.1
            reasons.append(f"Crude spiking +{metrics.crude_change:.1f}%")
        
        # USD/INR
        if metrics.usdinr > self.USDINR_THRESHOLD:
            score -= 0.1
            reasons.append(f"Weak INR ({metrics.usdinr:.2f})")
        if metrics.usdinr_change > 0.3:
            score -= 0.1
            reasons.append(f"INR depreciating +{metrics.usdinr_change:.1f}%")
        
        # Apply time weight
        score *= metrics.time_weight
        
        # Normalize
        score = max(-1.0, min(1.0, score))
        
        if metrics.time_weight < 1.0:
            confidence *= metrics.time_weight
            reasons.append(f"(Reduced weight: {metrics.time_weight:.1f}×)")
        
        return SignalResult(
            name="global_cues",
            score=score,
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "Global cues neutral",
            timestamp=now,
        )


# Global instance
_global_cues_analyzer: GlobalCuesAnalyzer | None = None


def get_global_cues_analyzer() -> GlobalCuesAnalyzer:
    """Get or create global cues analyzer."""
    global _global_cues_analyzer
    if _global_cues_analyzer is None:
        _global_cues_analyzer = GlobalCuesAnalyzer()
    return _global_cues_analyzer


async def compute_global_cues_signal(**kwargs) -> SignalResult:
    """Compute global cues signal (async wrapper)."""
    analyzer = get_global_cues_analyzer()
    
    try:
        cues = await analyzer.fetch_cues()
        return analyzer.compute_signal(cues, **kwargs)
    except Exception as e:
        logger.error(f"Failed to compute global cues signal: {e}")
        return SignalResult(
            name="global_cues",
            score=0.0,
            confidence=0.0,
            reason=f"Error: {str(e)[:50]}",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
