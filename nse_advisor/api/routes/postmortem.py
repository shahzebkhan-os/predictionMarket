"""
Postmortem API Routes.

Endpoints for trade postmortem analysis.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Path, Query, HTTPException
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


class GreeksAttribution(BaseModel):
    """Greeks P&L attribution."""
    delta_pnl: float
    gamma_pnl: float
    theta_pnl: float
    vega_pnl: float
    residual_pnl: float
    total_pnl: float


class TradePostmortem(BaseModel):
    """Complete trade postmortem."""
    trade_id: str
    strategy: str
    underlying: str
    entry_time: str
    exit_time: str | None
    entry_regime: str
    exit_regime: str | None
    dte_at_entry: int
    dte_at_exit: int | None
    pnl: float
    pnl_pct: float
    verdict: str
    verdict_reason: str
    greeks_attribution: GreeksAttribution
    signals_at_entry: dict[str, float]
    signals_at_exit: dict[str, float] | None
    recommendations: list[str]


class CalibrationBucket(BaseModel):
    """Calibration bucket for confidence analysis."""
    bucket: str
    predicted_win_prob: float
    actual_win_rate: float
    trade_count: int


@router.get("/{trade_id}", response_model=TradePostmortem)
async def get_trade_postmortem(
    trade_id: str = Path(..., description="Trade ID"),
) -> TradePostmortem:
    """
    Get detailed postmortem for a specific trade.
    
    Args:
        trade_id: Trade identifier
        
    Returns:
        Complete postmortem analysis
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    from nse_advisor.tracker.position_tracker import get_position_tracker
    
    postmortem_engine = get_postmortem_engine()
    tracker = get_position_tracker()
    
    trade = tracker.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    
    pm = postmortem_engine.get_postmortem(trade_id)
    
    if not pm:
        # Generate postmortem on-demand if not exists
        if trade.status == "CLOSED":
            pm = await postmortem_engine.analyze_trade(trade)
        else:
            raise HTTPException(status_code=400, detail="Trade is not closed yet")
    
    # Calculate P&L percentage
    pnl = trade.realized_pnl or trade.unrealized_pnl
    pnl_pct = (pnl / trade.max_loss * 100) if trade.max_loss > 0 else 0
    
    return TradePostmortem(
        trade_id=trade.trade_id,
        strategy=trade.strategy_name,
        underlying=trade.underlying,
        entry_time=trade.entry_time.isoformat(),
        exit_time=trade.exit_time.isoformat() if trade.exit_time else None,
        entry_regime=trade.regime_at_entry,
        exit_regime=pm.exit_regime if pm else None,
        dte_at_entry=trade.dte_at_entry,
        dte_at_exit=pm.dte_at_exit if pm else None,
        pnl=pnl,
        pnl_pct=pnl_pct,
        verdict=pm.verdict.value if pm and hasattr(pm.verdict, 'value') else "UNKNOWN",
        verdict_reason=pm.verdict_reason if pm else "",
        greeks_attribution=GreeksAttribution(
            delta_pnl=pm.delta_pnl if pm else 0,
            gamma_pnl=pm.gamma_pnl if pm else 0,
            theta_pnl=pm.theta_pnl if pm else 0,
            vega_pnl=pm.vega_pnl if pm else 0,
            residual_pnl=pm.residual_pnl if pm else 0,
            total_pnl=pnl,
        ),
        signals_at_entry=trade.signal_scores_at_entry,
        signals_at_exit=pm.signal_scores_at_exit if pm else None,
        recommendations=pm.recommendations if pm else [],
    )


@router.get("/report/daily")
async def get_daily_report(
    date_str: str | None = Query(default=None, description="Date (YYYY-MM-DD)"),
) -> dict[str, Any]:
    """
    Get nightly postmortem report.
    
    Args:
        date_str: Optional date (defaults to today)
        
    Returns:
        Aggregate report for the day
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    postmortem_engine = get_postmortem_engine()
    
    if date_str:
        report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        report_date = date.today()
    
    report = postmortem_engine.get_daily_report(report_date)
    
    if not report:
        return {
            "date": report_date.isoformat(),
            "trades_count": 0,
            "message": "No trades closed on this day",
        }
    
    return {
        "date": report.date.isoformat() if hasattr(report, 'date') else report_date.isoformat(),
        "trades_count": report.trades_count,
        "total_pnl": report.total_pnl,
        "winning_trades": report.winning_trades,
        "losing_trades": report.losing_trades,
        "win_rate": report.win_rate,
        "best_trade": {
            "trade_id": report.best_trade_id,
            "pnl": report.best_trade_pnl,
        } if hasattr(report, 'best_trade_id') else None,
        "worst_trade": {
            "trade_id": report.worst_trade_id,
            "pnl": report.worst_trade_pnl,
        } if hasattr(report, 'worst_trade_id') else None,
        "signal_performance": report.signal_performance if hasattr(report, 'signal_performance') else {},
        "regime_performance": report.regime_performance if hasattr(report, 'regime_performance') else {},
        "recommendations": report.recommendations if hasattr(report, 'recommendations') else [],
    }


@router.get("/calibration", response_model=list[CalibrationBucket])
async def get_calibration_curve() -> list[CalibrationBucket]:
    """
    Get confidence calibration data.
    
    Returns:
        Buckets showing predicted vs actual win rates
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    postmortem_engine = get_postmortem_engine()
    calibration = postmortem_engine.get_calibration_data()
    
    return [
        CalibrationBucket(
            bucket=item["bucket"],
            predicted_win_prob=item["predicted_win_prob"],
            actual_win_rate=item["actual_win_rate"],
            trade_count=item["trade_count"],
        )
        for item in calibration
    ]


@router.get("/signal-matrix")
async def get_signal_accuracy_matrix() -> list[dict[str, Any]]:
    """
    Get signal accuracy matrix.
    
    Each row represents a closed trade, each column a signal.
    Cell value: 1 = signal was correct, 0 = incorrect, null = neutral.
    
    Returns:
        Matrix data for heatmap visualization
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    postmortem_engine = get_postmortem_engine()
    matrix = postmortem_engine.get_signal_accuracy_matrix()
    
    return matrix


@router.get("/attribution/aggregate")
async def get_aggregate_attribution(
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """
    Get aggregate Greeks attribution over time.
    
    Args:
        days: Number of days to analyze
        
    Returns:
        Total P&L attributed to each Greek
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    postmortem_engine = get_postmortem_engine()
    attribution = postmortem_engine.get_aggregate_attribution(days)
    
    return {
        "period_days": days,
        "total_pnl": attribution.get("total_pnl", 0),
        "delta_pnl": attribution.get("delta_pnl", 0),
        "gamma_pnl": attribution.get("gamma_pnl", 0),
        "theta_pnl": attribution.get("theta_pnl", 0),
        "vega_pnl": attribution.get("vega_pnl", 0),
        "residual_pnl": attribution.get("residual_pnl", 0),
        "trades_count": attribution.get("trades_count", 0),
    }
