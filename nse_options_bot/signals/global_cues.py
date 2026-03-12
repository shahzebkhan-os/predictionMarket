"""Global Cues Signal (Signal 7).

GIFT Nifty premium/discount vs. prev NIFTY close → gap estimate.
SPX/Nasdaq prev close (via yfinance).
DXY rising → FII outflows → bearish.
Crude WTI >$90 → inflationary pressure.
USD/INR > 84 → FII selling pressure.
Weight cues more heavily at 09:15–09:45, reduce after 11:00.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Any

import pytz
import structlog

from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class GlobalCuesData:
    """Global market cues data."""

    gift_nifty: float | None = None
    gift_nifty_change_pct: float | None = None
    nifty_prev_close: float | None = None

    spx_prev_close: float | None = None
    spx_change_pct: float | None = None

    nasdaq_prev_close: float | None = None
    nasdaq_change_pct: float | None = None

    dxy_value: float | None = None
    dxy_change_pct: float | None = None

    crude_wti: float | None = None
    crude_change_pct: float | None = None

    usdinr: float | None = None
    usdinr_change_pct: float | None = None

    vix_us: float | None = None


class GlobalCuesAnalyzer:
    """Global market cues analyzer.

    Fetches and analyzes global market data for Indian market direction.
    """

    # Thresholds
    GIFT_NIFTY_BULLISH_THRESHOLD = 1.0  # >+1% = bullish
    GIFT_NIFTY_BEARISH_THRESHOLD = -1.0  # <-1% = bearish

    SPX_BULLISH_THRESHOLD = 0.5
    SPX_BEARISH_THRESHOLD = -0.5

    DXY_BEARISH_THRESHOLD = 0.5  # DXY rise = bearish for EM
    DXY_BULLISH_THRESHOLD = -0.3  # DXY fall = bullish for EM

    CRUDE_HIGH_THRESHOLD = 90.0  # >$90 = inflationary pressure
    CRUDE_VERY_HIGH = 100.0

    USDINR_HIGH_THRESHOLD = 84.0  # INR weakness = FII selling pressure
    USDINR_VERY_HIGH = 85.0

    # Time-based weighting
    HIGH_WEIGHT_START = time(9, 15)
    HIGH_WEIGHT_END = time(9, 45)
    LOW_WEIGHT_START = time(11, 0)

    def __init__(self) -> None:
        """Initialize global cues analyzer."""
        self._last_update: datetime | None = None
        self._cached_cues: GlobalCuesData | None = None

    async def fetch_cues(self) -> GlobalCuesData:
        """Fetch global market cues.

        In production, this would fetch from various APIs:
        - GIFT Nifty: NSE India international website
        - US Markets: yfinance
        - DXY, Crude: investing.com or similar

        Returns:
            GlobalCuesData
        """
        # This is a placeholder - actual implementation would fetch real data
        # For now, return empty cues that can be set manually
        cues = GlobalCuesData()
        self._last_update = datetime.now(IST)
        self._cached_cues = cues
        return cues

    def set_cues(self, cues: GlobalCuesData) -> None:
        """Manually set cues data.

        Args:
            cues: Global cues data
        """
        self._cached_cues = cues
        self._last_update = datetime.now(IST)

    def analyze(
        self,
        cues: GlobalCuesData | None = None,
        nifty_prev_close: float | None = None,
    ) -> Signal:
        """Analyze global cues.

        Args:
            cues: Global cues data (uses cached if not provided)
            nifty_prev_close: Previous NIFTY close

        Returns:
            Global cues signal
        """
        cues = cues or self._cached_cues or GlobalCuesData()

        if nifty_prev_close:
            cues.nifty_prev_close = nifty_prev_close

        # Get time-based weight multiplier
        weight_mult = self._get_time_weight()

        # Analyze each component
        gift_score = self._analyze_gift_nifty(cues)
        us_score = self._analyze_us_markets(cues)
        dxy_score = self._analyze_dxy(cues)
        crude_score = self._analyze_crude(cues)
        usdinr_score = self._analyze_usdinr(cues)

        # Combine scores with weights
        weights = {
            "gift_nifty": 0.35,
            "us_markets": 0.25,
            "dxy": 0.15,
            "crude": 0.10,
            "usdinr": 0.15,
        }

        raw_score = (
            gift_score * weights["gift_nifty"]
            + us_score * weights["us_markets"]
            + dxy_score * weights["dxy"]
            + crude_score * weights["crude"]
            + usdinr_score * weights["usdinr"]
        )

        # Apply time-based weighting
        score = raw_score * weight_mult

        # Calculate confidence
        confidence = self._calculate_confidence(cues, weight_mult)

        # Generate reason
        reason = self._generate_reason(cues, gift_score, us_score, dxy_score, crude_score, usdinr_score)

        return create_signal(
            signal_type=SignalType.GLOBAL_CUES,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "gift_nifty_score": gift_score,
                "us_markets_score": us_score,
                "dxy_score": dxy_score,
                "crude_score": crude_score,
                "usdinr_score": usdinr_score,
                "time_weight": weight_mult,
                "gift_nifty_change": cues.gift_nifty_change_pct,
                "spx_change": cues.spx_change_pct,
            },
        )

    def _get_time_weight(self) -> float:
        """Get time-based weight multiplier.

        Higher weight in early morning, lower after 11:00.

        Returns:
            Weight multiplier (0.5 to 1.0)
        """
        now = datetime.now(IST).time()

        if self.HIGH_WEIGHT_START <= now <= self.HIGH_WEIGHT_END:
            return 1.0
        elif now >= self.LOW_WEIGHT_START:
            return 0.5
        else:
            # Linear decrease between 9:45 and 11:00
            minutes_since_high_end = (
                datetime.combine(datetime.today(), now)
                - datetime.combine(datetime.today(), self.HIGH_WEIGHT_END)
            ).seconds / 60

            minutes_to_low_start = (
                datetime.combine(datetime.today(), self.LOW_WEIGHT_START)
                - datetime.combine(datetime.today(), self.HIGH_WEIGHT_END)
            ).seconds / 60

            decay = minutes_since_high_end / minutes_to_low_start if minutes_to_low_start > 0 else 0
            return max(0.5, 1.0 - decay * 0.5)

    def _analyze_gift_nifty(self, cues: GlobalCuesData) -> float:
        """Analyze GIFT Nifty.

        Args:
            cues: Global cues data

        Returns:
            Score -1 to +1
        """
        if cues.gift_nifty_change_pct is None:
            return 0.0

        change = cues.gift_nifty_change_pct

        if change >= self.GIFT_NIFTY_BULLISH_THRESHOLD:
            return min(1.0, change / 2)  # Scale: +2% = 1.0
        elif change <= self.GIFT_NIFTY_BEARISH_THRESHOLD:
            return max(-1.0, change / 2)  # Scale: -2% = -1.0
        else:
            return change / 2  # Linear scaling

    def _analyze_us_markets(self, cues: GlobalCuesData) -> float:
        """Analyze US markets.

        Args:
            cues: Global cues data

        Returns:
            Score -1 to +1
        """
        scores = []

        if cues.spx_change_pct is not None:
            if cues.spx_change_pct >= self.SPX_BULLISH_THRESHOLD:
                scores.append(min(1.0, cues.spx_change_pct / 1.5))
            elif cues.spx_change_pct <= self.SPX_BEARISH_THRESHOLD:
                scores.append(max(-1.0, cues.spx_change_pct / 1.5))
            else:
                scores.append(cues.spx_change_pct / 1.5)

        if cues.nasdaq_change_pct is not None:
            if cues.nasdaq_change_pct >= self.SPX_BULLISH_THRESHOLD:
                scores.append(min(1.0, cues.nasdaq_change_pct / 2))
            elif cues.nasdaq_change_pct <= self.SPX_BEARISH_THRESHOLD:
                scores.append(max(-1.0, cues.nasdaq_change_pct / 2))
            else:
                scores.append(cues.nasdaq_change_pct / 2)

        return sum(scores) / len(scores) if scores else 0.0

    def _analyze_dxy(self, cues: GlobalCuesData) -> float:
        """Analyze DXY (Dollar Index).

        DXY rise = bearish for EM/India.

        Args:
            cues: Global cues data

        Returns:
            Score -1 to +1
        """
        if cues.dxy_change_pct is None:
            return 0.0

        change = cues.dxy_change_pct

        # Inverse relationship - DXY up is bearish for India
        if change >= self.DXY_BEARISH_THRESHOLD:
            return max(-1.0, -change / 1.5)
        elif change <= self.DXY_BULLISH_THRESHOLD:
            return min(1.0, -change / 1.0)
        else:
            return -change / 1.5

    def _analyze_crude(self, cues: GlobalCuesData) -> float:
        """Analyze Crude oil price.

        High crude = bearish for India (importer).

        Args:
            cues: Global cues data

        Returns:
            Score -1 to +1
        """
        if cues.crude_wti is None:
            return 0.0

        price = cues.crude_wti

        if price >= self.CRUDE_VERY_HIGH:
            return -0.8
        elif price >= self.CRUDE_HIGH_THRESHOLD:
            return -0.4
        elif price < 70:
            return 0.3  # Low crude is mildly bullish
        else:
            return 0.0

    def _analyze_usdinr(self, cues: GlobalCuesData) -> float:
        """Analyze USD/INR.

        High USD/INR = INR weakness = FII outflows = bearish.

        Args:
            cues: Global cues data

        Returns:
            Score -1 to +1
        """
        if cues.usdinr is None:
            return 0.0

        rate = cues.usdinr

        if rate >= self.USDINR_VERY_HIGH:
            return -0.6
        elif rate >= self.USDINR_HIGH_THRESHOLD:
            return -0.3
        elif rate < 82:
            return 0.2  # Strong INR is mildly bullish
        else:
            return 0.0

    def _calculate_confidence(
        self, cues: GlobalCuesData, time_weight: float
    ) -> float:
        """Calculate confidence based on data availability.

        Args:
            cues: Global cues data
            time_weight: Time-based weight

        Returns:
            Confidence 0-1
        """
        data_points = 0
        total_points = 5  # gift, spx, dxy, crude, usdinr

        if cues.gift_nifty_change_pct is not None:
            data_points += 1
        if cues.spx_change_pct is not None:
            data_points += 1
        if cues.dxy_change_pct is not None:
            data_points += 1
        if cues.crude_wti is not None:
            data_points += 1
        if cues.usdinr is not None:
            data_points += 1

        base_confidence = (data_points / total_points) * 0.7
        return base_confidence * time_weight + 0.3

    def _generate_reason(
        self,
        cues: GlobalCuesData,
        gift_score: float,
        us_score: float,
        dxy_score: float,
        crude_score: float,
        usdinr_score: float,
    ) -> str:
        """Generate reason string.

        Args:
            cues: Global cues data
            gift_score: GIFT Nifty score
            us_score: US markets score
            dxy_score: DXY score
            crude_score: Crude score
            usdinr_score: USD/INR score

        Returns:
            Reason string
        """
        reasons = []

        if cues.gift_nifty_change_pct is not None:
            direction = "+" if cues.gift_nifty_change_pct > 0 else ""
            reasons.append(
                f"GIFT Nifty {direction}{cues.gift_nifty_change_pct:.1f}%"
            )

        if cues.spx_change_pct is not None:
            direction = "+" if cues.spx_change_pct > 0 else ""
            reasons.append(f"SPX {direction}{cues.spx_change_pct:.1f}%")

        if cues.dxy_change_pct is not None and abs(cues.dxy_change_pct) > 0.3:
            direction = "up" if cues.dxy_change_pct > 0 else "down"
            reasons.append(f"DXY {direction} (bearish)" if cues.dxy_change_pct > 0 else f"DXY {direction} (bullish)")

        if cues.crude_wti is not None and cues.crude_wti >= self.CRUDE_HIGH_THRESHOLD:
            reasons.append(f"Crude ${cues.crude_wti:.0f} (high)")

        if cues.usdinr is not None and cues.usdinr >= self.USDINR_HIGH_THRESHOLD:
            reasons.append(f"USD/INR {cues.usdinr:.2f} (INR weak)")

        return " | ".join(reasons) if reasons else "No significant global cues"
