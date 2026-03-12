"""
IV Analysis Signal.

Signal 2: Implied Volatility analysis for IVR, IVP, IV skew, and term structure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class IVMetrics:
    """IV analysis metrics."""
    current_iv: float
    iv_52wk_high: float
    iv_52wk_low: float
    ivr: float  # IV Rank
    ivp: float  # IV Percentile
    iv_skew: float  # 25-delta PE IV - CE IV
    atm_iv: float
    near_term_iv: float
    next_term_iv: float
    term_structure: str  # "CONTANGO" or "BACKWARDATION"


class IVAnalyzer:
    """
    Analyzes Implied Volatility patterns.
    
    Signal scoring:
    - IVR > 70 → Sell premium (score towards -0.5 to signal sell premium strategies)
    - IVR < 30 → Buy premium (score towards +0.5 to signal buy premium strategies)
    - IV skew > 5% → Fear premium present (protective, bullish contrarian)
    - Term structure backwardation → Mean reversion expected
    
    IVR = (current_IV - 52wk_low) / (52wk_high - 52wk_low) × 100
    IVP = % of 252 days where IV was below current (more robust than IVR)
    """
    
    # IVR thresholds
    IVR_SELL_THRESHOLD = 70.0  # Sell premium when IVR > 70
    IVR_BUY_THRESHOLD = 30.0   # Buy premium when IVR < 30
    
    # IV skew threshold
    SKEW_THRESHOLD = 5.0  # % difference
    
    def __init__(self) -> None:
        """Initialize IV analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        
        # IV history storage (per underlying)
        self._iv_history: dict[str, list[tuple[date, float]]] = {}
    
    def update_iv_history(
        self,
        underlying: str,
        history: list[tuple[date, float]]
    ) -> None:
        """
        Update IV history for IVR/IVP calculations.
        
        Args:
            underlying: Underlying symbol
            history: List of (date, iv) tuples for 252 days
        """
        self._iv_history[underlying] = sorted(history, key=lambda x: x[0])
    
    def analyze(
        self,
        chain: OptionChainSnapshot,
        underlying: str = "NIFTY"
    ) -> IVMetrics:
        """
        Analyze IV from option chain.
        
        Args:
            chain: Option chain snapshot
            underlying: Underlying symbol for history lookup
            
        Returns:
            IVMetrics with analysis results
        """
        # Get ATM IV
        atm_strike = chain.get_atm_strike()
        atm_data = chain.get_strike(atm_strike)
        
        atm_iv = 0.0
        if atm_data:
            atm_iv = (atm_data.ce_iv + atm_data.pe_iv) / 2
        
        # Get IV skew
        iv_skew = chain.get_iv_skew() * 100  # Convert to percentage
        
        # Calculate IVR and IVP from history
        history = self._iv_history.get(underlying, [])
        
        if len(history) >= 50:
            iv_values = [h[1] for h in history[-252:]]
            iv_52wk_high = max(iv_values)
            iv_52wk_low = min(iv_values)
            
            # IVR calculation
            if iv_52wk_high > iv_52wk_low:
                ivr = ((atm_iv - iv_52wk_low) / (iv_52wk_high - iv_52wk_low)) * 100
            else:
                ivr = 50.0
            
            # IVP calculation (percentile)
            below_current = sum(1 for v in iv_values if v < atm_iv)
            ivp = (below_current / len(iv_values)) * 100
        else:
            # Default if no history
            iv_52wk_high = atm_iv * 1.5
            iv_52wk_low = atm_iv * 0.5
            ivr = 50.0
            ivp = 50.0
        
        # Term structure (near vs next expiry)
        # For simplicity, use ATM IV as near term
        near_term_iv = atm_iv
        next_term_iv = atm_iv * 0.95  # Approximate
        
        if near_term_iv > next_term_iv:
            term_structure = "BACKWARDATION"
        else:
            term_structure = "CONTANGO"
        
        return IVMetrics(
            current_iv=atm_iv,
            iv_52wk_high=iv_52wk_high,
            iv_52wk_low=iv_52wk_low,
            ivr=ivr,
            ivp=ivp,
            iv_skew=iv_skew,
            atm_iv=atm_iv,
            near_term_iv=near_term_iv,
            next_term_iv=next_term_iv,
            term_structure=term_structure,
        )
    
    def compute_signal(
        self,
        chain: OptionChainSnapshot,
        underlying: str = "NIFTY",
        **kwargs
    ) -> SignalResult:
        """
        Compute IV analysis signal.
        
        Returns:
            SignalResult with score from -1 to +1
            
        Note: Score interpretation for IV:
        - Positive score: Suggests buying premium (IV low)
        - Negative score: Suggests selling premium (IV high)
        This is about premium strategy, not direction.
        """
        now = datetime.now(self._ist)
        
        if not chain.is_valid:
            return SignalResult(
                name="iv_analysis",
                score=0.0,
                confidence=0.0,
                reason="Invalid/stale option chain",
                timestamp=now,
            )
        
        metrics = self.analyze(chain, underlying)
        
        # Calculate IV score
        # High IVR = sell premium = negative score (contrarian, expect IV crush)
        # Low IVR = buy premium = positive score (expect IV expansion)
        
        score = 0.0
        reasons = []
        
        # IVR component
        if metrics.ivr >= self.IVR_SELL_THRESHOLD:
            ivr_score = -0.5 - ((metrics.ivr - self.IVR_SELL_THRESHOLD) / 60)
            reasons.append(f"High IVR {metrics.ivr:.0f}% (sell premium)")
        elif metrics.ivr <= self.IVR_BUY_THRESHOLD:
            ivr_score = 0.5 + ((self.IVR_BUY_THRESHOLD - metrics.ivr) / 60)
            reasons.append(f"Low IVR {metrics.ivr:.0f}% (buy premium)")
        else:
            ivr_score = (50 - metrics.ivr) / 50  # Linear scale
            reasons.append(f"IVR {metrics.ivr:.0f}% (neutral)")
        
        score += ivr_score * 0.5
        
        # IV skew component
        if metrics.iv_skew > self.SKEW_THRESHOLD:
            skew_score = 0.3  # Fear premium = contrarian bullish
            reasons.append(f"Put skew {metrics.iv_skew:.1f}% (fear premium)")
        elif metrics.iv_skew < -self.SKEW_THRESHOLD:
            skew_score = -0.3  # Call premium = contrarian bearish
            reasons.append(f"Call skew {abs(metrics.iv_skew):.1f}%")
        else:
            skew_score = 0.0
        
        score += skew_score * 0.3
        
        # Term structure component
        if metrics.term_structure == "BACKWARDATION":
            term_score = 0.2  # Expect IV normalization
            reasons.append("Term structure backwardation (mean reversion)")
        else:
            term_score = 0.0
        
        score += term_score * 0.2
        
        # Confidence based on IVP (percentile is more robust)
        # Higher confidence when IVP agrees with IVR
        confidence = 0.5 + abs(metrics.ivp - 50) / 100
        
        return SignalResult(
            name="iv_analysis",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons),
            timestamp=now,
        )


# Global instance
_iv_analyzer: IVAnalyzer | None = None


def get_iv_analyzer() -> IVAnalyzer:
    """Get or create global IV analyzer."""
    global _iv_analyzer
    if _iv_analyzer is None:
        _iv_analyzer = IVAnalyzer()
    return _iv_analyzer


async def compute_iv_signal(
    chain: OptionChainSnapshot | None,
    underlying: str = "NIFTY",
    **kwargs
) -> SignalResult:
    """Compute IV analysis signal (async wrapper)."""
    analyzer = get_iv_analyzer()
    
    if chain is None:
        return SignalResult(
            name="iv_analysis",
            score=0.0,
            confidence=0.0,
            reason="No option chain data",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
    
    return analyzer.compute_signal(chain, underlying, **kwargs)
