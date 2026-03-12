"""
Postmortem Engine.

Analyzes completed trades for performance attribution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Literal

from zoneinfo import ZoneInfo

from nse_advisor.tracker.state import ManualTrade

logger = logging.getLogger(__name__)


@dataclass
class TradePostmortem:
    """Postmortem analysis of a completed trade."""
    trade_id: str
    strategy_name: str
    underlying: str
    entry_time: datetime
    exit_time: datetime
    
    # P&L
    realized_pnl_inr: float
    max_adverse_excursion: float
    max_favorable_excursion: float
    
    # Exit quality
    exit_quality_score: float  # 0-1
    
    # Signal accuracy
    linked_recommendation_accuracy: float | None
    signal_accuracy: dict[str, float]  # Per-signal accuracy
    
    # Greeks P&L attribution
    delta_pnl: float
    theta_pnl: float
    vega_pnl: float
    gamma_pnl: float
    residual_pnl: float
    
    # Verdict
    verdict: str
    verdict_reason: str


@dataclass
class NightlyReport:
    """Nightly postmortem report."""
    report_date: date
    lookback_days: int
    
    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    
    # P&L
    total_pnl: float
    avg_pnl_per_trade: float
    best_trade_pnl: float
    worst_trade_pnl: float
    
    # Strategy breakdown
    strategy_stats: dict[str, dict]
    
    # Signal accuracy
    signal_accuracy: dict[str, dict]
    
    # Greeks attribution
    total_delta_pnl: float
    total_theta_pnl: float
    total_vega_pnl: float
    total_gamma_pnl: float
    
    # Regime performance
    regime_stats: dict[str, dict]
    
    # IV timing
    iv_timing_score: float
    
    # Paper vs actual
    paper_pnl: float
    actual_pnl: float
    
    # Recommendations
    recommendations: list[str]


class PostmortemEngine:
    """
    Analyzes completed trades for performance attribution.
    
    Features:
    - Per-trade P&L attribution (delta, theta, vega, gamma, residual)
    - Signal accuracy tracking
    - Exit quality scoring
    - Verdict classification
    """
    
    VERDICTS = {
        "GOOD_TRADE": "Trade executed well with positive outcome",
        "GOOD_IDEA_BAD_EXIT": "Signal was correct but exit timing poor",
        "BAD_IV_TIMING": "IV moved against position",
        "WRONG_REGIME": "Market regime changed unexpectedly",
        "UNLUCKY_EVENT": "Unexpected event caused adverse move",
        "BAD_SIZING": "Position size was inappropriate",
        "SIGNAL_FAILURE": "Signal generated incorrect prediction",
        "USER_OVERRIDE": "User ignored signal recommendation",
    }
    
    def __init__(self) -> None:
        """Initialize postmortem engine."""
        self._ist = ZoneInfo("Asia/Kolkata")
        
        # Tracking
        self._trade_mae: dict[str, float] = {}  # Max adverse excursion
        self._trade_mfe: dict[str, float] = {}  # Max favorable excursion
    
    def track_excursion(self, trade_id: str, pnl: float) -> None:
        """Track running P&L for MAE/MFE calculation."""
        # Update MAE
        current_mae = self._trade_mae.get(trade_id, 0)
        if pnl < current_mae:
            self._trade_mae[trade_id] = pnl
        
        # Update MFE
        current_mfe = self._trade_mfe.get(trade_id, 0)
        if pnl > current_mfe:
            self._trade_mfe[trade_id] = pnl
    
    def analyze_trade(self, trade: ManualTrade) -> TradePostmortem:
        """
        Analyze a completed trade.
        
        Args:
            trade: Completed trade to analyze
            
        Returns:
            TradePostmortem with full analysis
        """
        if trade.is_open:
            raise ValueError("Cannot analyze open trade")
        
        realized_pnl = trade.realized_pnl or 0
        
        # Get MAE/MFE
        mae = self._trade_mae.get(trade.trade_id, 0)
        mfe = self._trade_mfe.get(trade.trade_id, 0)
        
        # Calculate exit quality
        exit_quality = self._calculate_exit_quality(realized_pnl, mfe, mae)
        
        # Calculate Greeks P&L attribution
        greeks_pnl = self._attribute_greeks_pnl(trade)
        
        # Evaluate signal accuracy
        signal_accuracy = self._evaluate_signal_accuracy(trade)
        
        # Determine verdict
        verdict, verdict_reason = self._determine_verdict(
            trade, realized_pnl, exit_quality, signal_accuracy
        )
        
        # Cleanup tracking
        self._trade_mae.pop(trade.trade_id, None)
        self._trade_mfe.pop(trade.trade_id, None)
        
        return TradePostmortem(
            trade_id=trade.trade_id,
            strategy_name=trade.strategy_name,
            underlying=trade.underlying,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time or datetime.now(self._ist),
            realized_pnl_inr=realized_pnl,
            max_adverse_excursion=mae,
            max_favorable_excursion=mfe,
            exit_quality_score=exit_quality,
            linked_recommendation_accuracy=signal_accuracy.get("composite"),
            signal_accuracy=signal_accuracy,
            delta_pnl=greeks_pnl["delta"],
            theta_pnl=greeks_pnl["theta"],
            vega_pnl=greeks_pnl["vega"],
            gamma_pnl=greeks_pnl["gamma"],
            residual_pnl=greeks_pnl["residual"],
            verdict=verdict,
            verdict_reason=verdict_reason,
        )
    
    def _calculate_exit_quality(
        self,
        realized_pnl: float,
        mfe: float,
        mae: float
    ) -> float:
        """
        Calculate exit quality score (0-1).
        
        Compares actual exit to ideal hindsight exit.
        """
        if mfe <= 0:
            return 0.5  # No profitable opportunity
        
        # How much of the max favorable move was captured
        capture_ratio = realized_pnl / mfe if mfe > 0 else 0
        
        # Clamp to 0-1
        return max(0.0, min(1.0, capture_ratio))
    
    def _attribute_greeks_pnl(self, trade: ManualTrade) -> dict[str, float]:
        """
        Attribute P&L to Greeks.
        
        This is a simplified attribution - real implementation would need
        historical data for proper decomposition.
        """
        realized_pnl = trade.realized_pnl or 0
        
        # Estimate based on entry/exit Greeks difference
        # This is simplified - real attribution needs tick data
        
        # For now, distribute based on typical contributions
        # Theta: time decay (main contributor for short premium)
        # Delta: directional move
        # Vega: IV change
        # Gamma: convexity
        
        # Simplified heuristics
        theta_contrib = 0.4 if realized_pnl > 0 else 0.2
        delta_contrib = 0.3
        vega_contrib = 0.2
        gamma_contrib = 0.1
        
        theta_pnl = realized_pnl * theta_contrib
        delta_pnl = realized_pnl * delta_contrib
        vega_pnl = realized_pnl * vega_contrib
        gamma_pnl = realized_pnl * gamma_contrib
        
        # Residual ensures sum equals total
        residual = realized_pnl - (theta_pnl + delta_pnl + vega_pnl + gamma_pnl)
        
        return {
            "delta": delta_pnl,
            "theta": theta_pnl,
            "vega": vega_pnl,
            "gamma": gamma_pnl,
            "residual": residual,
        }
    
    def _evaluate_signal_accuracy(self, trade: ManualTrade) -> dict[str, float]:
        """
        Evaluate accuracy of signals that generated this trade.
        
        Returns accuracy scores (0-1) per signal.
        """
        accuracy = {}
        realized_pnl = trade.realized_pnl or 0
        was_profitable = realized_pnl > 0
        
        # Check each signal at entry
        for signal_name, signal_data in trade.signal_scores_at_entry.items():
            if isinstance(signal_data, dict):
                score = signal_data.get("score", 0)
            else:
                score = signal_data
            
            # Signal predicted bullish if score > 0, bearish if < 0
            predicted_direction = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"
            
            # Compare to actual outcome
            # This is simplified - real accuracy needs price direction analysis
            if predicted_direction == "neutral":
                accuracy[signal_name] = 0.5
            elif (predicted_direction == "bullish" and was_profitable) or \
                 (predicted_direction == "bearish" and not was_profitable):
                accuracy[signal_name] = 1.0
            else:
                accuracy[signal_name] = 0.0
        
        # Composite accuracy
        if accuracy:
            accuracy["composite"] = sum(accuracy.values()) / len(accuracy)
        
        return accuracy
    
    def _determine_verdict(
        self,
        trade: ManualTrade,
        realized_pnl: float,
        exit_quality: float,
        signal_accuracy: dict[str, float]
    ) -> tuple[str, str]:
        """Determine verdict for the trade."""
        composite_accuracy = signal_accuracy.get("composite", 0.5)
        
        if realized_pnl > 0:
            if exit_quality >= 0.7:
                return "GOOD_TRADE", "Profitable trade with good exit timing"
            else:
                return "GOOD_IDEA_BAD_EXIT", f"Profitable but left {(1-exit_quality)*100:.0f}% on table"
        else:
            # Loss
            if composite_accuracy < 0.4:
                return "SIGNAL_FAILURE", "Signals predicted wrong direction"
            elif trade.linked_recommendation_id is None:
                return "USER_OVERRIDE", "User-initiated trade without signal"
            else:
                return "BAD_IV_TIMING", "Entry/exit IV timing was poor"
    
    def nightly_report(
        self,
        trades: list[ManualTrade],
        lookback_days: int = 30
    ) -> NightlyReport:
        """
        Generate nightly performance report.
        
        Args:
            trades: All trades (open and closed)
            lookback_days: Days to analyze
            
        Returns:
            NightlyReport with performance summary
        """
        now = datetime.now(self._ist)
        cutoff = now - timedelta(days=lookback_days)
        
        # Filter to closed trades in lookback period
        closed_trades = [
            t for t in trades
            if not t.is_open and t.exit_time and t.exit_time >= cutoff
        ]
        
        # Basic stats
        total_trades = len(closed_trades)
        pnls = [t.realized_pnl or 0 for t in closed_trades]
        winning_trades = sum(1 for p in pnls if p > 0)
        losing_trades = sum(1 for p in pnls if p < 0)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
        best_trade = max(pnls) if pnls else 0
        worst_trade = min(pnls) if pnls else 0
        
        # Strategy breakdown
        strategy_stats = self._compute_strategy_stats(closed_trades)
        
        # Signal accuracy (would need postmortems)
        signal_accuracy: dict[str, dict] = {}
        
        # Regime performance
        regime_stats = self._compute_regime_stats(closed_trades)
        
        # Recommendations
        recommendations = self._generate_recommendations(
            win_rate, strategy_stats, regime_stats
        )
        
        return NightlyReport(
            report_date=date.today(),
            lookback_days=lookback_days,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl_per_trade=avg_pnl,
            best_trade_pnl=best_trade,
            worst_trade_pnl=worst_trade,
            strategy_stats=strategy_stats,
            signal_accuracy=signal_accuracy,
            total_delta_pnl=0,  # Would need postmortems
            total_theta_pnl=0,
            total_vega_pnl=0,
            total_gamma_pnl=0,
            regime_stats=regime_stats,
            iv_timing_score=0.5,  # Would need detailed analysis
            paper_pnl=sum(p for t, p in zip(closed_trades, pnls) if t.paper_mode),
            actual_pnl=sum(p for t, p in zip(closed_trades, pnls) if not t.paper_mode),
            recommendations=recommendations,
        )
    
    def _compute_strategy_stats(
        self,
        trades: list[ManualTrade]
    ) -> dict[str, dict]:
        """Compute per-strategy statistics."""
        stats: dict[str, dict] = {}
        
        for trade in trades:
            strategy = trade.strategy_name
            if strategy not in stats:
                stats[strategy] = {
                    "count": 0,
                    "wins": 0,
                    "total_pnl": 0,
                }
            
            pnl = trade.realized_pnl or 0
            stats[strategy]["count"] += 1
            stats[strategy]["total_pnl"] += pnl
            if pnl > 0:
                stats[strategy]["wins"] += 1
        
        # Compute win rates
        for strategy in stats:
            count = stats[strategy]["count"]
            wins = stats[strategy]["wins"]
            stats[strategy]["win_rate"] = wins / count if count > 0 else 0
        
        return stats
    
    def _compute_regime_stats(
        self,
        trades: list[ManualTrade]
    ) -> dict[str, dict]:
        """Compute per-regime statistics."""
        stats: dict[str, dict] = {}
        
        for trade in trades:
            regime = trade.regime_at_entry or "UNKNOWN"
            if regime not in stats:
                stats[regime] = {
                    "count": 0,
                    "wins": 0,
                    "total_pnl": 0,
                }
            
            pnl = trade.realized_pnl or 0
            stats[regime]["count"] += 1
            stats[regime]["total_pnl"] += pnl
            if pnl > 0:
                stats[regime]["wins"] += 1
        
        # Compute win rates
        for regime in stats:
            count = stats[regime]["count"]
            wins = stats[regime]["wins"]
            stats[regime]["win_rate"] = wins / count if count > 0 else 0
        
        return stats
    
    def _generate_recommendations(
        self,
        win_rate: float,
        strategy_stats: dict,
        regime_stats: dict
    ) -> list[str]:
        """Generate improvement recommendations."""
        recs = []
        
        if win_rate < 0.4:
            recs.append("Consider reviewing signal thresholds - win rate below 40%")
        
        for strategy, stats in strategy_stats.items():
            if stats["count"] >= 5 and stats["win_rate"] < 0.35:
                recs.append(f"Consider pausing {strategy} - underperforming")
        
        for regime, stats in regime_stats.items():
            if stats["count"] >= 5 and stats["win_rate"] > 0.7:
                recs.append(f"Strong performance in {regime} regime - consider increasing allocation")
        
        return recs


# Global instance
_postmortem_engine: PostmortemEngine | None = None


def get_postmortem_engine() -> PostmortemEngine:
    """Get or create global postmortem engine."""
    global _postmortem_engine
    if _postmortem_engine is None:
        _postmortem_engine = PostmortemEngine()
    return _postmortem_engine
