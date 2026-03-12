"""
Straddle Pricing Signal.

Signal 9: ATM straddle pricing analysis for expected move.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from zoneinfo import ZoneInfo

from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class StraddleMetrics:
    """Straddle pricing metrics."""
    atm_straddle_price: float
    expected_move_pct: float
    expected_move_upper: float
    expected_move_lower: float
    implied_hv_ratio: float  # IV / HV
    straddle_overpriced: bool
    straddle_underpriced: bool
    spot_price: float
    dte: int


class StraddlePricingAnalyzer:
    """
    Analyzes ATM straddle pricing.
    
    Signal scoring:
    - ATM straddle price = CE + PE LTP
    - Expected move % = straddle / spot × 100
    - Compare to HV20 × sqrt(DTE/252)
    - Overpriced (IV > HV) → Sell straddle (score towards sell premium)
    - Underpriced (IV < HV) → Buy straddle (score towards buy premium)
    
    Breakeven levels = ATM ± straddle price
    Near expiry, index tends to stay within breakeven (magnet effect)
    """
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._straddle_history: list[tuple[datetime, float]] = []
    
    def analyze(
        self,
        chain: OptionChainSnapshot,
        hv20: float = 0.0,
        dte: int = 5
    ) -> StraddleMetrics:
        """
        Analyze straddle pricing.
        
        Args:
            chain: Option chain snapshot
            hv20: 20-day historical volatility (annualized)
            dte: Days to expiry
            
        Returns:
            StraddleMetrics with analysis
        """
        spot = chain.spot_price
        straddle_price = chain.get_straddle_price()
        expected_move_pct = chain.get_expected_move_pct()
        
        # Breakeven levels
        expected_upper = spot + straddle_price
        expected_lower = spot - straddle_price
        
        # Compare IV to HV
        # Expected move from HV: HV × sqrt(DTE/252)
        if hv20 > 0:
            hv_expected_move = hv20 * math.sqrt(dte / 252) * spot / 100
            implied_hv_ratio = straddle_price / hv_expected_move if hv_expected_move > 0 else 1.0
        else:
            implied_hv_ratio = 1.0
        
        # Determine if overpriced/underpriced
        overpriced = implied_hv_ratio > 1.15  # IV > HV by 15%
        underpriced = implied_hv_ratio < 0.85  # IV < HV by 15%
        
        # Track straddle decay
        self._straddle_history.append((datetime.now(self._ist), straddle_price))
        if len(self._straddle_history) > 100:
            self._straddle_history = self._straddle_history[-100:]
        
        return StraddleMetrics(
            atm_straddle_price=straddle_price,
            expected_move_pct=expected_move_pct,
            expected_move_upper=expected_upper,
            expected_move_lower=expected_lower,
            implied_hv_ratio=implied_hv_ratio,
            straddle_overpriced=overpriced,
            straddle_underpriced=underpriced,
            spot_price=spot,
            dte=dte,
        )
    
    def compute_signal(
        self,
        chain: OptionChainSnapshot,
        hv20: float = 0.0,
        dte: int = 5,
        **kwargs
    ) -> SignalResult:
        """
        Compute straddle pricing signal.
        
        Returns:
            SignalResult with score from -1 to +1
            
        Note: Score interpretation:
        - Positive: IV underpriced, buy premium strategies
        - Negative: IV overpriced, sell premium strategies
        """
        now = datetime.now(self._ist)
        
        if not chain.is_valid:
            return SignalResult(
                name="straddle_pricing",
                score=0.0,
                confidence=0.0,
                reason="Invalid/stale option chain",
                timestamp=now,
            )
        
        metrics = self.analyze(chain, hv20, dte)
        
        score = 0.0
        reasons = []
        confidence = 0.5
        
        # IV vs HV assessment
        if metrics.straddle_overpriced:
            score -= 0.4  # Sell premium
            reasons.append(f"Straddle overpriced (IV/HV: {metrics.implied_hv_ratio:.2f})")
            confidence += 0.15
        elif metrics.straddle_underpriced:
            score += 0.4  # Buy premium
            reasons.append(f"Straddle underpriced (IV/HV: {metrics.implied_hv_ratio:.2f})")
            confidence += 0.15
        else:
            reasons.append(f"Straddle fairly priced (IV/HV: {metrics.implied_hv_ratio:.2f})")
        
        # Expected move context
        reasons.append(
            f"Expected move: ±{metrics.expected_move_pct:.1f}% "
            f"({metrics.expected_move_lower:.0f}-{metrics.expected_move_upper:.0f})"
        )
        
        # DTE impact
        if dte <= 2:
            # Near expiry, straddle decay accelerates
            reasons.append(f"DTE={dte}: Rapid theta decay")
            if metrics.straddle_overpriced:
                score -= 0.1  # Extra confidence in selling
                confidence += 0.1
        
        # Straddle decay tracking
        if len(self._straddle_history) >= 2:
            prev_straddle = self._straddle_history[-2][1]
            decay = prev_straddle - metrics.atm_straddle_price
            if decay > 0:
                decay_pct = (decay / prev_straddle) * 100
                if decay_pct > 2:  # Significant decay
                    reasons.append(f"Straddle decayed {decay_pct:.1f}% since last check")
        
        return SignalResult(
            name="straddle_pricing",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons),
            timestamp=now,
        )


# Global instance
_straddle_analyzer: StraddlePricingAnalyzer | None = None


def get_straddle_analyzer() -> StraddlePricingAnalyzer:
    """Get or create global straddle analyzer."""
    global _straddle_analyzer
    if _straddle_analyzer is None:
        _straddle_analyzer = StraddlePricingAnalyzer()
    return _straddle_analyzer


async def compute_straddle_signal(
    chain: OptionChainSnapshot | None,
    hv20: float = 0.0,
    dte: int = 5,
    **kwargs
) -> SignalResult:
    """Compute straddle pricing signal (async wrapper)."""
    analyzer = get_straddle_analyzer()
    
    if chain is None:
        return SignalResult(
            name="straddle_pricing",
            score=0.0,
            confidence=0.0,
            reason="No option chain data",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
    
    return analyzer.compute_signal(chain, hv20, dte, **kwargs)
