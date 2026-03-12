"""Price Action & VWAP Signal (Signal 5).

Price>VWAP for >30min → bullish bias.
Price<VWAP for >30min → bearish bias.
Opening range breakout detection.
Gap analysis.
Key support/resistance levels.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytz
import structlog

from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class VWAPState:
    """VWAP tracking state."""

    vwap: Decimal
    upper_band: Decimal  # +1 std dev
    lower_band: Decimal  # -1 std dev
    price_position: str  # "above", "below", "at"
    minutes_above: int
    minutes_below: int


@dataclass
class OpeningRange:
    """Opening range analysis."""

    high: Decimal
    low: Decimal
    range_pct: float
    breakout_detected: bool
    breakout_direction: str  # "up", "down", "none"
    breakout_strength: float


@dataclass
class GapAnalysis:
    """Gap analysis."""

    gap_pct: float
    gap_type: str  # "gap_up", "gap_down", "no_gap"
    gap_filled: bool
    fill_pct: float


class PriceActionAnalyzer:
    """Price action and VWAP analyzer."""

    # VWAP thresholds
    VWAP_BIAS_MINUTES = 30  # 30 min above/below VWAP = bias
    VWAP_DEVIATION_THRESHOLD = 0.5  # 0.5% deviation from VWAP

    # Opening range settings
    OR_PERIOD_MINUTES = 15  # First 15 minutes
    OR_BREAKOUT_THRESHOLD = 0.3  # 0.3% beyond OR for breakout

    # Gap settings
    GAP_THRESHOLD = 0.5  # 0.5% = significant gap

    def __init__(self) -> None:
        """Initialize analyzer."""
        self._price_history: deque[tuple[datetime, Decimal]] = deque(maxlen=1000)
        self._volume_history: deque[tuple[datetime, int]] = deque(maxlen=1000)
        self._or_high: Decimal | None = None
        self._or_low: Decimal | None = None
        self._prev_close: Decimal | None = None
        self._vwap_minutes_above = 0
        self._vwap_minutes_below = 0

    def set_previous_close(self, close: Decimal) -> None:
        """Set previous day close for gap calculation.

        Args:
            close: Previous close price
        """
        self._prev_close = close

    def add_price(self, timestamp: datetime, price: Decimal, volume: int) -> None:
        """Add price and volume data.

        Args:
            timestamp: Timestamp
            price: Price
            volume: Volume
        """
        self._price_history.append((timestamp, price))
        self._volume_history.append((timestamp, volume))

        # Track opening range
        market_open = timestamp.replace(
            hour=9, minute=15, second=0, microsecond=0
        )
        or_end = market_open + timedelta(minutes=self.OR_PERIOD_MINUTES)

        if market_open <= timestamp <= or_end:
            if self._or_high is None or price > self._or_high:
                self._or_high = price
            if self._or_low is None or price < self._or_low:
                self._or_low = price

    def calculate_vwap(self) -> Decimal:
        """Calculate VWAP from history.

        Returns:
            VWAP value
        """
        if not self._price_history or not self._volume_history:
            return Decimal("0")

        # Filter for current day
        today = datetime.now(IST).date()
        day_prices = [
            (ts, price)
            for ts, price in self._price_history
            if ts.date() == today
        ]
        day_volumes = [
            (ts, vol)
            for ts, vol in self._volume_history
            if ts.date() == today
        ]

        if not day_prices or not day_volumes:
            return Decimal("0")

        # Match prices with volumes
        total_value = Decimal("0")
        total_volume = 0

        for (ts_p, price), (ts_v, vol) in zip(day_prices, day_volumes):
            total_value += price * Decimal(str(vol))
            total_volume += vol

        if total_volume == 0:
            return Decimal("0")

        return total_value / Decimal(str(total_volume))

    def analyze(
        self,
        current_price: Decimal,
        day_high: Decimal,
        day_low: Decimal,
        day_open: Decimal,
        volume: int = 0,
    ) -> Signal:
        """Analyze price action.

        Args:
            current_price: Current price
            day_high: Day high
            day_low: Day low
            day_open: Day open
            volume: Current volume

        Returns:
            Price action signal
        """
        now = datetime.now(IST)
        self.add_price(now, current_price, volume)

        # VWAP analysis
        vwap = self.calculate_vwap()
        vwap_state = self._analyze_vwap(current_price, vwap)

        # Opening range analysis
        or_analysis = self._analyze_opening_range(current_price, now)

        # Gap analysis
        gap_analysis = self._analyze_gap(day_open, current_price)

        # Key levels
        key_levels = self._identify_key_levels(
            current_price, day_high, day_low, vwap
        )

        # Calculate score
        score, confidence, reason = self._calculate_score(
            vwap_state, or_analysis, gap_analysis, key_levels, current_price
        )

        return create_signal(
            signal_type=SignalType.PRICE_ACTION,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "vwap": float(vwap),
                "vwap_deviation_pct": float(
                    (current_price - vwap) / vwap * 100 if vwap else 0
                ),
                "vwap_minutes_above": vwap_state.minutes_above,
                "vwap_minutes_below": vwap_state.minutes_below,
                "or_breakout": or_analysis.breakout_detected,
                "or_direction": or_analysis.breakout_direction,
                "gap_pct": gap_analysis.gap_pct,
                "gap_filled": gap_analysis.gap_filled,
            },
        )

    def _analyze_vwap(
        self, current_price: Decimal, vwap: Decimal
    ) -> VWAPState:
        """Analyze price position relative to VWAP.

        Args:
            current_price: Current price
            vwap: VWAP value

        Returns:
            VWAPState
        """
        if vwap == 0:
            return VWAPState(
                vwap=Decimal("0"),
                upper_band=Decimal("0"),
                lower_band=Decimal("0"),
                price_position="at",
                minutes_above=0,
                minutes_below=0,
            )

        # Calculate bands (simplified - should use std dev)
        band_width = vwap * Decimal("0.01")  # 1% bands
        upper_band = vwap + band_width
        lower_band = vwap - band_width

        # Determine position
        if current_price > vwap:
            price_position = "above"
            self._vwap_minutes_above += 1
            self._vwap_minutes_below = 0
        elif current_price < vwap:
            price_position = "below"
            self._vwap_minutes_below += 1
            self._vwap_minutes_above = 0
        else:
            price_position = "at"

        return VWAPState(
            vwap=vwap,
            upper_band=upper_band,
            lower_band=lower_band,
            price_position=price_position,
            minutes_above=self._vwap_minutes_above,
            minutes_below=self._vwap_minutes_below,
        )

    def _analyze_opening_range(
        self, current_price: Decimal, now: datetime
    ) -> OpeningRange:
        """Analyze opening range.

        Args:
            current_price: Current price
            now: Current timestamp

        Returns:
            OpeningRange
        """
        if self._or_high is None or self._or_low is None:
            return OpeningRange(
                high=current_price,
                low=current_price,
                range_pct=0.0,
                breakout_detected=False,
                breakout_direction="none",
                breakout_strength=0.0,
            )

        range_pct = float(
            (self._or_high - self._or_low) / self._or_low * 100
        )

        # Check for breakout after OR period
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        or_end = market_open + timedelta(minutes=self.OR_PERIOD_MINUTES)

        breakout = False
        direction = "none"
        strength = 0.0

        if now > or_end:
            breakout_threshold = (self._or_high - self._or_low) * Decimal(
                str(self.OR_BREAKOUT_THRESHOLD)
            )

            if current_price > self._or_high + breakout_threshold:
                breakout = True
                direction = "up"
                strength = float(
                    (current_price - self._or_high) / (self._or_high - self._or_low)
                )
            elif current_price < self._or_low - breakout_threshold:
                breakout = True
                direction = "down"
                strength = float(
                    (self._or_low - current_price) / (self._or_high - self._or_low)
                )

        return OpeningRange(
            high=self._or_high,
            low=self._or_low,
            range_pct=range_pct,
            breakout_detected=breakout,
            breakout_direction=direction,
            breakout_strength=min(1.0, strength),
        )

    def _analyze_gap(
        self, day_open: Decimal, current_price: Decimal
    ) -> GapAnalysis:
        """Analyze opening gap.

        Args:
            day_open: Day open
            current_price: Current price

        Returns:
            GapAnalysis
        """
        if self._prev_close is None:
            return GapAnalysis(
                gap_pct=0.0,
                gap_type="no_gap",
                gap_filled=False,
                fill_pct=0.0,
            )

        gap_pct = float((day_open - self._prev_close) / self._prev_close * 100)

        if gap_pct >= self.GAP_THRESHOLD:
            gap_type = "gap_up"
        elif gap_pct <= -self.GAP_THRESHOLD:
            gap_type = "gap_down"
        else:
            gap_type = "no_gap"

        # Check if gap filled
        gap_filled = False
        fill_pct = 0.0

        if gap_type == "gap_up":
            if current_price <= self._prev_close:
                gap_filled = True
                fill_pct = 100.0
            else:
                fill_pct = float(
                    (day_open - current_price) / (day_open - self._prev_close) * 100
                )

        elif gap_type == "gap_down":
            if current_price >= self._prev_close:
                gap_filled = True
                fill_pct = 100.0
            else:
                fill_pct = float(
                    (current_price - day_open) / (self._prev_close - day_open) * 100
                )

        return GapAnalysis(
            gap_pct=gap_pct,
            gap_type=gap_type,
            gap_filled=gap_filled,
            fill_pct=max(0, min(100, fill_pct)),
        )

    def _identify_key_levels(
        self,
        current_price: Decimal,
        day_high: Decimal,
        day_low: Decimal,
        vwap: Decimal,
    ) -> dict[str, Decimal]:
        """Identify key support/resistance levels.

        Args:
            current_price: Current price
            day_high: Day high
            day_low: Day low
            vwap: VWAP

        Returns:
            Dict of level names to prices
        """
        levels = {
            "day_high": day_high,
            "day_low": day_low,
            "vwap": vwap,
        }

        if self._or_high:
            levels["or_high"] = self._or_high
        if self._or_low:
            levels["or_low"] = self._or_low
        if self._prev_close:
            levels["prev_close"] = self._prev_close

        return levels

    def _calculate_score(
        self,
        vwap_state: VWAPState,
        or_analysis: OpeningRange,
        gap_analysis: GapAnalysis,
        key_levels: dict[str, Decimal],
        current_price: Decimal,
    ) -> tuple[float, float, str]:
        """Calculate price action score.

        Args:
            vwap_state: VWAP state
            or_analysis: Opening range analysis
            gap_analysis: Gap analysis
            key_levels: Key levels
            current_price: Current price

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # VWAP bias
        if vwap_state.minutes_above >= self.VWAP_BIAS_MINUTES:
            score += 0.4
            reasons.append(f"Price > VWAP for {vwap_state.minutes_above}min → Bullish")
            confidence += 0.1

        elif vwap_state.minutes_below >= self.VWAP_BIAS_MINUTES:
            score -= 0.4
            reasons.append(f"Price < VWAP for {vwap_state.minutes_below}min → Bearish")
            confidence += 0.1

        # Opening range breakout
        if or_analysis.breakout_detected:
            if or_analysis.breakout_direction == "up":
                score += 0.3 + or_analysis.breakout_strength * 0.2
                reasons.append(f"OR breakout UP (strength: {or_analysis.breakout_strength:.1f})")
                confidence += 0.15

            elif or_analysis.breakout_direction == "down":
                score -= 0.3 + or_analysis.breakout_strength * 0.2
                reasons.append(f"OR breakout DOWN (strength: {or_analysis.breakout_strength:.1f})")
                confidence += 0.15

        # Gap analysis
        if gap_analysis.gap_type == "gap_up":
            if not gap_analysis.gap_filled:
                score += 0.2
                reasons.append(f"Gap up {gap_analysis.gap_pct:.1f}% unfilled → Bullish")
            else:
                score -= 0.1
                reasons.append("Gap up filled → Weakness")

        elif gap_analysis.gap_type == "gap_down":
            if not gap_analysis.gap_filled:
                score -= 0.2
                reasons.append(f"Gap down {gap_analysis.gap_pct:.1f}% unfilled → Bearish")
            else:
                score += 0.1
                reasons.append("Gap down filled → Recovery")

        reason = " | ".join(reasons) if reasons else "No significant price action"

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def reset_daily(self) -> None:
        """Reset daily state (call at market open)."""
        self._or_high = None
        self._or_low = None
        self._vwap_minutes_above = 0
        self._vwap_minutes_below = 0
        self._price_history.clear()
        self._volume_history.clear()
