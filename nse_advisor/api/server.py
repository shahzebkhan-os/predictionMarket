"""
NSE Advisor API Server.

FastAPI application with REST and WebSocket endpoints for the React dashboard.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.api.routes import signals, chain, positions, paper, postmortem
from nse_advisor.api.websocket import ConnectionManager

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# WebSocket connection manager
ws_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting NSE Advisor API server")
    yield
    logger.info("Shutting down NSE Advisor API server")


app = FastAPI(
    title="NSE Options Advisor API",
    description="Signal-only options trading advisor for NSE indices",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",  # Vite dev server
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
app.include_router(chain.router, prefix="/api/chain", tags=["chain"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(paper.router, prefix="/api/paper", tags=["paper"])
app.include_router(postmortem.router, prefix="/api/postmortem", tags=["postmortem"])


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    """
    Get system health status.
    
    Returns:
        System status including session age, last chain update, etc.
    """
    from nse_advisor.data.nse_session import get_nse_session
    from nse_advisor.market.option_chain import get_option_chain_manager
    from nse_advisor.market.circuit_breaker import get_circuit_breaker
    from nse_advisor.market.regime import get_regime_classifier
    
    settings = get_settings()
    now = datetime.now(IST)
    
    # Get session status
    session = get_nse_session()
    session_age_minutes = session.session_age_minutes
    session_stale = session_age_minutes > settings.nse_session_refresh_minutes
    
    # Get chain status
    chain_manager = get_option_chain_manager()
    chain = chain_manager.get_latest(settings.primary_underlying)
    chain_timestamp = chain.timestamp if chain else None
    chain_age_seconds = (
        (now - chain_timestamp).total_seconds() 
        if chain_timestamp 
        else float('inf')
    )
    chain_stale = chain_age_seconds > settings.chain_stale_seconds
    
    # Get circuit breaker status
    cb = get_circuit_breaker()
    market_halted = cb.is_market_halted()
    
    # Get regime
    classifier = get_regime_classifier()
    current_regime = classifier.get_current_regime()
    regime = current_regime.regime.value if current_regime else "UNKNOWN"
    
    # Check if within market hours
    is_market_hours = (
        now.weekday() < 5
        and now.hour >= 9 and now.minute >= 15
        and now.hour < 15 or (now.hour == 15 and now.minute <= 30)
    )
    
    return {
        "status": "healthy" if not (session_stale or chain_stale) else "degraded",
        "timestamp": now.isoformat(),
        "market_hours": is_market_hours,
        "market_halted": market_halted,
        "regime": regime,
        "session": {
            "age_minutes": round(session_age_minutes, 1),
            "stale": session_stale,
            "last_refresh": session.last_refresh.isoformat() if session.last_refresh else None,
        },
        "option_chain": {
            "age_seconds": round(chain_age_seconds, 1) if chain_age_seconds != float('inf') else None,
            "stale": chain_stale,
            "last_update": chain_timestamp.isoformat() if chain_timestamp else None,
            "underlying": settings.primary_underlying,
        },
        "config": {
            "scan_interval_seconds": settings.scan_interval_seconds,
            "chain_stale_seconds": settings.chain_stale_seconds,
            "ticker_stale_seconds": settings.ticker_stale_seconds,
            "paper_trading": settings.paper_trading,
        },
    }


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for live updates.
    
    Pushes updates every 5 seconds during market hours:
    - Composite score and confidence
    - Current regime
    - Open positions P&L
    - Latest recommendation (if any)
    - Option chain summary
    """
    await ws_manager.connect(websocket)
    
    try:
        while True:
            # Build snapshot
            snapshot = await build_live_snapshot()
            
            # Send to client
            await ws_manager.send_json(websocket, snapshot)
            
            # Wait 5 seconds
            await asyncio.sleep(5)
            
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.debug("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)


async def build_live_snapshot() -> dict[str, Any]:
    """Build a live data snapshot for WebSocket broadcast."""
    from nse_advisor.signals.engine import get_signal_engine
    from nse_advisor.market.regime import get_regime_classifier
    from nse_advisor.market.option_chain import get_option_chain_manager
    from nse_advisor.tracker.position_tracker import get_position_tracker
    from nse_advisor.market.circuit_breaker import get_circuit_breaker
    
    settings = get_settings()
    now = datetime.now(IST)
    
    # Get signal engine state
    signal_engine = get_signal_engine()
    latest_signal = signal_engine.get_latest_result()
    
    # Get regime
    classifier = get_regime_classifier()
    current_regime = classifier.get_current_regime()
    regime = current_regime.regime.value if current_regime else "UNKNOWN"
    
    # Get circuit breaker status
    cb = get_circuit_breaker()
    market_halted = cb.is_market_halted()
    
    # Get open positions P&L
    tracker = get_position_tracker()
    open_trades = tracker.get_open_trades()
    positions_pnl = {
        "count": len(open_trades),
        "total_pnl": sum(t.unrealized_pnl for t in open_trades),
        "trades": [
            {
                "trade_id": t.trade_id,
                "strategy": t.strategy_name,
                "pnl": t.unrealized_pnl,
                "status": t.status,
            }
            for t in open_trades
        ],
    }
    
    # Get option chain summary
    chain_manager = get_option_chain_manager()
    chain = chain_manager.get_latest(settings.primary_underlying)
    chain_summary = None
    if chain:
        chain_summary = {
            "underlying": chain.underlying,
            "spot": chain.spot_price,
            "atm_strike": chain.atm_strike,
            "pcr": chain.pcr,
            "max_pain": chain.max_pain,
            "timestamp": chain.timestamp.isoformat() if chain.timestamp else None,
        }
    
    # Build snapshot
    snapshot = {
        "type": "snapshot",
        "timestamp": now.isoformat(),
        "regime": regime,
        "composite_score": latest_signal.composite_score if latest_signal else 0.0,
        "composite_confidence": latest_signal.composite_confidence if latest_signal else 0.0,
        "market_halted": market_halted,
        "option_chain": chain_summary,
        "open_positions_pnl": positions_pnl,
        "latest_recommendation": None,  # Add recommendation if available
    }
    
    # Add latest recommendation if signal is actionable
    if latest_signal and latest_signal.should_recommend:
        from nse_advisor.recommender.engine import get_recommender
        recommender = get_recommender()
        recommendation = recommender.get_latest()
        if recommendation:
            snapshot["latest_recommendation"] = {
                "id": recommendation.recommendation_id,
                "strategy": recommendation.strategy_name,
                "underlying": recommendation.underlying,
                "urgency": recommendation.urgency.value,
                "legs": [
                    {
                        "symbol": leg.tradingsymbol,
                        "action": leg.action,
                        "strike": leg.strike,
                        "expiry": leg.expiry.isoformat(),
                        "suggested_price": leg.suggested_entry_price,
                        "lots": leg.suggested_lots,
                    }
                    for leg in recommendation.legs
                ],
                "max_profit": recommendation.max_profit,
                "max_loss": recommendation.max_loss,
            }
    
    return snapshot


# Run with: uvicorn nse_advisor.api.server:app --host 0.0.0.0 --port 8000 --reload
