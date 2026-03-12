"""
Paper Trading API Routes.

Endpoints for paper trading performance.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


class PaperTradeSummary(BaseModel):
    """Paper trading summary."""
    total_trades: int
    open_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    avg_pnl_per_trade: float
    best_trade_pnl: float
    worst_trade_pnl: float
    sharpe_ratio: float | None


class TradeHistoryItem(BaseModel):
    """Trade history item."""
    trade_id: str
    timestamp: str
    strategy: str
    underlying: str
    entry_price_avg: float
    exit_price_avg: float | None
    pnl: float
    pnl_pct: float
    status: str
    verdict: str


class SignalAccuracy(BaseModel):
    """Signal accuracy stats."""
    signal_name: str
    total_trades: int
    correct_predictions: int
    accuracy: float
    edge: float


@router.get("/summary", response_model=PaperTradeSummary)
async def get_paper_summary() -> PaperTradeSummary:
    """
    Get paper trading summary statistics.
    
    Returns:
        Overall performance metrics
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    from nse_advisor.paper.portfolio import get_paper_portfolio
    
    tracker = get_position_tracker()
    portfolio = get_paper_portfolio()
    
    all_trades = tracker.get_all_trades(paper_only=True)
    closed_trades = [t for t in all_trades if t.status == "CLOSED"]
    open_trades = [t for t in all_trades if t.status != "CLOSED"]
    
    # Calculate metrics
    total_trades = len(all_trades)
    winning_trades = [t for t in closed_trades if (t.realized_pnl or 0) > 0]
    losing_trades = [t for t in closed_trades if (t.realized_pnl or 0) < 0]
    
    win_rate = len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0.0
    
    realized_pnl = sum(t.realized_pnl or 0 for t in closed_trades)
    unrealized_pnl = sum(t.unrealized_pnl for t in open_trades)
    total_pnl = realized_pnl + unrealized_pnl
    
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0
    
    best_pnl = max((t.realized_pnl or 0 for t in closed_trades), default=0)
    worst_pnl = min((t.realized_pnl or 0 for t in closed_trades), default=0)
    
    # Sharpe ratio (simplified)
    sharpe = portfolio.calculate_sharpe_ratio() if hasattr(portfolio, 'calculate_sharpe_ratio') else None
    
    return PaperTradeSummary(
        total_trades=total_trades,
        open_trades=len(open_trades),
        closed_trades=len(closed_trades),
        winning_trades=len(winning_trades),
        losing_trades=len(losing_trades),
        win_rate=win_rate,
        total_pnl=total_pnl,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        avg_pnl_per_trade=avg_pnl,
        best_trade_pnl=best_pnl,
        worst_trade_pnl=worst_pnl,
        sharpe_ratio=sharpe,
    )


@router.get("/pnl")
async def get_pnl_history(
    days: int = Query(default=30, ge=1, le=365),
) -> list[dict[str, Any]]:
    """
    Get daily P&L history.
    
    Args:
        days: Number of days to return
        
    Returns:
        List of daily P&L values
    """
    from nse_advisor.paper.portfolio import get_paper_portfolio
    
    portfolio = get_paper_portfolio()
    history = portfolio.get_daily_pnl(days)
    
    return [
        {
            "date": item["date"].isoformat() if isinstance(item["date"], date) else item["date"],
            "daily_pnl": item.get("daily_pnl", 0.0),
            "cumulative_pnl": item.get("cumulative_pnl", 0.0),
            "trades_count": item.get("trades_count", 0),
        }
        for item in history
    ]


@router.get("/trades", response_model=list[TradeHistoryItem])
async def get_paper_trades(
    n: int = Query(default=50, ge=1, le=500),
    status: str | None = Query(default=None, description="Filter by status"),
) -> list[TradeHistoryItem]:
    """
    Get paper trade history.
    
    Args:
        n: Number of trades to return
        status: Filter by status (OPEN, CLOSED)
        
    Returns:
        List of paper trades
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    tracker = get_position_tracker()
    postmortem = get_postmortem_engine()
    
    all_trades = tracker.get_all_trades(paper_only=True)
    
    # Filter by status
    if status:
        all_trades = [t for t in all_trades if t.status == status.upper()]
    
    # Sort by entry time (most recent first)
    all_trades.sort(key=lambda t: t.entry_time, reverse=True)
    
    # Limit
    all_trades = all_trades[:n]
    
    result = []
    for trade in all_trades:
        # Get verdict if closed
        verdict = "PENDING"
        if trade.status == "CLOSED":
            pm = postmortem.get_postmortem(trade.trade_id)
            if pm:
                verdict = pm.verdict.value if hasattr(pm.verdict, 'value') else str(pm.verdict)
        
        # Calculate average prices
        entry_avg = sum(leg.entry_price for leg in trade.legs) / len(trade.legs) if trade.legs else 0
        exit_avg = None
        if trade.status == "CLOSED":
            exits = [leg.exit_price for leg in trade.legs if leg.exit_price is not None]
            exit_avg = sum(exits) / len(exits) if exits else None
        
        pnl = trade.realized_pnl if trade.status == "CLOSED" else trade.unrealized_pnl
        pnl_pct = (pnl / trade.max_loss * 100) if trade.max_loss > 0 else 0
        
        result.append(TradeHistoryItem(
            trade_id=trade.trade_id,
            timestamp=trade.entry_time.isoformat(),
            strategy=trade.strategy_name,
            underlying=trade.underlying,
            entry_price_avg=entry_avg,
            exit_price_avg=exit_avg,
            pnl=pnl or 0,
            pnl_pct=pnl_pct,
            status=trade.status,
            verdict=verdict,
        ))
    
    return result


@router.get("/signal-accuracy", response_model=list[SignalAccuracy])
async def get_signal_accuracy() -> list[SignalAccuracy]:
    """
    Get accuracy statistics for each signal.
    
    Returns:
        List of signal accuracy metrics
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    postmortem = get_postmortem_engine()
    accuracy = postmortem.get_signal_accuracy()
    
    return [
        SignalAccuracy(
            signal_name=item["signal_name"],
            total_trades=item["total_trades"],
            correct_predictions=item["correct_predictions"],
            accuracy=item["accuracy"],
            edge=item.get("edge", 0.0),
        )
        for item in accuracy
    ]


@router.get("/regime-performance")
async def get_regime_performance() -> list[dict[str, Any]]:
    """
    Get performance breakdown by market regime.
    
    Returns:
        Win rate and P&L by regime
    """
    from nse_advisor.postmortem.engine import get_postmortem_engine
    
    postmortem = get_postmortem_engine()
    performance = postmortem.get_regime_performance()
    
    return [
        {
            "regime": item["regime"],
            "total_trades": item["total_trades"],
            "winning_trades": item["winning_trades"],
            "win_rate": item["win_rate"],
            "total_pnl": item["total_pnl"],
        }
        for item in performance
    ]
