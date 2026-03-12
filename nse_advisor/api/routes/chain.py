"""
Option Chain API Routes.

Endpoints for option chain data.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


class StrikeData(BaseModel):
    """Data for a single strike."""
    strike: float
    ce_oi: int
    ce_oi_change: int
    ce_ltp: float
    ce_iv: float
    ce_volume: int
    pe_oi: int
    pe_oi_change: int
    pe_ltp: float
    pe_iv: float
    pe_volume: int
    is_atm: bool
    is_max_pain: bool


class ChainSnapshot(BaseModel):
    """Option chain snapshot."""
    underlying: str
    spot_price: float
    atm_strike: float
    pcr: float
    max_pain: float
    total_ce_oi: int
    total_pe_oi: int
    gex: float
    timestamp: str
    expiries: list[str]
    selected_expiry: str
    strikes: list[StrikeData]


class GEXData(BaseModel):
    """GEX data for a strike."""
    strike: float
    gex: float


class IVSkewData(BaseModel):
    """IV skew data."""
    strike: float
    ce_iv: float
    pe_iv: float


@router.get("/{underlying}", response_model=ChainSnapshot)
async def get_option_chain(
    underlying: str = Path(..., description="Underlying symbol (NIFTY, BANKNIFTY)"),
    expiry: str | None = Query(default=None, description="Expiry date (DD-MMM-YYYY)"),
) -> ChainSnapshot:
    """
    Get full option chain snapshot for an underlying.
    
    Args:
        underlying: Underlying symbol
        expiry: Optional expiry filter
        
    Returns:
        ChainSnapshot with all strike data
    """
    from nse_advisor.market.option_chain import get_option_chain_manager
    
    chain_manager = get_option_chain_manager()
    chain = chain_manager.get_latest(underlying.upper())
    
    if not chain:
        # Return empty chain
        return ChainSnapshot(
            underlying=underlying.upper(),
            spot_price=0.0,
            atm_strike=0.0,
            pcr=0.0,
            max_pain=0.0,
            total_ce_oi=0,
            total_pe_oi=0,
            gex=0.0,
            timestamp=datetime.now(IST).isoformat(),
            expiries=[],
            selected_expiry="",
            strikes=[],
        )
    
    # Filter by expiry if provided
    selected_expiry = chain.expiries[0] if chain.expiries else ""
    if expiry:
        selected_expiry = expiry
    
    # Build strikes list
    strikes = []
    for strike in sorted(chain.strikes.keys()):
        ce = chain.get_ce(strike)
        pe = chain.get_pe(strike)
        
        if ce or pe:
            strikes.append(StrikeData(
                strike=strike,
                ce_oi=ce.open_interest if ce else 0,
                ce_oi_change=ce.oi_change if ce else 0,
                ce_ltp=ce.ltp if ce else 0.0,
                ce_iv=ce.iv if ce else 0.0,
                ce_volume=ce.volume if ce else 0,
                pe_oi=pe.open_interest if pe else 0,
                pe_oi_change=pe.oi_change if pe else 0,
                pe_ltp=pe.ltp if pe else 0.0,
                pe_iv=pe.iv if pe else 0.0,
                pe_volume=pe.volume if pe else 0,
                is_atm=abs(strike - chain.atm_strike) < 25,
                is_max_pain=abs(strike - chain.max_pain) < 25,
            ))
    
    return ChainSnapshot(
        underlying=chain.underlying,
        spot_price=chain.spot_price,
        atm_strike=chain.atm_strike,
        pcr=chain.pcr,
        max_pain=chain.max_pain,
        total_ce_oi=chain.total_ce_oi,
        total_pe_oi=chain.total_pe_oi,
        gex=chain.net_gex if hasattr(chain, 'net_gex') else 0.0,
        timestamp=chain.timestamp.isoformat() if chain.timestamp else datetime.now(IST).isoformat(),
        expiries=[e.strftime("%d-%b-%Y") for e in chain.expiries] if chain.expiries else [],
        selected_expiry=selected_expiry,
        strikes=strikes,
    )


@router.get("/{underlying}/gex", response_model=list[GEXData])
async def get_gex_data(
    underlying: str = Path(..., description="Underlying symbol"),
) -> list[GEXData]:
    """
    Get GEX (Gamma Exposure) data by strike.
    
    Args:
        underlying: Underlying symbol
        
    Returns:
        List of GEX values by strike
    """
    from nse_advisor.market.option_chain import get_option_chain_manager
    
    chain_manager = get_option_chain_manager()
    chain = chain_manager.get_latest(underlying.upper())
    
    if not chain:
        return []
    
    gex_data = []
    for strike in sorted(chain.strikes.keys()):
        ce = chain.get_ce(strike)
        pe = chain.get_pe(strike)
        
        # GEX calculation: CE OI * gamma - PE OI * gamma
        # Simplified: (CE_OI - PE_OI) * spot / 100
        ce_oi = ce.open_interest if ce else 0
        pe_oi = pe.open_interest if pe else 0
        gex = (ce_oi - pe_oi) * chain.spot_price / 10000
        
        gex_data.append(GEXData(strike=strike, gex=gex))
    
    return gex_data


@router.get("/{underlying}/iv-skew", response_model=list[IVSkewData])
async def get_iv_skew(
    underlying: str = Path(..., description="Underlying symbol"),
) -> list[IVSkewData]:
    """
    Get IV skew data.
    
    Args:
        underlying: Underlying symbol
        
    Returns:
        List of CE/PE IV by strike
    """
    from nse_advisor.market.option_chain import get_option_chain_manager
    
    chain_manager = get_option_chain_manager()
    chain = chain_manager.get_latest(underlying.upper())
    
    if not chain:
        return []
    
    iv_data = []
    for strike in sorted(chain.strikes.keys()):
        ce = chain.get_ce(strike)
        pe = chain.get_pe(strike)
        
        if ce and pe:
            iv_data.append(IVSkewData(
                strike=strike,
                ce_iv=ce.iv if ce else 0.0,
                pe_iv=pe.iv if pe else 0.0,
            ))
    
    return iv_data


@router.get("/{underlying}/pcr-history")
async def get_pcr_history(
    underlying: str = Path(..., description="Underlying symbol"),
    n: int = Query(default=30, ge=1, le=100),
) -> list[dict[str, Any]]:
    """
    Get PCR history (last N snapshots).
    
    Args:
        underlying: Underlying symbol
        n: Number of snapshots
        
    Returns:
        List of PCR values over time
    """
    from nse_advisor.market.option_chain import get_option_chain_manager
    
    chain_manager = get_option_chain_manager()
    history = chain_manager.get_pcr_history(underlying.upper(), n)
    
    return [
        {
            "timestamp": item["timestamp"].isoformat() if item.get("timestamp") else None,
            "pcr": item.get("pcr", 0.0),
        }
        for item in history
    ]
