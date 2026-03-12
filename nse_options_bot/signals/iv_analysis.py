"""IV Analysis Signal (Signal 2).

IVR = (current_IV - 52wk_low) / (52wk_high - 52wk_low) × 100
IVR>70 → sell premium. IVR<30 → buy premium.
IVP = % of 252 days where IV was below current.
IV skew = 25-delta PE IV minus 25-delta CE IV. Skew>5% → fear premium.
Term structure: near expiry IV vs. next expiry IV.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytz
import structlog

from nse_options_bot.market.option_chain import OptionChainSnapshot
from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class IVMetrics:
    """IV analysis metrics."""

    current_iv: float  # ATM IV
    iv_52wk_high: float
    iv_52wk_low: float
    ivr: float  # IV Rank (0-100)
    ivp: float  # IV Percentile (0-100)
    iv_skew: float  # 25-delta skew
    near_expiry_iv: float
    far_expiry_iv: float
    term_structure: str  # "contango", "backwardation", "flat"


class IVAnalyzer:
    """Implied Volatility analyzer.

    Tracks IV history and calculates IVR, IVP, skew, and term structure.
    """

    # Thresholds
    IVR_SELL_PREMIUM_THRESHOLD = 70  # High IVR = sell premium
    IVR_BUY_PREMIUM_THRESHOLD = 30  # Low IVR = buy premium
    IVP_SELL_THRESHOLD = 70
    IVP_BUY_THRESHOLD = 30

    SKEW_FEAR_THRESHOLD = 5.0  # 5% skew indicates fear
    SKEW_GREED_THRESHOLD = -3.0  # Negative skew indicates greed

    BACKWARDATION_THRESHOLD = -2.0  # Near IV > Far IV by 2%
    CONTANGO_THRESHOLD = 2.0  # Far IV > Near IV by 2%

    # History settings
    HISTORY_DAYS = 252  # Trading days for percentile

    def __init__(self) -> None:
        """Initialize IV analyzer."""
        # IV history per underlying
        self._iv_history: dict[str, deque[float]] = {}
        self._iv_52wk_high: dict[str, float] = {}
        self._iv_52wk_low: dict[str, float] = {}

    def record_iv(self, underlying: str, iv: float) -> None:
        """Record IV for history tracking.

        Args:
            underlying: Underlying symbol
            iv: Current ATM IV
        """
        if underlying not in self._iv_history:
            self._iv_history[underlying] = deque(maxlen=self.HISTORY_DAYS)
            self._iv_52wk_high[underlying] = iv
            self._iv_52wk_low[underlying] = iv

        self._iv_history[underlying].append(iv)

        # Update 52-week high/low
        if iv > self._iv_52wk_high[underlying]:
            self._iv_52wk_high[underlying] = iv
        if iv < self._iv_52wk_low[underlying]:
            self._iv_52wk_low[underlying] = iv

    def calculate_ivr(self, underlying: str, current_iv: float) -> float:
        """Calculate IV Rank.

        IVR = (current_IV - 52wk_low) / (52wk_high - 52wk_low) × 100

        Args:
            underlying: Underlying symbol
            current_iv: Current ATM IV

        Returns:
            IVR (0-100)
        """
        high = self._iv_52wk_high.get(underlying, current_iv)
        low = self._iv_52wk_low.get(underlying, current_iv)

        if high == low:
            return 50.0

        ivr = ((current_iv - low) / (high - low)) * 100
        return max(0.0, min(100.0, ivr))

    def calculate_ivp(self, underlying: str, current_iv: float) -> float:
        """Calculate IV Percentile.

        IVP = % of days where IV was below current.

        Args:
            underlying: Underlying symbol
            current_iv: Current ATM IV

        Returns:
            IVP (0-100)
        """
        history = self._iv_history.get(underlying, [])
        if not history:
            return 50.0

        days_below = sum(1 for iv in history if iv < current_iv)
        return (days_below / len(history)) * 100

    def analyze(
        self,
        near_chain: OptionChainSnapshot,
        far_chain: OptionChainSnapshot | None = None,
        india_vix: float | None = None,
    ) -> Signal:
        """Analyze IV metrics.

        Args:
            near_chain: Near expiry option chain
            far_chain: Far expiry option chain (for term structure)
            india_vix: India VIX value

        Returns:
            IV analysis signal
        """
        underlying = near_chain.underlying

        # Get ATM IV from near chain
        atm_strike = near_chain.get_atm_strike()
        near_strike_data = near_chain._chain.get(atm_strike)

        current_iv = 0.0
        if near_strike_data:
            ce_iv = near_strike_data.ce.iv if near_strike_data.ce else 0
            pe_iv = near_strike_data.pe.iv if near_strike_data.pe else 0
            current_iv = (ce_iv + pe_iv) / 2 if (ce_iv and pe_iv) else max(ce_iv, pe_iv)

        # Use India VIX if available and no chain IV
        if current_iv == 0 and india_vix:
            current_iv = india_vix / 100  # VIX is in percentage

        # Record IV for history
        if current_iv > 0:
            self.record_iv(underlying, current_iv)

        # Calculate metrics
        ivr = self.calculate_ivr(underlying, current_iv)
        ivp = self.calculate_ivp(underlying, current_iv)
        iv_skew = near_chain.get_iv_skew()

        # Term structure
        near_expiry_iv = current_iv
        far_expiry_iv = 0.0
        term_structure = "flat"

        if far_chain:
            far_atm = far_chain.get_atm_strike()
            far_strike_data = far_chain._chain.get(far_atm)
            if far_strike_data:
                far_ce_iv = far_strike_data.ce.iv if far_strike_data.ce else 0
                far_pe_iv = far_strike_data.pe.iv if far_strike_data.pe else 0
                far_expiry_iv = (far_ce_iv + far_pe_iv) / 2

                if near_expiry_iv > 0 and far_expiry_iv > 0:
                    diff_pct = ((far_expiry_iv - near_expiry_iv) / near_expiry_iv) * 100
                    if diff_pct >= self.CONTANGO_THRESHOLD:
                        term_structure = "contango"
                    elif diff_pct <= self.BACKWARDATION_THRESHOLD:
                        term_structure = "backwardation"

        metrics = IVMetrics(
            current_iv=current_iv,
            iv_52wk_high=self._iv_52wk_high.get(underlying, current_iv),
            iv_52wk_low=self._iv_52wk_low.get(underlying, current_iv),
            ivr=ivr,
            ivp=ivp,
            iv_skew=iv_skew,
            near_expiry_iv=near_expiry_iv,
            far_expiry_iv=far_expiry_iv,
            term_structure=term_structure,
        )

        # Calculate score and confidence
        score, confidence, reason = self._calculate_score(metrics)

        return create_signal(
            signal_type=SignalType.IV_ANALYSIS,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "current_iv": current_iv * 100,  # Convert to %
                "ivr": ivr,
                "ivp": ivp,
                "iv_skew": iv_skew,
                "term_structure_diff": (far_expiry_iv - near_expiry_iv) * 100 if far_expiry_iv else 0,
            },
        )

    def _calculate_score(
        self, metrics: IVMetrics
    ) -> tuple[float, float, str]:
        """Calculate IV signal score.

        Args:
            metrics: IV metrics

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # IVR analysis
        if metrics.ivr >= self.IVR_SELL_PREMIUM_THRESHOLD:
            # High IVR - sell premium (this is a vol signal, not direction)
            # We use negative score to indicate sell premium opportunity
            score -= 0.3
            reasons.append(f"IVR {metrics.ivr:.0f}% → Sell premium")
            confidence += 0.1

        elif metrics.ivr <= self.IVR_BUY_PREMIUM_THRESHOLD:
            # Low IVR - buy premium
            score += 0.3
            reasons.append(f"IVR {metrics.ivr:.0f}% → Buy premium opportunity")
            confidence += 0.1

        # IVP confirmation
        if metrics.ivp >= self.IVP_SELL_THRESHOLD and metrics.ivr >= 50:
            score -= 0.1
            confidence += 0.1
            reasons.append(f"IVP {metrics.ivp:.0f}% confirms high IV")

        elif metrics.ivp <= self.IVP_BUY_THRESHOLD and metrics.ivr <= 50:
            score += 0.1
            confidence += 0.1
            reasons.append(f"IVP {metrics.ivp:.0f}% confirms low IV")

        # IV Skew analysis (directional signal)
        if metrics.iv_skew >= self.SKEW_FEAR_THRESHOLD:
            # Fear premium in puts - contrarian bullish
            score += 0.2
            reasons.append(f"Skew {metrics.iv_skew:.1f}% → Fear premium (contrarian bullish)")

        elif metrics.iv_skew <= self.SKEW_GREED_THRESHOLD:
            # Unusual call demand - potential reversal
            score -= 0.1
            reasons.append(f"Skew {metrics.iv_skew:.1f}% → Call demand elevated")

        # Term structure
        if metrics.term_structure == "backwardation":
            # Near IV > Far IV - mean reversion expected
            score -= 0.15
            reasons.append("IV backwardation → Mean reversion expected")
            confidence += 0.05

        elif metrics.term_structure == "contango":
            reasons.append("IV contango → Normal term structure")

        # Combine reasons
        reason = " | ".join(reasons) if reasons else f"IVR: {metrics.ivr:.0f}%, IVP: {metrics.ivp:.0f}%"

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def get_iv_summary(
        self, chain: OptionChainSnapshot, india_vix: float | None = None
    ) -> dict[str, Any]:
        """Get IV summary.

        Args:
            chain: Option chain snapshot
            india_vix: India VIX value

        Returns:
            Summary dict
        """
        underlying = chain.underlying
        atm_strike = chain.get_atm_strike()
        strike_data = chain._chain.get(atm_strike)

        current_iv = 0.0
        if strike_data:
            ce_iv = strike_data.ce.iv if strike_data.ce else 0
            pe_iv = strike_data.pe.iv if strike_data.pe else 0
            current_iv = (ce_iv + pe_iv) / 2 if (ce_iv and pe_iv) else max(ce_iv, pe_iv)

        return {
            "underlying": underlying,
            "current_iv_pct": current_iv * 100,
            "ivr": self.calculate_ivr(underlying, current_iv),
            "ivp": self.calculate_ivp(underlying, current_iv),
            "iv_skew": chain.get_iv_skew(),
            "iv_52wk_high": self._iv_52wk_high.get(underlying, 0) * 100,
            "iv_52wk_low": self._iv_52wk_low.get(underlying, 0) * 100,
            "india_vix": india_vix,
            "history_days": len(self._iv_history.get(underlying, [])),
        }
