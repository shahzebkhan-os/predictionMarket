"""Signal attribution for postmortem analysis.

Analyzes signal contribution to entry/exit decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytz
import structlog

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class SignalContribution:
    """Contribution of a signal to trade outcome."""

    signal_name: str
    entry_value: float  # Signal value at entry
    exit_value: float | None  # Signal value at exit (if applicable)

    # Did signal correctly predict outcome?
    was_correct: bool = False

    # Contribution metrics
    weight: float = 0.0  # Weight in composite signal
    contribution_score: float = 0.0  # Weighted contribution

    # Statistics
    historical_accuracy: float = 0.0
    avg_pnl_when_positive: Decimal = Decimal("0")
    avg_pnl_when_negative: Decimal = Decimal("0")


@dataclass
class EntryAttributionResult:
    """Attribution analysis for trade entry."""

    trade_id: str
    entry_time: datetime

    # Overall entry quality
    entry_quality_score: float  # 0-100
    composite_signal_value: float

    # Individual signals
    signals: list[SignalContribution] = field(default_factory=list)

    # What would have happened with different entry
    optimal_entry_delay: int = 0  # Minutes to wait for better entry
    potential_improvement_pct: float = 0.0


@dataclass
class ExitAttributionResult:
    """Attribution analysis for trade exit."""

    trade_id: str
    exit_time: datetime
    exit_reason: str

    # Exit timing quality
    exit_quality_score: float  # 0-100

    # How much P&L was left on table
    pnl_left_on_table: Decimal = Decimal("0")
    optimal_exit_delay: int = 0

    # Signals at exit
    signals_at_exit: dict[str, float] = field(default_factory=dict)


class SignalAttributor:
    """Analyzes signal contribution to trade outcomes.

    Tracks:
    - Which signals contributed to entry
    - How accurate each signal was
    - Signal improvement opportunities
    """

    # Signal weights for composite
    DEFAULT_WEIGHTS = {
        "oi_analysis": 0.15,
        "iv_analysis": 0.15,
        "max_pain": 0.10,
        "vix": 0.10,
        "price_action": 0.10,
        "technicals": 0.10,
        "global_cues": 0.05,
        "fii_dii": 0.05,
        "straddle_pricing": 0.10,
        "news_events": 0.05,
        "greeks": 0.05,
    }

    def __init__(self) -> None:
        """Initialize attributor."""
        self._signal_history: dict[str, list[dict[str, Any]]] = {}
        self._attribution_results: list[EntryAttributionResult] = []

    def record_signals(
        self,
        trade_id: str,
        timestamp: datetime,
        signals: dict[str, float],
        pnl: Decimal | None = None,
    ) -> None:
        """Record signals for later attribution.

        Args:
            trade_id: Trade ID
            timestamp: Signal timestamp
            signals: Signal values
            pnl: P&L at this point (for exit analysis)
        """
        if trade_id not in self._signal_history:
            self._signal_history[trade_id] = []

        self._signal_history[trade_id].append({
            "timestamp": timestamp,
            "signals": signals.copy(),
            "pnl": pnl,
        })

    def attribute_entry(
        self,
        trade_id: str,
        entry_signals: dict[str, float],
        trade_pnl: Decimal,
        trade_was_winning: bool,
    ) -> EntryAttributionResult:
        """Attribute entry decision to signals.

        Args:
            trade_id: Trade ID
            entry_signals: Signal values at entry
            trade_pnl: Final trade P&L
            trade_was_winning: Whether trade was profitable

        Returns:
            EntryAttributionResult
        """
        contributions = []

        # Calculate composite signal
        composite = 0.0
        total_weight = 0.0

        for signal_name, signal_value in entry_signals.items():
            weight = self.DEFAULT_WEIGHTS.get(signal_name, 0.05)
            composite += signal_value * weight
            total_weight += weight

            # Was signal correct?
            # Positive signal + winning trade = correct
            # Negative signal + losing trade = correct
            was_correct = (
                (signal_value > 0 and trade_was_winning) or
                (signal_value < 0 and not trade_was_winning)
            )

            contribution = SignalContribution(
                signal_name=signal_name,
                entry_value=signal_value,
                was_correct=was_correct,
                weight=weight,
                contribution_score=signal_value * weight,
            )
            contributions.append(contribution)

        # Normalize composite
        if total_weight > 0:
            composite /= total_weight

        # Calculate entry quality
        entry_quality = self._calculate_entry_quality(
            composite, trade_pnl, trade_was_winning
        )

        result = EntryAttributionResult(
            trade_id=trade_id,
            entry_time=datetime.now(IST),
            entry_quality_score=entry_quality,
            composite_signal_value=composite,
            signals=contributions,
        )

        self._attribution_results.append(result)

        return result

    def attribute_exit(
        self,
        trade_id: str,
        exit_reason: str,
        exit_signals: dict[str, float],
        final_pnl: Decimal,
        peak_pnl: Decimal,
    ) -> ExitAttributionResult:
        """Attribute exit decision.

        Args:
            trade_id: Trade ID
            exit_reason: Exit reason
            exit_signals: Signal values at exit
            final_pnl: Final P&L
            peak_pnl: Peak P&L during trade

        Returns:
            ExitAttributionResult
        """
        # P&L left on table
        pnl_left = peak_pnl - final_pnl if peak_pnl > final_pnl else Decimal("0")

        # Exit quality
        exit_quality = self._calculate_exit_quality(
            final_pnl, peak_pnl, exit_reason
        )

        return ExitAttributionResult(
            trade_id=trade_id,
            exit_time=datetime.now(IST),
            exit_reason=exit_reason,
            exit_quality_score=exit_quality,
            pnl_left_on_table=pnl_left,
            signals_at_exit=exit_signals,
        )

    def _calculate_entry_quality(
        self,
        composite_signal: float,
        pnl: Decimal,
        was_winning: bool,
    ) -> float:
        """Calculate entry quality score.

        Args:
            composite_signal: Composite signal value
            pnl: Trade P&L
            was_winning: Whether trade was profitable

        Returns:
            Quality score 0-100
        """
        score = 50.0  # Baseline

        # Strong signal + winning = good entry
        if abs(composite_signal) > 0.5 and was_winning:
            score += 30

        # Strong signal + losing = bad entry
        elif abs(composite_signal) > 0.5 and not was_winning:
            score -= 20

        # Weak signal entry
        elif abs(composite_signal) < 0.3:
            score -= 10  # Penalty for weak signal entries

        # P&L magnitude adjustment
        if was_winning:
            score += min(20, float(pnl) / 1000)  # +20 max for big wins
        else:
            score += max(-20, float(pnl) / 1000)  # -20 max for big losses

        return max(0, min(100, score))

    def _calculate_exit_quality(
        self,
        final_pnl: Decimal,
        peak_pnl: Decimal,
        exit_reason: str,
    ) -> float:
        """Calculate exit quality score.

        Args:
            final_pnl: Final P&L
            peak_pnl: Peak P&L
            exit_reason: Exit reason

        Returns:
            Quality score 0-100
        """
        score = 50.0

        # Captured most of peak
        if peak_pnl > 0:
            capture_ratio = float(final_pnl / peak_pnl) if peak_pnl != 0 else 0
            score += (capture_ratio - 0.5) * 40  # +/-20 based on capture

        # Reason-based adjustment
        good_exits = {"TARGET_HIT", "TRAILING_STOP", "THETA_TARGET"}
        bad_exits = {"STOP_LOSS", "MAX_LOSS_HIT", "KILL_SWITCH"}

        if exit_reason in good_exits:
            score += 15
        elif exit_reason in bad_exits:
            score -= 10

        return max(0, min(100, score))

    def get_signal_leaderboard(self) -> list[dict[str, Any]]:
        """Get signal performance leaderboard.

        Returns:
            Sorted list of signal performance
        """
        signal_stats: dict[str, dict[str, Any]] = {}

        for result in self._attribution_results:
            for contrib in result.signals:
                if contrib.signal_name not in signal_stats:
                    signal_stats[contrib.signal_name] = {
                        "name": contrib.signal_name,
                        "count": 0,
                        "correct": 0,
                        "total_contribution": 0.0,
                    }

                stats = signal_stats[contrib.signal_name]
                stats["count"] += 1
                if contrib.was_correct:
                    stats["correct"] += 1
                stats["total_contribution"] += contrib.contribution_score

        # Calculate accuracy and sort
        leaderboard = []
        for name, stats in signal_stats.items():
            accuracy = stats["correct"] / stats["count"] * 100 if stats["count"] > 0 else 0
            avg_contribution = stats["total_contribution"] / stats["count"] if stats["count"] > 0 else 0

            leaderboard.append({
                "signal": name,
                "accuracy": accuracy,
                "avg_contribution": avg_contribution,
                "sample_size": stats["count"],
            })

        return sorted(leaderboard, key=lambda x: x["accuracy"], reverse=True)

    def get_attribution_summary(self) -> dict[str, Any]:
        """Get attribution summary.

        Returns:
            Summary dict
        """
        if not self._attribution_results:
            return {"total_trades": 0}

        avg_entry_quality = sum(r.entry_quality_score for r in self._attribution_results) / len(self._attribution_results)

        # Count correct signals
        total_signals = 0
        correct_signals = 0
        for result in self._attribution_results:
            for contrib in result.signals:
                total_signals += 1
                if contrib.was_correct:
                    correct_signals += 1

        return {
            "total_trades_analyzed": len(self._attribution_results),
            "avg_entry_quality": avg_entry_quality,
            "total_signal_evaluations": total_signals,
            "correct_signal_evaluations": correct_signals,
            "overall_signal_accuracy": correct_signals / total_signals * 100 if total_signals > 0 else 0,
            "signal_leaderboard": self.get_signal_leaderboard()[:5],  # Top 5
        }
