"""Technical Analysis Signal (Signal 6).

Supertrend(10,3) → primary trend.
RSI(14) with divergence detection.
Bollinger Bands(20,2): squeeze (bandwidth<1%) → imminent expansion.
EMA 9 vs 21 crossover.
Volume >2× avg on breakout → high conviction.
ATR(14) → dynamic stop-loss calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import structlog

from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)


@dataclass
class SupertrendResult:
    """Supertrend indicator result."""

    value: float
    direction: int  # 1 = bullish, -1 = bearish
    trend_changed: bool


@dataclass
class RSIResult:
    """RSI indicator result."""

    value: float
    condition: str  # "overbought", "oversold", "neutral"
    bullish_divergence: bool
    bearish_divergence: bool


@dataclass
class BollingerResult:
    """Bollinger Bands result."""

    upper: float
    middle: float
    lower: float
    bandwidth: float
    squeeze: bool
    price_position: str  # "above_upper", "below_lower", "inside"


@dataclass
class EMAResult:
    """EMA crossover result."""

    ema9: float
    ema21: float
    crossover: str  # "bullish", "bearish", "none"


@dataclass
class TechnicalSummary:
    """Technical analysis summary."""

    supertrend: SupertrendResult
    rsi: RSIResult
    bollinger: BollingerResult
    ema: EMAResult
    atr: float
    volume_ratio: float
    overall_bias: str


class TechnicalAnalyzer:
    """Technical analysis using pandas-ta style calculations."""

    # Indicator parameters
    SUPERTREND_PERIOD = 10
    SUPERTREND_MULTIPLIER = 3.0
    RSI_PERIOD = 14
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    BB_PERIOD = 20
    BB_STD = 2.0
    BB_SQUEEZE_THRESHOLD = 1.0  # 1% bandwidth = squeeze
    EMA_FAST = 9
    EMA_SLOW = 21
    ATR_PERIOD = 14
    VOLUME_BREAKOUT_MULT = 2.0

    def __init__(self) -> None:
        """Initialize technical analyzer."""
        self._prev_supertrend_dir: int | None = None
        self._prev_ema_state: str | None = None

    def analyze(
        self,
        df: pd.DataFrame,
        timeframe: str = "5min",
    ) -> Signal:
        """Analyze technical indicators.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            timeframe: Timeframe for context

        Returns:
            Technical signal
        """
        if len(df) < max(self.BB_PERIOD, self.RSI_PERIOD, self.EMA_SLOW) + 5:
            return create_signal(
                signal_type=SignalType.TECHNICALS,
                score=0.0,
                confidence=0.0,
                reason="Insufficient data for technical analysis",
            )

        # Calculate indicators
        supertrend = self._calculate_supertrend(df)
        rsi = self._calculate_rsi(df)
        bollinger = self._calculate_bollinger(df)
        ema = self._calculate_ema_crossover(df)
        atr = self._calculate_atr(df)
        volume_ratio = self._calculate_volume_ratio(df)

        # Calculate score
        score, confidence, reason = self._calculate_score(
            supertrend, rsi, bollinger, ema, volume_ratio
        )

        # Update previous states
        self._prev_supertrend_dir = supertrend.direction
        self._prev_ema_state = ema.crossover

        return create_signal(
            signal_type=SignalType.TECHNICALS,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "supertrend_dir": supertrend.direction,
                "supertrend_changed": supertrend.trend_changed,
                "rsi": rsi.value,
                "rsi_condition": rsi.condition,
                "bb_bandwidth": bollinger.bandwidth,
                "bb_squeeze": bollinger.squeeze,
                "ema_crossover": ema.crossover,
                "atr": atr,
                "volume_ratio": volume_ratio,
            },
        )

    def _calculate_supertrend(self, df: pd.DataFrame) -> SupertrendResult:
        """Calculate Supertrend indicator.

        Args:
            df: OHLCV DataFrame

        Returns:
            SupertrendResult
        """
        # ATR
        df = df.copy()
        df["tr1"] = df["high"] - df["low"]
        df["tr2"] = abs(df["high"] - df["close"].shift())
        df["tr3"] = abs(df["low"] - df["close"].shift())
        df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
        df["atr"] = df["tr"].rolling(window=self.SUPERTREND_PERIOD).mean()

        # Bands
        hl2 = (df["high"] + df["low"]) / 2
        df["basic_upper"] = hl2 + self.SUPERTREND_MULTIPLIER * df["atr"]
        df["basic_lower"] = hl2 - self.SUPERTREND_MULTIPLIER * df["atr"]

        df["final_upper"] = df["basic_upper"]
        df["final_lower"] = df["basic_lower"]

        for i in range(1, len(df)):
            if (
                df["basic_upper"].iloc[i] < df["final_upper"].iloc[i - 1]
                or df["close"].iloc[i - 1] > df["final_upper"].iloc[i - 1]
            ):
                df.loc[df.index[i], "final_upper"] = df["basic_upper"].iloc[i]
            else:
                df.loc[df.index[i], "final_upper"] = df["final_upper"].iloc[i - 1]

            if (
                df["basic_lower"].iloc[i] > df["final_lower"].iloc[i - 1]
                or df["close"].iloc[i - 1] < df["final_lower"].iloc[i - 1]
            ):
                df.loc[df.index[i], "final_lower"] = df["basic_lower"].iloc[i]
            else:
                df.loc[df.index[i], "final_lower"] = df["final_lower"].iloc[i - 1]

        # Direction
        df["supertrend_dir"] = 0
        for i in range(1, len(df)):
            if df["close"].iloc[i] > df["final_upper"].iloc[i - 1]:
                df.loc[df.index[i], "supertrend_dir"] = 1
            elif df["close"].iloc[i] < df["final_lower"].iloc[i - 1]:
                df.loc[df.index[i], "supertrend_dir"] = -1
            else:
                df.loc[df.index[i], "supertrend_dir"] = df["supertrend_dir"].iloc[i - 1]

        direction = int(df["supertrend_dir"].iloc[-1])
        value = (
            df["final_lower"].iloc[-1]
            if direction == 1
            else df["final_upper"].iloc[-1]
        )

        trend_changed = False
        if self._prev_supertrend_dir is not None:
            trend_changed = direction != self._prev_supertrend_dir

        return SupertrendResult(
            value=value,
            direction=direction,
            trend_changed=trend_changed,
        )

    def _calculate_rsi(self, df: pd.DataFrame) -> RSIResult:
        """Calculate RSI with divergence detection.

        Args:
            df: OHLCV DataFrame

        Returns:
            RSIResult
        """
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)

        avg_gain = gain.rolling(window=self.RSI_PERIOD).mean()
        avg_loss = loss.rolling(window=self.RSI_PERIOD).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        current_rsi = rsi.iloc[-1]

        # Condition
        if current_rsi >= self.RSI_OVERBOUGHT:
            condition = "overbought"
        elif current_rsi <= self.RSI_OVERSOLD:
            condition = "oversold"
        else:
            condition = "neutral"

        # Divergence detection (simplified)
        bullish_div = False
        bearish_div = False

        if len(df) >= 10:
            # Check last 10 bars for divergence
            recent_prices = df["close"].iloc[-10:]
            recent_rsi = rsi.iloc[-10:]

            # Bullish: price making lower lows, RSI making higher lows
            price_ll = recent_prices.iloc[-1] < recent_prices.iloc[-5]
            rsi_hl = recent_rsi.iloc[-1] > recent_rsi.iloc[-5]
            if price_ll and rsi_hl and current_rsi < 40:
                bullish_div = True

            # Bearish: price making higher highs, RSI making lower highs
            price_hh = recent_prices.iloc[-1] > recent_prices.iloc[-5]
            rsi_lh = recent_rsi.iloc[-1] < recent_rsi.iloc[-5]
            if price_hh and rsi_lh and current_rsi > 60:
                bearish_div = True

        return RSIResult(
            value=current_rsi,
            condition=condition,
            bullish_divergence=bullish_div,
            bearish_divergence=bearish_div,
        )

    def _calculate_bollinger(self, df: pd.DataFrame) -> BollingerResult:
        """Calculate Bollinger Bands.

        Args:
            df: OHLCV DataFrame

        Returns:
            BollingerResult
        """
        sma = df["close"].rolling(window=self.BB_PERIOD).mean()
        std = df["close"].rolling(window=self.BB_PERIOD).std()

        upper = sma + self.BB_STD * std
        lower = sma - self.BB_STD * std

        current_upper = upper.iloc[-1]
        current_lower = lower.iloc[-1]
        current_middle = sma.iloc[-1]
        current_price = df["close"].iloc[-1]

        bandwidth = ((current_upper - current_lower) / current_middle) * 100
        squeeze = bandwidth < self.BB_SQUEEZE_THRESHOLD

        if current_price > current_upper:
            position = "above_upper"
        elif current_price < current_lower:
            position = "below_lower"
        else:
            position = "inside"

        return BollingerResult(
            upper=current_upper,
            middle=current_middle,
            lower=current_lower,
            bandwidth=bandwidth,
            squeeze=squeeze,
            price_position=position,
        )

    def _calculate_ema_crossover(self, df: pd.DataFrame) -> EMAResult:
        """Calculate EMA crossover.

        Args:
            df: OHLCV DataFrame

        Returns:
            EMAResult
        """
        ema9 = df["close"].ewm(span=self.EMA_FAST).mean()
        ema21 = df["close"].ewm(span=self.EMA_SLOW).mean()

        current_ema9 = ema9.iloc[-1]
        current_ema21 = ema21.iloc[-1]
        prev_ema9 = ema9.iloc[-2]
        prev_ema21 = ema21.iloc[-2]

        crossover = "none"
        if prev_ema9 <= prev_ema21 and current_ema9 > current_ema21:
            crossover = "bullish"
        elif prev_ema9 >= prev_ema21 and current_ema9 < current_ema21:
            crossover = "bearish"

        return EMAResult(
            ema9=current_ema9,
            ema21=current_ema21,
            crossover=crossover,
        )

    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """Calculate ATR.

        Args:
            df: OHLCV DataFrame

        Returns:
            ATR value
        """
        tr1 = df["high"] - df["low"]
        tr2 = abs(df["high"] - df["close"].shift())
        tr3 = abs(df["low"] - df["close"].shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=self.ATR_PERIOD).mean()
        return atr.iloc[-1]

    def _calculate_volume_ratio(self, df: pd.DataFrame) -> float:
        """Calculate volume ratio vs average.

        Args:
            df: OHLCV DataFrame

        Returns:
            Volume ratio
        """
        if "volume" not in df.columns:
            return 1.0

        avg_volume = df["volume"].rolling(window=20).mean()
        current_volume = df["volume"].iloc[-1]

        if avg_volume.iloc[-1] == 0:
            return 1.0

        return current_volume / avg_volume.iloc[-1]

    def _calculate_score(
        self,
        supertrend: SupertrendResult,
        rsi: RSIResult,
        bollinger: BollingerResult,
        ema: EMAResult,
        volume_ratio: float,
    ) -> tuple[float, float, str]:
        """Calculate technical score.

        Args:
            supertrend: Supertrend result
            rsi: RSI result
            bollinger: Bollinger result
            ema: EMA result
            volume_ratio: Volume ratio

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5

        # Supertrend (primary trend)
        if supertrend.direction == 1:
            score += 0.3
            if supertrend.trend_changed:
                score += 0.2
                reasons.append("Supertrend turned BULLISH")
                confidence += 0.15
            else:
                reasons.append("Supertrend bullish")
        else:
            score -= 0.3
            if supertrend.trend_changed:
                score -= 0.2
                reasons.append("Supertrend turned BEARISH")
                confidence += 0.15
            else:
                reasons.append("Supertrend bearish")

        # RSI
        if rsi.bullish_divergence:
            score += 0.2
            reasons.append("RSI bullish divergence")
            confidence += 0.1

        elif rsi.bearish_divergence:
            score -= 0.2
            reasons.append("RSI bearish divergence")
            confidence += 0.1

        elif rsi.condition == "oversold":
            score += 0.1
            reasons.append(f"RSI {rsi.value:.0f} oversold")

        elif rsi.condition == "overbought":
            score -= 0.1
            reasons.append(f"RSI {rsi.value:.0f} overbought")

        # Bollinger squeeze
        if bollinger.squeeze:
            reasons.append("BB squeeze → Expansion imminent")
            confidence += 0.1
            # Squeeze doesn't give direction, but increases conviction when broken

        if bollinger.price_position == "above_upper":
            score += 0.15
            reasons.append("Price above upper BB")

        elif bollinger.price_position == "below_lower":
            score -= 0.15
            reasons.append("Price below lower BB")

        # EMA crossover
        if ema.crossover == "bullish":
            score += 0.2
            reasons.append("EMA 9/21 bullish crossover")
            confidence += 0.1

        elif ema.crossover == "bearish":
            score -= 0.2
            reasons.append("EMA 9/21 bearish crossover")
            confidence += 0.1

        # Volume confirmation
        if volume_ratio >= self.VOLUME_BREAKOUT_MULT:
            if score > 0:
                score += 0.1
                reasons.append(f"Volume {volume_ratio:.1f}x confirms bullish")
            else:
                score -= 0.1
                reasons.append(f"Volume {volume_ratio:.1f}x confirms bearish")
            confidence += 0.1

        reason = " | ".join(reasons) if reasons else "No significant technical signal"

        return max(-1.0, min(1.0, score)), min(1.0, confidence), reason

    def get_technical_levels(
        self, df: pd.DataFrame, current_price: float
    ) -> dict[str, Any]:
        """Get key technical levels.

        Args:
            df: OHLCV DataFrame
            current_price: Current price

        Returns:
            Technical levels dict
        """
        bb = self._calculate_bollinger(df)
        atr = self._calculate_atr(df)

        return {
            "current_price": current_price,
            "bb_upper": bb.upper,
            "bb_middle": bb.middle,
            "bb_lower": bb.lower,
            "atr": atr,
            "stop_loss_long": current_price - 2 * atr,
            "stop_loss_short": current_price + 2 * atr,
            "target_long": current_price + 3 * atr,
            "target_short": current_price - 3 * atr,
        }
