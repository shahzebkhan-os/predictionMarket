"""
FII/DII Flow Signal.

Signal 8: FII/DII futures and options flow analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from zoneinfo import ZoneInfo

from nse_advisor.data.nse_fetcher import FiiDiiData, get_nse_fetcher
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class FiiDiiMetrics:
    """FII/DII flow metrics."""
    fii_net: float  # FII net value (positive = buy)
    dii_net: float  # DII net value
    fii_3day_net: float  # 3-day rolling FII net
    fii_trend: str  # "BUYING", "SELLING", "NEUTRAL"
    dii_trend: str  # "BUYING", "SELLING", "NEUTRAL"


class FiiDiiAnalyzer:
    """
    Analyzes FII/DII flow data.
    
    Signal scoring:
    - FII net futures long > +₹5000cr → Bullish
    - FII 3-day consecutive sell > -₹15000cr → Bearish regime
    - DII buying during FII selling → Support for market
    
    Note: FII/DII data has 1-day lag (available at 18:30 IST).
    This is for medium-term bias, not intraday triggers.
    """
    
    # Thresholds (in crores)
    FII_BULLISH_THRESHOLD = 5000.0
    FII_BEARISH_THRESHOLD = -5000.0
    FII_3DAY_BEARISH_THRESHOLD = -15000.0
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._fii_history: list[float] = []  # Last 5 days
        self._fetcher = get_nse_fetcher()
    
    def update_history(self, fii_net: float) -> None:
        """Update FII history for rolling calculations."""
        self._fii_history.append(fii_net)
        if len(self._fii_history) > 5:
            self._fii_history = self._fii_history[-5:]
    
    def analyze(self, data: FiiDiiData | None) -> FiiDiiMetrics:
        """
        Analyze FII/DII flow data.
        
        Args:
            data: FII/DII data from NSE
            
        Returns:
            FiiDiiMetrics with analysis
        """
        if data is None:
            return FiiDiiMetrics(
                fii_net=0.0,
                dii_net=0.0,
                fii_3day_net=0.0,
                fii_trend="NEUTRAL",
                dii_trend="NEUTRAL",
            )
        
        fii_net = data.fii_net_value
        dii_net = data.dii_net_value
        
        # Update history
        self.update_history(fii_net)
        
        # 3-day rolling net
        if len(self._fii_history) >= 3:
            fii_3day_net = sum(self._fii_history[-3:])
        else:
            fii_3day_net = fii_net * len(self._fii_history)
        
        # Determine trends
        if fii_net >= self.FII_BULLISH_THRESHOLD:
            fii_trend = "BUYING"
        elif fii_net <= self.FII_BEARISH_THRESHOLD:
            fii_trend = "SELLING"
        else:
            fii_trend = "NEUTRAL"
        
        if dii_net >= self.FII_BULLISH_THRESHOLD:
            dii_trend = "BUYING"
        elif dii_net <= self.FII_BEARISH_THRESHOLD:
            dii_trend = "SELLING"
        else:
            dii_trend = "NEUTRAL"
        
        return FiiDiiMetrics(
            fii_net=fii_net,
            dii_net=dii_net,
            fii_3day_net=fii_3day_net,
            fii_trend=fii_trend,
            dii_trend=dii_trend,
        )
    
    def compute_signal(
        self,
        data: FiiDiiData | None,
        **kwargs
    ) -> SignalResult:
        """
        Compute FII/DII signal.
        
        Returns:
            SignalResult with score from -1 to +1
        """
        now = datetime.now(self._ist)
        
        metrics = self.analyze(data)
        
        score = 0.0
        reasons = []
        confidence = 0.4  # Lower confidence due to lag
        
        # FII single-day flow
        if metrics.fii_trend == "BUYING":
            score += 0.3
            reasons.append(f"FII buying ₹{metrics.fii_net/100:.0f}00cr")
        elif metrics.fii_trend == "SELLING":
            score -= 0.3
            reasons.append(f"FII selling ₹{abs(metrics.fii_net)/100:.0f}00cr")
        
        # FII 3-day trend (stronger signal)
        if metrics.fii_3day_net <= self.FII_3DAY_BEARISH_THRESHOLD:
            score -= 0.4
            confidence += 0.2
            reasons.append(f"FII 3-day sell: ₹{abs(metrics.fii_3day_net)/100:.0f}00cr (bearish regime)")
        elif metrics.fii_3day_net >= 10000:  # Strong buying
            score += 0.3
            confidence += 0.1
            reasons.append(f"FII 3-day buy: ₹{metrics.fii_3day_net/100:.0f}00cr")
        
        # DII offsetting FII
        if metrics.fii_trend == "SELLING" and metrics.dii_trend == "BUYING":
            score += 0.15  # DII provides support
            reasons.append("DII buying provides support")
        elif metrics.fii_trend == "BUYING" and metrics.dii_trend == "SELLING":
            score -= 0.1  # Some concern
            reasons.append("DII selling during FII buying")
        
        # Add note about lag
        reasons.append("(1-day lag data)")
        
        return SignalResult(
            name="fii_dii",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "FII/DII neutral",
            timestamp=now,
        )


# Global instance
_fii_dii_analyzer: FiiDiiAnalyzer | None = None


def get_fii_dii_analyzer() -> FiiDiiAnalyzer:
    """Get or create global FII/DII analyzer."""
    global _fii_dii_analyzer
    if _fii_dii_analyzer is None:
        _fii_dii_analyzer = FiiDiiAnalyzer()
    return _fii_dii_analyzer


async def compute_fii_dii_signal(**kwargs) -> SignalResult:
    """Compute FII/DII signal (async wrapper)."""
    analyzer = get_fii_dii_analyzer()
    
    try:
        fetcher = get_nse_fetcher()
        data = await fetcher.fetch_fii_dii_data()
        return analyzer.compute_signal(data, **kwargs)
    except Exception as e:
        logger.error(f"Failed to compute FII/DII signal: {e}")
        return SignalResult(
            name="fii_dii",
            score=0.0,
            confidence=0.0,
            reason=f"Error: {str(e)[:50]}",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
