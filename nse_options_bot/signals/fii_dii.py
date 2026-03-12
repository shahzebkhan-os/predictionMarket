"""FII/DII Flow Signal (Signal 8).

Source: https://www.nseindia.com/api/fiidiiTradeReact
FII net futures long >+5000cr → bullish.
Consecutive 3-day sell >-15000cr cumulative → bearish regime.
FII index PE net sell → bullish confirmation.
1-day lag — use as medium-term bias only.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import aiohttp
import pytz
import structlog

from nse_options_bot.signals.engine import Signal, SignalType, create_signal

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class FIIDIIData:
    """FII/DII flow data for a single day."""

    trade_date: date

    # Cash segment (in crores)
    fii_buy_cash: Decimal = Decimal("0")
    fii_sell_cash: Decimal = Decimal("0")
    fii_net_cash: Decimal = Decimal("0")
    dii_buy_cash: Decimal = Decimal("0")
    dii_sell_cash: Decimal = Decimal("0")
    dii_net_cash: Decimal = Decimal("0")

    # F&O segment (in crores)
    fii_index_futures_long: Decimal = Decimal("0")
    fii_index_futures_short: Decimal = Decimal("0")
    fii_index_options_call_long: Decimal = Decimal("0")
    fii_index_options_put_long: Decimal = Decimal("0")
    fii_index_options_call_short: Decimal = Decimal("0")
    fii_index_options_put_short: Decimal = Decimal("0")

    @property
    def fii_net_futures(self) -> Decimal:
        """Net FII futures position."""
        return self.fii_index_futures_long - self.fii_index_futures_short

    @property
    def fii_options_net_call(self) -> Decimal:
        """Net FII call options."""
        return self.fii_index_options_call_long - self.fii_index_options_call_short

    @property
    def fii_options_net_put(self) -> Decimal:
        """Net FII put options."""
        return self.fii_index_options_put_long - self.fii_index_options_put_short


@dataclass
class FIIDIIAnalysis:
    """FII/DII analysis result."""

    fii_stance: str  # "bullish", "bearish", "neutral"
    dii_stance: str
    consecutive_sell_days: int
    cumulative_3day_fii: Decimal
    fii_futures_bias: str  # "long", "short", "neutral"
    fii_options_bias: str


class FIIDIIAnalyzer:
    """FII/DII flow analyzer.

    Analyzes FII and DII flows for medium-term bias.
    Data has 1-day lag - use for bias, not immediate signals.
    """

    NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

    # Thresholds (in crores)
    FII_FUTURES_BULLISH_THRESHOLD = Decimal("5000")  # +5000cr long = bullish
    FII_FUTURES_BEARISH_THRESHOLD = Decimal("-5000")
    FII_CASH_SIGNIFICANT = Decimal("2000")  # +/-2000cr is significant
    CUMULATIVE_BEARISH_THRESHOLD = Decimal("-15000")  # 3-day -15000cr = bearish

    def __init__(self) -> None:
        """Initialize analyzer."""
        self._history: deque[FIIDIIData] = deque(maxlen=30)  # 30 days history
        self._last_fetch: datetime | None = None

    async def fetch_data(self) -> FIIDIIData | None:
        """Fetch FII/DII data from NSE.

        Should be called after 18:00 IST when data is published.

        Returns:
            FIIDIIData or None if fetch fails
        """
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }

        try:
            async with aiohttp.ClientSession() as session:
                # First get cookies by visiting main page
                async with session.get(
                    "https://www.nseindia.com/", headers=headers
                ) as _:
                    pass

                # Then fetch FII/DII data
                async with session.get(
                    self.NSE_FII_DII_URL, headers=headers
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            "fii_dii_fetch_failed",
                            status=response.status,
                        )
                        return None

                    data = await response.json()
                    return self._parse_response(data)

        except Exception as e:
            logger.error("fii_dii_fetch_error", error=str(e))
            return None

    def _parse_response(self, data: dict[str, Any]) -> FIIDIIData:
        """Parse NSE API response.

        Args:
            data: API response

        Returns:
            FIIDIIData
        """
        # This is a simplified parser - actual NSE response format may vary
        fii_dii = FIIDIIData(trade_date=date.today())

        try:
            # Cash segment
            if "data" in data:
                for item in data["data"]:
                    category = item.get("category", "")
                    if "FII" in category or "FPI" in category:
                        fii_dii.fii_buy_cash = Decimal(str(item.get("buyValue", 0)))
                        fii_dii.fii_sell_cash = Decimal(str(item.get("sellValue", 0)))
                        fii_dii.fii_net_cash = Decimal(str(item.get("netValue", 0)))
                    elif "DII" in category:
                        fii_dii.dii_buy_cash = Decimal(str(item.get("buyValue", 0)))
                        fii_dii.dii_sell_cash = Decimal(str(item.get("sellValue", 0)))
                        fii_dii.dii_net_cash = Decimal(str(item.get("netValue", 0)))
        except (KeyError, ValueError) as e:
            logger.warning("fii_dii_parse_error", error=str(e))

        return fii_dii

    def add_data(self, data: FIIDIIData) -> None:
        """Add FII/DII data to history.

        Args:
            data: FII/DII data
        """
        self._history.append(data)
        self._last_fetch = datetime.now(IST)

    def set_data(
        self,
        fii_net_cash: Decimal,
        dii_net_cash: Decimal,
        fii_net_futures: Decimal | None = None,
        trade_date: date | None = None,
    ) -> None:
        """Manually set FII/DII data.

        Args:
            fii_net_cash: FII net cash flow
            dii_net_cash: DII net cash flow
            fii_net_futures: FII net futures position
            trade_date: Trade date
        """
        data = FIIDIIData(trade_date=trade_date or date.today())
        data.fii_net_cash = fii_net_cash
        data.dii_net_cash = dii_net_cash
        if fii_net_futures:
            if fii_net_futures > 0:
                data.fii_index_futures_long = fii_net_futures
            else:
                data.fii_index_futures_short = abs(fii_net_futures)

        self.add_data(data)

    def analyze(self) -> Signal:
        """Analyze FII/DII flows.

        Returns:
            FII/DII signal
        """
        if not self._history:
            return create_signal(
                signal_type=SignalType.FII_DII,
                score=0.0,
                confidence=0.0,
                reason="No FII/DII data available",
            )

        latest = self._history[-1]
        analysis = self._analyze_flows()

        # Calculate score
        score, confidence, reason = self._calculate_score(latest, analysis)

        return create_signal(
            signal_type=SignalType.FII_DII,
            score=score,
            confidence=confidence,
            reason=reason,
            components={
                "fii_net_cash": float(latest.fii_net_cash),
                "dii_net_cash": float(latest.dii_net_cash),
                "fii_net_futures": float(latest.fii_net_futures),
                "consecutive_sell_days": analysis.consecutive_sell_days,
                "cumulative_3day_fii": float(analysis.cumulative_3day_fii),
                "fii_stance": analysis.fii_stance,
                "fii_futures_bias": analysis.fii_futures_bias,
            },
        )

    def _analyze_flows(self) -> FIIDIIAnalysis:
        """Analyze FII/DII flow patterns.

        Returns:
            FIIDIIAnalysis
        """
        if not self._history:
            return FIIDIIAnalysis(
                fii_stance="neutral",
                dii_stance="neutral",
                consecutive_sell_days=0,
                cumulative_3day_fii=Decimal("0"),
                fii_futures_bias="neutral",
                fii_options_bias="neutral",
            )

        latest = self._history[-1]

        # Count consecutive FII sell days
        consecutive_sell = 0
        for data in reversed(list(self._history)):
            if data.fii_net_cash < 0:
                consecutive_sell += 1
            else:
                break

        # Calculate 3-day cumulative
        recent_3 = list(self._history)[-3:]
        cumulative_3day = sum(d.fii_net_cash for d in recent_3)

        # Determine FII stance
        if cumulative_3day >= self.FII_CASH_SIGNIFICANT:
            fii_stance = "bullish"
        elif cumulative_3day <= -self.FII_CASH_SIGNIFICANT:
            fii_stance = "bearish"
        else:
            fii_stance = "neutral"

        # DII stance
        if latest.dii_net_cash >= self.FII_CASH_SIGNIFICANT:
            dii_stance = "bullish"
        elif latest.dii_net_cash <= -self.FII_CASH_SIGNIFICANT:
            dii_stance = "bearish"
        else:
            dii_stance = "neutral"

        # FII futures bias
        fii_futures = latest.fii_net_futures
        if fii_futures >= self.FII_FUTURES_BULLISH_THRESHOLD:
            futures_bias = "long"
        elif fii_futures <= self.FII_FUTURES_BEARISH_THRESHOLD:
            futures_bias = "short"
        else:
            futures_bias = "neutral"

        # FII options bias (PE sell = bullish)
        if latest.fii_options_net_put < -Decimal("500"):  # Selling PEs
            options_bias = "bullish"
        elif latest.fii_options_net_call < -Decimal("500"):  # Selling CEs
            options_bias = "bearish"
        else:
            options_bias = "neutral"

        return FIIDIIAnalysis(
            fii_stance=fii_stance,
            dii_stance=dii_stance,
            consecutive_sell_days=consecutive_sell,
            cumulative_3day_fii=cumulative_3day,
            fii_futures_bias=futures_bias,
            fii_options_bias=options_bias,
        )

    def _calculate_score(
        self, latest: FIIDIIData, analysis: FIIDIIAnalysis
    ) -> tuple[float, float, str]:
        """Calculate FII/DII score.

        Args:
            latest: Latest FII/DII data
            analysis: Analysis result

        Returns:
            Tuple of (score, confidence, reason)
        """
        score = 0.0
        reasons = []
        confidence = 0.5  # Medium-term bias, lower confidence

        # FII cash flow (major factor)
        if analysis.fii_stance == "bullish":
            score += 0.3
            reasons.append(f"FII net buyer ({float(analysis.cumulative_3day_fii):.0f}cr 3-day)")
        elif analysis.fii_stance == "bearish":
            score -= 0.3
            reasons.append(f"FII net seller ({float(analysis.cumulative_3day_fii):.0f}cr 3-day)")

        # Consecutive sell days
        if analysis.consecutive_sell_days >= 3:
            if analysis.cumulative_3day_fii <= self.CUMULATIVE_BEARISH_THRESHOLD:
                score -= 0.3
                reasons.append(
                    f"{analysis.consecutive_sell_days} consecutive FII sell days → Bearish regime"
                )
                confidence += 0.1

        # FII futures position
        if analysis.fii_futures_bias == "long":
            score += 0.2
            reasons.append(f"FII futures net long ({float(latest.fii_net_futures):.0f}cr)")
            confidence += 0.1
        elif analysis.fii_futures_bias == "short":
            score -= 0.2
            reasons.append(f"FII futures net short ({float(latest.fii_net_futures):.0f}cr)")
            confidence += 0.1

        # FII options bias (PE sell = bullish confirmation)
        if analysis.fii_options_bias == "bullish":
            score += 0.1
            reasons.append("FII PE sellers → Bullish confirmation")
        elif analysis.fii_options_bias == "bearish":
            score -= 0.1
            reasons.append("FII CE sellers → Bearish confirmation")

        # DII counterbalance
        if analysis.dii_stance == "bullish" and analysis.fii_stance == "bearish":
            score += 0.1
            reasons.append("DII buying on FII selling")
        elif analysis.dii_stance == "bearish" and analysis.fii_stance == "bullish":
            score -= 0.1
            reasons.append("DII selling on FII buying")

        if not reasons:
            reasons.append("FII/DII flows neutral")

        reason = " | ".join(reasons)

        return max(-1.0, min(1.0, score)), min(0.7, confidence), reason  # Cap confidence at 0.7 due to lag

    def get_summary(self) -> dict[str, Any]:
        """Get FII/DII summary.

        Returns:
            Summary dict
        """
        if not self._history:
            return {"status": "No data available"}

        latest = self._history[-1]
        analysis = self._analyze_flows()

        return {
            "trade_date": latest.trade_date.isoformat(),
            "fii_net_cash": float(latest.fii_net_cash),
            "dii_net_cash": float(latest.dii_net_cash),
            "fii_net_futures": float(latest.fii_net_futures),
            "fii_stance": analysis.fii_stance,
            "dii_stance": analysis.dii_stance,
            "consecutive_sell_days": analysis.consecutive_sell_days,
            "cumulative_3day": float(analysis.cumulative_3day_fii),
            "history_days": len(self._history),
        }
