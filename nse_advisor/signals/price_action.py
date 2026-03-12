"""
Price Action Signal.

Signal 5: VWAP, opening range, gaps, and key price levels.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class PriceActionMetrics:
    """Price action analysis metrics."""
    current_price: float
    vwap: float
    vwap_distance_pct: float
    is_above_vwap: bool
    minutes_above_vwap: int
    opening_range_high: float
    opening_range_low: float
    gap_pct: float
    prev_day_high: float
    prev_day_low: float
    prev_day_close: float
    atr: float


class PriceActionAnalyzer:
    """
    Analyzes price action patterns.
    
    Signal scoring:
    - Price > VWAP for > 30min → Bullish bias (+0.3 to +0.5)
    - Price < VWAP for > 30min → Bearish bias (-0.3 to -0.5)
    - Gap > +0.5% → Gap fill probability assessment
    - Opening range breakout/breakdown signals
    - Previous day levels as support/resistance
    """
    
    # Thresholds
    VWAP_MINUTES_THRESHOLD = 30  # Minutes above/below VWAP for signal
    GAP_THRESHOLD = 0.5  # % gap threshold
    OR_BREAKOUT_THRESHOLD = 0.3  # % beyond opening range
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._vwap_time_above: int = 0
        self._vwap_time_below: int = 0
        self._opening_range_high: float | None = None
        self._opening_range_low: float | None = None
        self._last_check: datetime | None = None
    
    def calculate_vwap(self, df: pd.DataFrame) -> float:
        """
        Calculate VWAP from OHLCV data.
        
        VWAP = Σ(Typical Price × Volume) / Σ(Volume)
        Typical Price = (High + Low + Close) / 3
        """
        if df.empty:
            return 0.0
        
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        volume = df.get("volume", pd.Series([1] * len(df)))
        
        cumulative_tp_vol = (typical_price * volume).cumsum()
        cumulative_vol = volume.cumsum()
        
        vwap = cumulative_tp_vol / cumulative_vol
        
        return vwap.iloc[-1] if not vwap.empty else 0.0
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range."""
        if len(df) < period:
            return 0.0
        
        high = df["high"]
        low = df["low"]
        close = df["close"]
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        
        return atr.iloc[-1] if not atr.empty else 0.0
    
    def analyze(
        self,
        df: pd.DataFrame,
        prev_day_high: float | None = None,
        prev_day_low: float | None = None,
        prev_day_close: float | None = None,
    ) -> PriceActionMetrics:
        """
        Analyze price action from OHLCV data.
        
        Args:
            df: Intraday OHLCV DataFrame
            prev_day_high: Previous day's high
            prev_day_low: Previous day's low
            prev_day_close: Previous day's close
            
        Returns:
            PriceActionMetrics with analysis results
        """
        now = datetime.now(self._ist)
        
        if df.empty:
            return PriceActionMetrics(
                current_price=0.0,
                vwap=0.0,
                vwap_distance_pct=0.0,
                is_above_vwap=False,
                minutes_above_vwap=0,
                opening_range_high=0.0,
                opening_range_low=0.0,
                gap_pct=0.0,
                prev_day_high=prev_day_high or 0.0,
                prev_day_low=prev_day_low or 0.0,
                prev_day_close=prev_day_close or 0.0,
                atr=0.0,
            )
        
        current_price = df["close"].iloc[-1]
        vwap = self.calculate_vwap(df)
        atr = self.calculate_atr(df)
        
        # VWAP distance
        vwap_distance = ((current_price - vwap) / vwap) * 100 if vwap > 0 else 0
        is_above_vwap = current_price > vwap
        
        # Track time above/below VWAP
        if self._last_check:
            minutes_elapsed = (now - self._last_check).total_seconds() / 60
            if is_above_vwap:
                self._vwap_time_above += int(minutes_elapsed)
                self._vwap_time_below = 0
            else:
                self._vwap_time_below += int(minutes_elapsed)
                self._vwap_time_above = 0
        
        self._last_check = now
        
        # Opening range (first 15 minutes)
        market_open = time(9, 15)
        or_end = time(9, 30)
        
        if hasattr(df.index[0], 'time'):
            or_data = df[
                (df.index.time >= market_open) & (df.index.time <= or_end)
            ]
        else:
            # First 3 candles for 5-min data
            or_data = df.head(3)
        
        if not or_data.empty:
            self._opening_range_high = or_data["high"].max()
            self._opening_range_low = or_data["low"].min()
        
        # Gap calculation
        gap_pct = 0.0
        if prev_day_close and prev_day_close > 0:
            open_price = df["open"].iloc[0]
            gap_pct = ((open_price - prev_day_close) / prev_day_close) * 100
        
        return PriceActionMetrics(
            current_price=current_price,
            vwap=vwap,
            vwap_distance_pct=vwap_distance,
            is_above_vwap=is_above_vwap,
            minutes_above_vwap=self._vwap_time_above if is_above_vwap else -self._vwap_time_below,
            opening_range_high=self._opening_range_high or 0.0,
            opening_range_low=self._opening_range_low or 0.0,
            gap_pct=gap_pct,
            prev_day_high=prev_day_high or 0.0,
            prev_day_low=prev_day_low or 0.0,
            prev_day_close=prev_day_close or 0.0,
            atr=atr,
        )
    
    def compute_signal(
        self,
        price_data: pd.DataFrame,
        prev_day_high: float | None = None,
        prev_day_low: float | None = None,
        prev_day_close: float | None = None,
        **kwargs
    ) -> SignalResult:
        """
        Compute price action signal.
        
        Returns:
            SignalResult with score from -1 to +1
        """
        now = datetime.now(self._ist)
        
        if price_data is None or price_data.empty:
            return SignalResult(
                name="price_action",
                score=0.0,
                confidence=0.0,
                reason="No price data",
                timestamp=now,
            )
        
        metrics = self.analyze(
            price_data, prev_day_high, prev_day_low, prev_day_close
        )
        
        score = 0.0
        reasons = []
        confidence = 0.5
        
        # VWAP signal
        if abs(metrics.minutes_above_vwap) >= self.VWAP_MINUTES_THRESHOLD:
            if metrics.is_above_vwap:
                vwap_score = 0.4
                reasons.append(f"Above VWAP for {metrics.minutes_above_vwap}min (bullish)")
            else:
                vwap_score = -0.4
                reasons.append(f"Below VWAP for {abs(metrics.minutes_above_vwap)}min (bearish)")
            
            score += vwap_score
            confidence += 0.1
        
        # Opening range breakout/breakdown
        if metrics.opening_range_high > 0 and metrics.opening_range_low > 0:
            or_range = metrics.opening_range_high - metrics.opening_range_low
            
            if metrics.current_price > metrics.opening_range_high:
                breakout_pct = (
                    (metrics.current_price - metrics.opening_range_high) / 
                    metrics.opening_range_high
                ) * 100
                
                if breakout_pct >= self.OR_BREAKOUT_THRESHOLD:
                    score += 0.3
                    confidence += 0.15
                    reasons.append(f"OR breakout +{breakout_pct:.2f}%")
                    
            elif metrics.current_price < metrics.opening_range_low:
                breakdown_pct = (
                    (metrics.opening_range_low - metrics.current_price) /
                    metrics.opening_range_low
                ) * 100
                
                if breakdown_pct >= self.OR_BREAKOUT_THRESHOLD:
                    score -= 0.3
                    confidence += 0.15
                    reasons.append(f"OR breakdown -{breakdown_pct:.2f}%")
        
        # Gap analysis
        if abs(metrics.gap_pct) >= self.GAP_THRESHOLD:
            # Large gaps often fill
            if metrics.gap_pct > 0:
                # Gap up - check if filling
                if metrics.current_price < metrics.prev_day_close * 1.001:
                    reasons.append(f"Gap up {metrics.gap_pct:.1f}% filling")
                else:
                    score += 0.15  # Gap continuation
                    reasons.append(f"Gap up {metrics.gap_pct:.1f}% holding")
            else:
                # Gap down
                if metrics.current_price > metrics.prev_day_close * 0.999:
                    reasons.append(f"Gap down {abs(metrics.gap_pct):.1f}% filling")
                else:
                    score -= 0.15
                    reasons.append(f"Gap down {abs(metrics.gap_pct):.1f}% holding")
        
        # Previous day levels
        if prev_day_high and metrics.current_price > prev_day_high:
            score += 0.2
            confidence += 0.1
            reasons.append("Above previous day high")
        elif prev_day_low and metrics.current_price < prev_day_low:
            score -= 0.2
            confidence += 0.1
            reasons.append("Below previous day low")
        
        return SignalResult(
            name="price_action",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "Price action neutral",
            timestamp=now,
        )


# Global instance
_price_action_analyzer: PriceActionAnalyzer | None = None


def get_price_action_analyzer() -> PriceActionAnalyzer:
    """Get or create global price action analyzer."""
    global _price_action_analyzer
    if _price_action_analyzer is None:
        _price_action_analyzer = PriceActionAnalyzer()
    return _price_action_analyzer


async def compute_price_action_signal(
    price_data: pd.DataFrame | None,
    prev_day_high: float | None = None,
    prev_day_low: float | None = None,
    prev_day_close: float | None = None,
    **kwargs
) -> SignalResult:
    """Compute price action signal (async wrapper)."""
    analyzer = get_price_action_analyzer()
    
    if price_data is None:
        return SignalResult(
            name="price_action",
            score=0.0,
            confidence=0.0,
            reason="No price data",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
    
    return analyzer.compute_signal(
        price_data, prev_day_high, prev_day_low, prev_day_close, **kwargs
    )
