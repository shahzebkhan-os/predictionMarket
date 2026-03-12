"""
Positions API Routes.

Endpoints for position tracking.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Path, HTTPException, Body
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


class TradeLegInput(BaseModel):
    """Input for a trade leg."""
    tradingsymbol: str
    underlying: str
    strike: float
    expiry: str  # DD-MMM-YYYY
    option_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    quantity_lots: int = Field(ge=1)
    entry_price: float = Field(gt=0)


class NewTradeInput(BaseModel):
    """Input for creating a new trade."""
    strategy_name: str
    underlying: str
    legs: list[TradeLegInput]
    max_loss_inr: float = Field(gt=0)
    stop_loss_pct: float = Field(default=1.5, ge=0.5, le=3.0)
    linked_recommendation_id: str | None = None
    paper_mode: bool = True


class CloseTradeInput(BaseModel):
    """Input for closing a trade."""
    exit_prices: dict[str, float]  # tradingsymbol -> exit_price
    exit_reason: str = "manual_close"


class TradeResponse(BaseModel):
    """Trade response."""
    trade_id: str
    strategy_name: str
    underlying: str
    expiry: str
    entry_time: str
    status: str
    paper_mode: bool
    legs: list[dict[str, Any]]
    unrealized_pnl: float
    max_profit: float
    max_loss: float
    dte: int
    net_greeks: dict[str, float]


class PortfolioGreeks(BaseModel):
    """Portfolio Greeks summary."""
    delta: float
    gamma: float
    theta: float
    vega: float


@router.get("", response_model=list[TradeResponse])
async def get_all_positions() -> list[TradeResponse]:
    """
    Get all open positions.
    
    Returns:
        List of open trades with current P&L and Greeks
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    
    tracker = get_position_tracker()
    trades = tracker.get_open_trades()
    
    return [_trade_to_response(t) for t in trades]


@router.get("/greeks", response_model=PortfolioGreeks)
async def get_portfolio_greeks() -> PortfolioGreeks:
    """
    Get aggregate portfolio Greeks.
    
    Returns:
        Net delta, gamma, theta, vega across all positions
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    
    tracker = get_position_tracker()
    greeks = tracker.get_portfolio_greeks()
    
    return PortfolioGreeks(
        delta=greeks.get("delta", 0.0),
        gamma=greeks.get("gamma", 0.0),
        theta=greeks.get("theta", 0.0),
        vega=greeks.get("vega", 0.0),
    )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_position(
    trade_id: str = Path(..., description="Trade ID"),
) -> TradeResponse:
    """
    Get a specific position.
    
    Args:
        trade_id: Trade identifier
        
    Returns:
        Trade details
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    
    tracker = get_position_tracker()
    trade = tracker.get_trade(trade_id)
    
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    
    return _trade_to_response(trade)


@router.post("", response_model=TradeResponse)
async def create_position(
    trade_input: NewTradeInput = Body(...),
) -> TradeResponse:
    """
    Log a new manual trade.
    
    Args:
        trade_input: Trade details
        
    Returns:
        Created trade
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    from nse_advisor.tracker.state import ManualTrade, TradeLeg
    from nse_advisor.market.instruments import get_instrument_master
    
    import uuid
    
    tracker = get_position_tracker()
    instruments = get_instrument_master()
    settings = get_settings()
    
    # Validate trade
    if len(trade_input.legs) == 0:
        raise HTTPException(status_code=400, detail="At least 1 leg required")
    
    # Build legs
    legs = []
    for leg_input in trade_input.legs:
        expiry_date = datetime.strptime(leg_input.expiry, "%d-%b-%Y").date()
        lot_size = instruments.get_lot_size(leg_input.underlying)
        
        # Validate lots
        max_lots = (
            settings.max_lots_per_trade_nifty
            if leg_input.underlying.upper() == "NIFTY"
            else settings.max_lots_per_trade_banknifty
        )
        if leg_input.quantity_lots > max_lots:
            raise HTTPException(
                status_code=400,
                detail=f"Max lots for {leg_input.underlying} is {max_lots}"
            )
        
        legs.append(TradeLeg(
            tradingsymbol=leg_input.tradingsymbol,
            underlying=leg_input.underlying,
            strike=leg_input.strike,
            expiry=expiry_date,
            option_type=leg_input.option_type,
            action=leg_input.action,
            quantity_lots=leg_input.quantity_lots,
            lot_size=lot_size,
            entry_price=leg_input.entry_price,
            current_price=leg_input.entry_price,
        ))
    
    # Determine expiry (use first leg)
    trade_expiry = legs[0].expiry
    
    # Create trade
    trade = ManualTrade(
        trade_id=str(uuid.uuid4()),
        strategy_name=trade_input.strategy_name,
        underlying=trade_input.underlying,
        expiry=trade_expiry,
        entry_time=datetime.now(IST),
        legs=legs,
        linked_recommendation_id=trade_input.linked_recommendation_id,
        max_loss=trade_input.max_loss_inr,
        stop_loss_inr=trade_input.max_loss_inr * trade_input.stop_loss_pct,
        status="PAPER" if trade_input.paper_mode else "LIVE",
        paper_mode=trade_input.paper_mode,
    )
    
    # Add to tracker
    tracker.add_trade(trade)
    
    return _trade_to_response(trade)


@router.patch("/{trade_id}/close", response_model=TradeResponse)
async def close_position(
    trade_id: str = Path(..., description="Trade ID"),
    close_input: CloseTradeInput = Body(...),
) -> TradeResponse:
    """
    Close a position.
    
    Args:
        trade_id: Trade identifier
        close_input: Exit prices for each leg
        
    Returns:
        Closed trade
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    
    tracker = get_position_tracker()
    trade = tracker.get_trade(trade_id)
    
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    
    if trade.status == "CLOSED":
        raise HTTPException(status_code=400, detail="Trade is already closed")
    
    # Update exit prices
    for leg in trade.legs:
        if leg.tradingsymbol in close_input.exit_prices:
            leg.exit_price = close_input.exit_prices[leg.tradingsymbol]
            leg.exit_time = datetime.now(IST)
    
    # Mark as closed
    trade.status = "CLOSED"
    trade.exit_time = datetime.now(IST)
    trade.exit_reason = close_input.exit_reason
    
    return _trade_to_response(trade)


@router.get("/{trade_id}/alerts")
async def get_position_alerts(
    trade_id: str = Path(..., description="Trade ID"),
) -> list[dict[str, Any]]:
    """
    Get exit alerts for a position.
    
    Args:
        trade_id: Trade identifier
        
    Returns:
        List of active alerts for this trade
    """
    from nse_advisor.tracker.position_tracker import get_position_tracker
    from nse_advisor.tracker.exit_advisor import get_exit_advisor
    
    tracker = get_position_tracker()
    exit_advisor = get_exit_advisor()
    
    trade = tracker.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    
    alerts = exit_advisor.check_all_conditions(trade)
    
    return [
        {
            "alert_type": alert.alert_type,
            "urgency": alert.urgency,
            "message": alert.message,
            "action": alert.suggested_action,
        }
        for alert in alerts
    ]


def _trade_to_response(trade: Any) -> TradeResponse:
    """Convert ManualTrade to TradeResponse."""
    return TradeResponse(
        trade_id=trade.trade_id,
        strategy_name=trade.strategy_name,
        underlying=trade.underlying,
        expiry=trade.expiry.strftime("%d-%b-%Y"),
        entry_time=trade.entry_time.isoformat(),
        status=trade.status,
        paper_mode=trade.paper_mode,
        legs=[
            {
                "tradingsymbol": leg.tradingsymbol,
                "strike": leg.strike,
                "option_type": leg.option_type,
                "action": leg.action,
                "quantity_lots": leg.quantity_lots,
                "entry_price": leg.entry_price,
                "current_price": leg.current_price,
                "unrealized_pnl": leg.unrealized_pnl,
            }
            for leg in trade.legs
        ],
        unrealized_pnl=trade.unrealized_pnl,
        max_profit=trade.max_profit,
        max_loss=trade.max_loss,
        dte=trade.dte,
        net_greeks=trade.net_greeks(),
    )
