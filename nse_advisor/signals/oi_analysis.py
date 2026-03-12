"""
OI Analysis Signal.

Signal 1: Open Interest analysis for PCR, OI buildup, and OI walls.
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
class OIMetrics:
    """OI analysis metrics."""
    pcr: float
    pcr_change: float
    ce_oi_total: int
    pe_oi_total: int
    ce_oi_change: int
    pe_oi_change: int
    max_ce_oi_strike: float
    max_pe_oi_strike: float
    ce_wall_distance: float
    pe_wall_distance: float


class OIAnalyzer:
    """
    Analyzes Open Interest patterns.
    
    Signal scoring:
    - PCR > 1.5 rising → Contrarian bullish (+0.5 to +1.0)
    - PCR < 0.7 falling → Contrarian bearish (-0.5 to -1.0)
    - CE OI buildup at resistance + price falling → Strong wall (bearish)
    - PE OI buildup at support + price rising → Strong floor (bullish)
    
    Score = pcr_score × 0.35 + oi_buildup × 0.40 + wall_distance × 0.25
    """
    
    # PCR thresholds
    PCR_BULLISH_THRESHOLD = 1.5  # PCR > 1.5 → contrarian bullish
    PCR_BEARISH_THRESHOLD = 0.7  # PCR < 0.7 → contrarian bearish
    PCR_NEUTRAL_LOW = 0.9
    PCR_NEUTRAL_HIGH = 1.2
    
    def __init__(self) -> None:
        """Initialize OI analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._prev_pcr: float | None = None
    
    def analyze(
        self,
        chain: OptionChainSnapshot,
        spot_price: float | None = None
    ) -> OIMetrics:
        """
        Analyze OI patterns from option chain.
        
        Args:
            chain: Option chain snapshot
            spot_price: Current spot price (uses chain.spot_price if None)
            
        Returns:
            OIMetrics with analysis results
        """
        spot = spot_price or chain.spot_price
        
        # Calculate PCR
        ce_oi, pe_oi = chain.get_total_oi()
        pcr = pe_oi / ce_oi if ce_oi > 0 else 0.0
        
        # PCR change
        pcr_change = 0.0
        if self._prev_pcr is not None:
            pcr_change = pcr - self._prev_pcr
        self._prev_pcr = pcr
        
        # OI changes
        ce_change, pe_change = chain.get_oi_change()
        
        # Find max OI strikes (walls)
        max_ce_strike = 0.0
        max_pe_strike = 0.0
        max_ce_oi = 0
        max_pe_oi = 0
        
        for strike in chain.strikes:
            if strike.ce_oi > max_ce_oi:
                max_ce_oi = strike.ce_oi
                max_ce_strike = strike.strike_price
            if strike.pe_oi > max_pe_oi:
                max_pe_oi = strike.pe_oi
                max_pe_strike = strike.strike_price
        
        # Wall distances (as % of spot)
        ce_wall_dist = ((max_ce_strike - spot) / spot) * 100 if spot > 0 else 0
        pe_wall_dist = ((spot - max_pe_strike) / spot) * 100 if spot > 0 else 0
        
        return OIMetrics(
            pcr=pcr,
            pcr_change=pcr_change,
            ce_oi_total=ce_oi,
            pe_oi_total=pe_oi,
            ce_oi_change=ce_change,
            pe_oi_change=pe_change,
            max_ce_oi_strike=max_ce_strike,
            max_pe_oi_strike=max_pe_strike,
            ce_wall_distance=ce_wall_dist,
            pe_wall_distance=pe_wall_dist,
        )
    
    def compute_signal(
        self,
        chain: OptionChainSnapshot,
        **kwargs
    ) -> SignalResult:
        """
        Compute OI analysis signal.
        
        Returns:
            SignalResult with score from -1 to +1
        """
        now = datetime.now(self._ist)
        
        if not chain.is_valid:
            return SignalResult(
                name="oi_analysis",
                score=0.0,
                confidence=0.0,
                reason="Invalid/stale option chain",
                timestamp=now,
            )
        
        metrics = self.analyze(chain)
        
        # Calculate component scores
        pcr_score = self._calculate_pcr_score(metrics.pcr, metrics.pcr_change)
        buildup_score = self._calculate_buildup_score(
            metrics.ce_oi_change, metrics.pe_oi_change
        )
        wall_score = self._calculate_wall_score(
            metrics.ce_wall_distance, metrics.pe_wall_distance
        )
        
        # Weighted combination
        score = (
            pcr_score * 0.35 +
            buildup_score * 0.40 +
            wall_score * 0.25
        )
        
        # Confidence based on OI magnitude
        min_oi = 100000  # Minimum OI for high confidence
        confidence = min(1.0, (metrics.ce_oi_total + metrics.pe_oi_total) / min_oi)
        
        # Build reason
        reasons = []
        if metrics.pcr > self.PCR_BULLISH_THRESHOLD:
            reasons.append(f"High PCR {metrics.pcr:.2f} (bullish)")
        elif metrics.pcr < self.PCR_BEARISH_THRESHOLD:
            reasons.append(f"Low PCR {metrics.pcr:.2f} (bearish)")
        
        if metrics.ce_oi_change > 0 and metrics.pe_oi_change < 0:
            reasons.append("CE buildup + PE unwinding (bearish)")
        elif metrics.pe_oi_change > 0 and metrics.ce_oi_change < 0:
            reasons.append("PE buildup + CE unwinding (bullish)")
        
        reason = "; ".join(reasons) if reasons else "OI neutral"
        
        return SignalResult(
            name="oi_analysis",
            score=max(-1.0, min(1.0, score)),
            confidence=confidence,
            reason=reason,
            timestamp=now,
        )
    
    def _calculate_pcr_score(self, pcr: float, pcr_change: float) -> float:
        """Calculate PCR component score."""
        score = 0.0
        
        # Contrarian: High PCR = bullish (puts being bought = fear = bottom)
        if pcr >= self.PCR_BULLISH_THRESHOLD:
            score = 0.5 + min(0.5, (pcr - self.PCR_BULLISH_THRESHOLD) * 0.5)
            if pcr_change > 0:  # Rising PCR
                score += 0.2
        
        # Contrarian: Low PCR = bearish (calls being bought = greed = top)
        elif pcr <= self.PCR_BEARISH_THRESHOLD:
            score = -0.5 - min(0.5, (self.PCR_BEARISH_THRESHOLD - pcr) * 0.5)
            if pcr_change < 0:  # Falling PCR
                score -= 0.2
        
        # Neutral zone
        else:
            score = (pcr - 1.0) * 0.5  # Linear scale around 1.0
        
        return max(-1.0, min(1.0, score))
    
    def _calculate_buildup_score(
        self,
        ce_change: int,
        pe_change: int
    ) -> float:
        """Calculate OI buildup score."""
        # CE buildup = bearish (sellers expect resistance)
        # PE buildup = bullish (sellers expect support)
        
        total_change = abs(ce_change) + abs(pe_change)
        if total_change == 0:
            return 0.0
        
        # Net buildup ratio
        net_ratio = (pe_change - ce_change) / total_change
        
        return net_ratio  # -1 to +1
    
    def _calculate_wall_score(
        self,
        ce_wall_dist: float,
        pe_wall_dist: float
    ) -> float:
        """Calculate wall distance score."""
        # Closer CE wall (resistance) = more bearish
        # Closer PE wall (support) = more bullish
        
        # Score based on relative distances
        if ce_wall_dist + pe_wall_dist == 0:
            return 0.0
        
        # Positive if PE wall is closer (bullish support nearby)
        # Negative if CE wall is closer (bearish resistance nearby)
        if pe_wall_dist < ce_wall_dist:
            return 0.5 * (1 - pe_wall_dist / 5.0)  # Closer = stronger
        elif ce_wall_dist < pe_wall_dist:
            return -0.5 * (1 - ce_wall_dist / 5.0)
        
        return 0.0


# Global instance
_oi_analyzer: OIAnalyzer | None = None


def get_oi_analyzer() -> OIAnalyzer:
    """Get or create global OI analyzer."""
    global _oi_analyzer
    if _oi_analyzer is None:
        _oi_analyzer = OIAnalyzer()
    return _oi_analyzer


async def compute_oi_signal(
    chain: OptionChainSnapshot | None,
    **kwargs
) -> SignalResult:
    """Compute OI analysis signal (async wrapper)."""
    analyzer = get_oi_analyzer()
    
    if chain is None:
        return SignalResult(
            name="oi_analysis",
            score=0.0,
            confidence=0.0,
            reason="No option chain data",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
    
    return analyzer.compute_signal(chain, **kwargs)
