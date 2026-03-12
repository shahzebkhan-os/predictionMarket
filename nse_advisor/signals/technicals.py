"""
Technicals Signal.

Signal 6: Technical indicators using pandas-ta.
Supertrend, RSI, Bollinger Bands, EMA crossover, ATR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd
from zoneinfo import ZoneInfo

from nse_advisor.signals.engine import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class TechnicalMetrics:
    """Technical analysis metrics."""
    # Supertrend
    supertrend_direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    supertrend_value: float
    
    # RSI
    rsi_14: float
    rsi_divergence: str  # "BULLISH_DIV", "BEARISH_DIV", "NONE"
    
    # Bollinger Bands
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_bandwidth: float
    bb_squeeze: bool
    
    # EMAs
    ema_9: float
    ema_21: float
    ema_crossover: str  # "BULLISH", "BEARISH", "NONE"
    
    # Volume
    volume_ratio: float  # Current vs average
    
    # Current price
    current_price: float


class TechnicalsAnalyzer:
    """
    Analyzes technical indicators.
    
    Signal scoring:
    - Supertrend(10,3) bullish → +0.3, bearish → -0.3
    - RSI(14) oversold (<30) → +0.2, overbought (>70) → -0.2
    - RSI divergence: bullish → +0.3, bearish → -0.3
    - Bollinger squeeze (bandwidth < 1%) → flag expansion imminent
    - EMA 9 vs 21 crossover: bullish → +0.2, bearish → -0.2
    - Volume > 2× avg on breakout → +0.1 confidence boost
    
    Requires backfill of 50 candles to avoid cold-start garbage values.
    """
    
    # Supertrend parameters
    ST_LENGTH = 10
    ST_MULTIPLIER = 3.0
    
    # RSI parameters
    RSI_PERIOD = 14
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    
    # Bollinger parameters
    BB_LENGTH = 20
    BB_STD = 2.0
    BB_SQUEEZE_THRESHOLD = 1.0  # %
    
    # EMA parameters
    EMA_FAST = 9
    EMA_SLOW = 21
    
    # Volume threshold
    VOLUME_BREAKOUT_RATIO = 2.0
    
    def __init__(self) -> None:
        """Initialize analyzer."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._prev_rsi: float | None = None
        self._prev_price: float | None = None
    
    def calculate_supertrend(
        self,
        df: pd.DataFrame,
        length: int = 10,
        multiplier: float = 3.0
    ) -> tuple[pd.Series, pd.Series]:
        """
        Calculate Supertrend indicator.
        
        Returns:
            Tuple of (supertrend_value, supertrend_direction)
            Direction: 1 = bullish, -1 = bearish
        """
        if len(df) < length:
            return (
                pd.Series([df["close"].iloc[-1]] * len(df)),
                pd.Series([0] * len(df))
            )
        
        # ATR calculation
        high = df["high"]
        low = df["low"]
        close = df["close"]
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(span=length, adjust=False).mean()
        
        # Basic bands
        hl2 = (high + low) / 2
        upper_band = hl2 + (multiplier * atr)
        lower_band = hl2 - (multiplier * atr)
        
        # Supertrend calculation
        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)
        
        supertrend.iloc[0] = upper_band.iloc[0]
        direction.iloc[0] = 1
        
        for i in range(1, len(df)):
            if close.iloc[i] > supertrend.iloc[i-1]:
                supertrend.iloc[i] = max(lower_band.iloc[i], supertrend.iloc[i-1])
                direction.iloc[i] = 1
            else:
                supertrend.iloc[i] = min(upper_band.iloc[i], supertrend.iloc[i-1])
                direction.iloc[i] = -1
        
        return (supertrend, direction)
    
    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI."""
        if len(df) < period:
            return pd.Series([50.0] * len(df))
        
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()
        
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_bollinger(
        self,
        df: pd.DataFrame,
        length: int = 20,
        std: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """
        Calculate Bollinger Bands.
        
        Returns:
            Tuple of (upper, middle, lower, bandwidth)
        """
        if len(df) < length:
            close = df["close"].iloc[-1]
            return (
                pd.Series([close * 1.02] * len(df)),
                pd.Series([close] * len(df)),
                pd.Series([close * 0.98] * len(df)),
                pd.Series([4.0] * len(df))
            )
        
        middle = df["close"].rolling(length).mean()
        rolling_std = df["close"].rolling(length).std()
        
        upper = middle + (std * rolling_std)
        lower = middle - (std * rolling_std)
        
        bandwidth = ((upper - lower) / middle) * 100
        
        return (upper, middle, lower, bandwidth)
    
    def detect_rsi_divergence(
        self,
        price: float,
        rsi: float
    ) -> str:
        """
        Detect RSI divergence.
        
        Bullish: Price making lower lows, RSI making higher lows
        Bearish: Price making higher highs, RSI making lower highs
        """
        if self._prev_rsi is None or self._prev_price is None:
            self._prev_rsi = rsi
            self._prev_price = price
            return "NONE"
        
        divergence = "NONE"
        
        # Bullish divergence
        if price < self._prev_price and rsi > self._prev_rsi:
            divergence = "BULLISH_DIV"
        
        # Bearish divergence
        elif price > self._prev_price and rsi < self._prev_rsi:
            divergence = "BEARISH_DIV"
        
        self._prev_rsi = rsi
        self._prev_price = price
        
        return divergence
    
    def analyze(self, df: pd.DataFrame) -> TechnicalMetrics:
        """
        Analyze all technical indicators.
        
        Args:
            df: OHLCV DataFrame with at least 50 rows
            
        Returns:
            TechnicalMetrics with all indicator values
        """
        if df.empty or len(df) < 20:
            return TechnicalMetrics(
                supertrend_direction="NEUTRAL",
                supertrend_value=0.0,
                rsi_14=50.0,
                rsi_divergence="NONE",
                bb_upper=0.0,
                bb_middle=0.0,
                bb_lower=0.0,
                bb_bandwidth=5.0,
                bb_squeeze=False,
                ema_9=0.0,
                ema_21=0.0,
                ema_crossover="NONE",
                volume_ratio=1.0,
                current_price=0.0,
            )
        
        current_price = df["close"].iloc[-1]
        
        # Supertrend
        st_value, st_dir = self.calculate_supertrend(df, self.ST_LENGTH, self.ST_MULTIPLIER)
        st_direction = "BULLISH" if st_dir.iloc[-1] == 1 else "BEARISH"
        
        # RSI
        rsi = self.calculate_rsi(df, self.RSI_PERIOD)
        rsi_14 = rsi.iloc[-1]
        rsi_divergence = self.detect_rsi_divergence(current_price, rsi_14)
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower, bb_bandwidth = self.calculate_bollinger(
            df, self.BB_LENGTH, self.BB_STD
        )
        bb_squeeze = bb_bandwidth.iloc[-1] < self.BB_SQUEEZE_THRESHOLD
        
        # EMAs
        ema_9 = df["close"].ewm(span=self.EMA_FAST, adjust=False).mean().iloc[-1]
        ema_21 = df["close"].ewm(span=self.EMA_SLOW, adjust=False).mean().iloc[-1]
        
        # EMA crossover
        prev_ema_9 = df["close"].ewm(span=self.EMA_FAST, adjust=False).mean().iloc[-2]
        prev_ema_21 = df["close"].ewm(span=self.EMA_SLOW, adjust=False).mean().iloc[-2]
        
        if prev_ema_9 <= prev_ema_21 and ema_9 > ema_21:
            ema_crossover = "BULLISH"
        elif prev_ema_9 >= prev_ema_21 and ema_9 < ema_21:
            ema_crossover = "BEARISH"
        else:
            ema_crossover = "NONE"
        
        # Volume ratio
        if "volume" in df.columns:
            avg_volume = df["volume"].rolling(20).mean().iloc[-1]
            current_volume = df["volume"].iloc[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        else:
            volume_ratio = 1.0
        
        return TechnicalMetrics(
            supertrend_direction=st_direction,
            supertrend_value=st_value.iloc[-1],
            rsi_14=rsi_14,
            rsi_divergence=rsi_divergence,
            bb_upper=bb_upper.iloc[-1],
            bb_middle=bb_middle.iloc[-1],
            bb_lower=bb_lower.iloc[-1],
            bb_bandwidth=bb_bandwidth.iloc[-1],
            bb_squeeze=bb_squeeze,
            ema_9=ema_9,
            ema_21=ema_21,
            ema_crossover=ema_crossover,
            volume_ratio=volume_ratio,
            current_price=current_price,
        )
    
    def compute_signal(
        self,
        price_data: pd.DataFrame,
        **kwargs
    ) -> SignalResult:
        """
        Compute technicals signal.
        
        Returns:
            SignalResult with score from -1 to +1
        """
        now = datetime.now(self._ist)
        
        if price_data is None or price_data.empty:
            return SignalResult(
                name="technicals",
                score=0.0,
                confidence=0.0,
                reason="No price data",
                timestamp=now,
            )
        
        metrics = self.analyze(price_data)
        
        score = 0.0
        reasons = []
        confidence = 0.5
        
        # Supertrend (primary trend)
        if metrics.supertrend_direction == "BULLISH":
            score += 0.3
            reasons.append("Supertrend bullish")
        elif metrics.supertrend_direction == "BEARISH":
            score -= 0.3
            reasons.append("Supertrend bearish")
        
        # RSI
        if metrics.rsi_14 < self.RSI_OVERSOLD:
            score += 0.2
            reasons.append(f"RSI oversold ({metrics.rsi_14:.0f})")
        elif metrics.rsi_14 > self.RSI_OVERBOUGHT:
            score -= 0.2
            reasons.append(f"RSI overbought ({metrics.rsi_14:.0f})")
        
        # RSI divergence
        if metrics.rsi_divergence == "BULLISH_DIV":
            score += 0.3
            confidence += 0.1
            reasons.append("Bullish RSI divergence")
        elif metrics.rsi_divergence == "BEARISH_DIV":
            score -= 0.3
            confidence += 0.1
            reasons.append("Bearish RSI divergence")
        
        # Bollinger squeeze
        if metrics.bb_squeeze:
            confidence += 0.1
            reasons.append("Bollinger squeeze (expansion imminent)")
        
        # EMA crossover
        if metrics.ema_crossover == "BULLISH":
            score += 0.2
            confidence += 0.1
            reasons.append("Bullish EMA crossover (9/21)")
        elif metrics.ema_crossover == "BEARISH":
            score -= 0.2
            confidence += 0.1
            reasons.append("Bearish EMA crossover (9/21)")
        
        # Volume confirmation
        if metrics.volume_ratio >= self.VOLUME_BREAKOUT_RATIO:
            confidence += 0.15
            reasons.append(f"High volume ({metrics.volume_ratio:.1f}x avg)")
        
        return SignalResult(
            name="technicals",
            score=max(-1.0, min(1.0, score)),
            confidence=min(1.0, confidence),
            reason="; ".join(reasons) if reasons else "Technicals neutral",
            timestamp=now,
        )


# Global instance
_technicals_analyzer: TechnicalsAnalyzer | None = None


def get_technicals_analyzer() -> TechnicalsAnalyzer:
    """Get or create global technicals analyzer."""
    global _technicals_analyzer
    if _technicals_analyzer is None:
        _technicals_analyzer = TechnicalsAnalyzer()
    return _technicals_analyzer


async def compute_technicals_signal(
    price_data: pd.DataFrame | None,
    **kwargs
) -> SignalResult:
    """Compute technicals signal (async wrapper)."""
    analyzer = get_technicals_analyzer()
    
    if price_data is None:
        return SignalResult(
            name="technicals",
            score=0.0,
            confidence=0.0,
            reason="No price data",
            timestamp=datetime.now(ZoneInfo("Asia/Kolkata")),
        )
    
    return analyzer.compute_signal(price_data, **kwargs)
