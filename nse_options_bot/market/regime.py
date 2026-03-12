"""Market regime detector.

Classifies market into:
- TRENDING_UP: price>20EMA, supertrend bullish, NIFTY>prev day high after 10:30
- TRENDING_DOWN: price<20EMA, supertrend bearish
- RANGE_BOUND: oscillating within ±0.3% of VWAP, positive GEX
- HIGH_VOLATILITY: VIX>18 OR VIX spike>+10% intraday, negative GEX

Size multipliers: RANGE_BOUND=1.0, TRENDING=0.7, HIGH_VOLATILITY=0.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
import pytz
import structlog

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class MarketRegime(str, Enum):
    """Market regime classification."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeSignals:
    """Individual regime signals."""

    # Trend signals
    price_above_ema20: bool = False
    supertrend_bullish: bool = False
    above_prev_high: bool = False
    below_prev_low: bool = False

    # Range signals
    vwap_deviation_pct: float = 0.0
    oscillating_around_vwap: bool = False

    # Volatility signals
    vix_value: float = 0.0
    vix_spike_pct: float = 0.0
    gex_value: float = 0.0
    gex_positive: bool = True

    # Market timing
    after_initial_hour: bool = False  # After 10:30 IST


@dataclass
class RegimeResult:
    """Market regime detection result."""

    regime: MarketRegime
    confidence: float  # 0-1
    signals: RegimeSignals
    size_multiplier: float
    recommended_strategies: list[str] = field(default_factory=list)
    avoid_strategies: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(IST))


class SupertrendCalculator:
    """Supertrend indicator calculator."""

    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        """Initialize calculator.

        Args:
            period: ATR period
            multiplier: ATR multiplier
        """
        self.period = period
        self.multiplier = multiplier

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate Supertrend.

        Args:
            df: DataFrame with 'high', 'low', 'close' columns

        Returns:
            DataFrame with supertrend columns
        """
        df = df.copy()

        # Calculate ATR
        df["tr1"] = df["high"] - df["low"]
        df["tr2"] = abs(df["high"] - df["close"].shift())
        df["tr3"] = abs(df["low"] - df["close"].shift())
        df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
        df["atr"] = df["tr"].rolling(window=self.period).mean()

        # Calculate basic bands
        hl2 = (df["high"] + df["low"]) / 2
        df["basic_upper"] = hl2 + self.multiplier * df["atr"]
        df["basic_lower"] = hl2 - self.multiplier * df["atr"]

        # Initialize final bands
        df["final_upper"] = df["basic_upper"]
        df["final_lower"] = df["basic_lower"]

        # Calculate final bands with proper logic
        for i in range(1, len(df)):
            # Final upper band
            if (
                df["basic_upper"].iloc[i] < df["final_upper"].iloc[i - 1]
                or df["close"].iloc[i - 1] > df["final_upper"].iloc[i - 1]
            ):
                df.loc[df.index[i], "final_upper"] = df["basic_upper"].iloc[i]
            else:
                df.loc[df.index[i], "final_upper"] = df["final_upper"].iloc[i - 1]

            # Final lower band
            if (
                df["basic_lower"].iloc[i] > df["final_lower"].iloc[i - 1]
                or df["close"].iloc[i - 1] < df["final_lower"].iloc[i - 1]
            ):
                df.loc[df.index[i], "final_lower"] = df["basic_lower"].iloc[i]
            else:
                df.loc[df.index[i], "final_lower"] = df["final_lower"].iloc[i - 1]

        # Calculate Supertrend
        df["supertrend"] = np.nan
        df["supertrend_direction"] = 0  # 1 = bullish, -1 = bearish

        for i in range(1, len(df)):
            if df["close"].iloc[i] > df["final_upper"].iloc[i - 1]:
                df.loc[df.index[i], "supertrend"] = df["final_lower"].iloc[i]
                df.loc[df.index[i], "supertrend_direction"] = 1
            elif df["close"].iloc[i] < df["final_lower"].iloc[i - 1]:
                df.loc[df.index[i], "supertrend"] = df["final_upper"].iloc[i]
                df.loc[df.index[i], "supertrend_direction"] = -1
            else:
                if df["supertrend_direction"].iloc[i - 1] == 1:
                    df.loc[df.index[i], "supertrend"] = df["final_lower"].iloc[i]
                    df.loc[df.index[i], "supertrend_direction"] = 1
                else:
                    df.loc[df.index[i], "supertrend"] = df["final_upper"].iloc[i]
                    df.loc[df.index[i], "supertrend_direction"] = -1

        return df


class RegimeDetector:
    """Market regime detector.

    Analyzes market conditions every 15 minutes.
    """

    # Regime thresholds
    VWAP_RANGE_THRESHOLD = 0.003  # ±0.3% of VWAP
    VIX_HIGH_THRESHOLD = 18.0
    VIX_SPIKE_THRESHOLD = 0.10  # 10% intraday spike
    EMA_PERIOD = 20
    INITIAL_HOUR_END = time(10, 30)

    # Size multipliers
    SIZE_MULTIPLIERS = {
        MarketRegime.TRENDING_UP: 0.7,
        MarketRegime.TRENDING_DOWN: 0.7,
        MarketRegime.RANGE_BOUND: 1.0,
        MarketRegime.HIGH_VOLATILITY: 0.4,
        MarketRegime.UNKNOWN: 0.5,
    }

    # Strategy recommendations
    STRATEGY_MAP = {
        MarketRegime.TRENDING_UP: {
            "recommended": ["bull_call_spread", "sell_otm_pe"],
            "avoid": ["short_straddle", "iron_condor", "bear_put_spread"],
        },
        MarketRegime.TRENDING_DOWN: {
            "recommended": ["bear_put_spread", "sell_otm_ce"],
            "avoid": ["short_straddle", "iron_condor", "bull_call_spread"],
        },
        MarketRegime.RANGE_BOUND: {
            "recommended": ["iron_condor", "short_straddle"],
            "avoid": ["long_straddle", "directional_spreads"],
        },
        MarketRegime.HIGH_VOLATILITY: {
            "recommended": ["long_straddle"],
            "avoid": ["short_straddle", "iron_condor", "naked_options"],
        },
    }

    def __init__(self) -> None:
        """Initialize regime detector."""
        self._supertrend_calc = SupertrendCalculator()
        self._last_regime: RegimeResult | None = None
        self._vix_open: float | None = None

    def detect(
        self,
        price_data: pd.DataFrame,
        spot_price: Decimal,
        prev_day_high: Decimal,
        prev_day_low: Decimal,
        vix: float,
        gex: float,
        vwap: Decimal | None = None,
    ) -> RegimeResult:
        """Detect current market regime.

        Args:
            price_data: OHLC DataFrame with 'open', 'high', 'low', 'close', 'volume'
            spot_price: Current spot price
            prev_day_high: Previous day high
            prev_day_low: Previous day low
            vix: India VIX value
            gex: Gamma Exposure value
            vwap: VWAP (calculated if not provided)

        Returns:
            RegimeResult
        """
        signals = self._calculate_signals(
            price_data, spot_price, prev_day_high, prev_day_low, vix, gex, vwap
        )

        # Determine regime
        regime, confidence = self._classify_regime(signals)

        result = RegimeResult(
            regime=regime,
            confidence=confidence,
            signals=signals,
            size_multiplier=self.SIZE_MULTIPLIERS[regime],
            recommended_strategies=self.STRATEGY_MAP.get(regime, {}).get(
                "recommended", []
            ),
            avoid_strategies=self.STRATEGY_MAP.get(regime, {}).get("avoid", []),
        )

        self._last_regime = result

        logger.info(
            "regime_detected",
            regime=regime.value,
            confidence=confidence,
            size_multiplier=result.size_multiplier,
        )

        return result

    def _calculate_signals(
        self,
        price_data: pd.DataFrame,
        spot_price: Decimal,
        prev_day_high: Decimal,
        prev_day_low: Decimal,
        vix: float,
        gex: float,
        vwap: Decimal | None = None,
    ) -> RegimeSignals:
        """Calculate regime signals.

        Args:
            price_data: OHLC DataFrame
            spot_price: Current spot price
            prev_day_high: Previous day high
            prev_day_low: Previous day low
            vix: India VIX
            gex: Gamma Exposure
            vwap: VWAP

        Returns:
            RegimeSignals
        """
        signals = RegimeSignals()
        spot = float(spot_price)

        # Calculate EMA20
        if len(price_data) >= self.EMA_PERIOD:
            ema20 = price_data["close"].ewm(span=self.EMA_PERIOD).mean().iloc[-1]
            signals.price_above_ema20 = spot > ema20

        # Calculate Supertrend
        if len(price_data) >= 15:
            st_df = self._supertrend_calc.calculate(price_data)
            if len(st_df) > 0 and not pd.isna(st_df["supertrend_direction"].iloc[-1]):
                signals.supertrend_bullish = st_df["supertrend_direction"].iloc[-1] == 1

        # Check previous day levels
        signals.above_prev_high = spot > float(prev_day_high)
        signals.below_prev_low = spot < float(prev_day_low)

        # Check time
        now = datetime.now(IST).time()
        signals.after_initial_hour = now >= self.INITIAL_HOUR_END

        # Calculate VWAP if not provided
        if vwap is None and "volume" in price_data.columns:
            typical_price = (
                price_data["high"] + price_data["low"] + price_data["close"]
            ) / 3
            vwap = Decimal(
                str(
                    (typical_price * price_data["volume"]).sum()
                    / price_data["volume"].sum()
                )
            )

        # Calculate VWAP deviation
        if vwap:
            signals.vwap_deviation_pct = abs(spot - float(vwap)) / float(vwap)
            signals.oscillating_around_vwap = (
                signals.vwap_deviation_pct <= self.VWAP_RANGE_THRESHOLD
            )

        # VIX signals
        signals.vix_value = vix

        # Track VIX spike
        if self._vix_open is None:
            self._vix_open = vix
        signals.vix_spike_pct = (vix - self._vix_open) / self._vix_open if self._vix_open > 0 else 0

        # GEX signals
        signals.gex_value = gex
        signals.gex_positive = gex > 0

        return signals

    def _classify_regime(self, signals: RegimeSignals) -> tuple[MarketRegime, float]:
        """Classify market regime from signals.

        Args:
            signals: Calculated regime signals

        Returns:
            Tuple of (regime, confidence)
        """
        # Check HIGH_VOLATILITY first (takes precedence)
        if (
            signals.vix_value >= self.VIX_HIGH_THRESHOLD
            or signals.vix_spike_pct >= self.VIX_SPIKE_THRESHOLD
        ):
            if not signals.gex_positive:
                confidence = min(
                    0.9,
                    0.5 + (signals.vix_value / 25) * 0.2 + (1 - signals.gex_positive) * 0.2,
                )
                return MarketRegime.HIGH_VOLATILITY, confidence

        # Check TRENDING_UP
        trending_up_score = 0
        if signals.price_above_ema20:
            trending_up_score += 1
        if signals.supertrend_bullish:
            trending_up_score += 1
        if signals.above_prev_high and signals.after_initial_hour:
            trending_up_score += 1

        if trending_up_score >= 2:
            confidence = trending_up_score / 3
            return MarketRegime.TRENDING_UP, confidence

        # Check TRENDING_DOWN
        trending_down_score = 0
        if not signals.price_above_ema20:
            trending_down_score += 1
        if not signals.supertrend_bullish:
            trending_down_score += 1
        if signals.below_prev_low and signals.after_initial_hour:
            trending_down_score += 1

        if trending_down_score >= 2:
            confidence = trending_down_score / 3
            return MarketRegime.TRENDING_DOWN, confidence

        # Check RANGE_BOUND
        if signals.oscillating_around_vwap and signals.gex_positive:
            confidence = 0.6 + (1 - signals.vwap_deviation_pct / self.VWAP_RANGE_THRESHOLD) * 0.3
            return MarketRegime.RANGE_BOUND, confidence

        # Default to UNKNOWN
        return MarketRegime.UNKNOWN, 0.3

    def reset_daily(self) -> None:
        """Reset daily tracking (call at market open)."""
        self._vix_open = None
        self._last_regime = None

    @property
    def last_regime(self) -> RegimeResult | None:
        """Get last detected regime."""
        return self._last_regime

    def get_regime_summary(self) -> dict[str, Any]:
        """Get regime summary.

        Returns:
            Summary dict
        """
        if not self._last_regime:
            return {
                "regime": MarketRegime.UNKNOWN.value,
                "confidence": 0.0,
                "size_multiplier": 0.5,
            }

        return {
            "regime": self._last_regime.regime.value,
            "confidence": self._last_regime.confidence,
            "size_multiplier": self._last_regime.size_multiplier,
            "recommended": self._last_regime.recommended_strategies,
            "avoid": self._last_regime.avoid_strategies,
            "signals": {
                "price_above_ema20": self._last_regime.signals.price_above_ema20,
                "supertrend_bullish": self._last_regime.signals.supertrend_bullish,
                "vwap_deviation_pct": self._last_regime.signals.vwap_deviation_pct,
                "vix": self._last_regime.signals.vix_value,
                "gex": self._last_regime.signals.gex_value,
            },
            "timestamp": self._last_regime.timestamp.isoformat(),
        }
