"""
Position Sizer.

Calculates recommended lot count using Kelly criterion and max loss limits.
"""

from __future__ import annotations

import logging
import math

from nse_advisor.config import get_settings
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


def calculate_position_size(
    strategy: BaseStrategy,
    chain: OptionChainSnapshot,
    underlying: str,
    win_rate: float = 0.55,
    avg_win_loss_ratio: float = 1.5,
) -> int:
    """
    Calculate recommended position size in lots.
    
    Uses:
    1. Max loss per trade limit
    2. Kelly criterion for optimal sizing
    3. Max lots per underlying limit
    
    Args:
        strategy: Strategy being recommended
        chain: Option chain for premium calculation
        underlying: Underlying symbol
        win_rate: Historical win rate (default 55%)
        avg_win_loss_ratio: Average win/loss ratio (default 1.5)
        
    Returns:
        Recommended number of lots
    """
    settings = get_settings()
    
    # Get max lots limit for underlying
    if underlying == "NIFTY":
        max_lots = settings.max_lots_per_trade_nifty
    elif underlying == "BANKNIFTY":
        max_lots = settings.max_lots_per_trade_banknifty
    else:
        max_lots = min(settings.max_lots_per_trade_nifty, settings.max_lots_per_trade_banknifty)
    
    # For defined risk strategies: lots = max_loss_limit / max_loss_per_lot
    if strategy.is_defined_risk:
        # Estimate max loss per lot
        # Use ATM straddle as proxy for typical spread cost
        atm_strike = chain.get_atm_strike()
        straddle_price = chain.get_straddle_price()
        
        # For defined risk, assume max loss is spread width or premium
        estimated_max_loss_per_lot = straddle_price * chain.lot_size * 0.5
        
        if estimated_max_loss_per_lot > 0:
            lots_by_risk = math.floor(
                settings.max_loss_per_trade_inr / estimated_max_loss_per_lot
            )
        else:
            lots_by_risk = 1
    else:
        # Undefined risk: use margin-based sizing
        spot = chain.spot_price
        lot_size = chain.lot_size
        
        # Estimate margin per lot (approx 15% of notional)
        margin_per_lot = spot * lot_size * 0.15
        
        # Capital available for undefined risk
        available_capital = settings.paper_capital * settings.max_undefined_risk_pct
        
        lots_by_risk = math.floor(available_capital / margin_per_lot) if margin_per_lot > 0 else 1
    
    # Apply Kelly criterion
    kelly_fraction = calculate_kelly_fraction(win_rate, avg_win_loss_ratio)
    kelly_adjusted = kelly_fraction * settings.kelly_fraction  # Apply user's kelly fraction
    
    lots_by_kelly = max(1, math.floor(lots_by_risk * kelly_adjusted))
    
    # Apply max lots limit
    final_lots = min(lots_by_kelly, max_lots)
    
    # Ensure at least 1 lot
    final_lots = max(1, final_lots)
    
    logger.debug(
        f"Position sizing: risk={lots_by_risk}, kelly_adj={kelly_adjusted:.2f}, "
        f"kelly_lots={lots_by_kelly}, max={max_lots}, final={final_lots}"
    )
    
    return final_lots


def calculate_kelly_fraction(
    win_rate: float,
    win_loss_ratio: float
) -> float:
    """
    Calculate Kelly criterion fraction.
    
    Kelly % = W - (1-W)/R
    
    Where:
    - W = Win probability
    - R = Win/Loss ratio (average win / average loss)
    
    Args:
        win_rate: Probability of winning (0-1)
        win_loss_ratio: Average win / average loss
        
    Returns:
        Kelly fraction (0-1)
    """
    if win_loss_ratio <= 0:
        return 0.0
    
    kelly = win_rate - (1 - win_rate) / win_loss_ratio
    
    # Clamp between 0 and 1
    return max(0.0, min(1.0, kelly))


def calculate_margin_requirement(
    strategy_result,
    spot_price: float,
    lot_size: int
) -> float:
    """
    Estimate margin requirement for a strategy.
    
    Args:
        strategy_result: Result from strategy build
        spot_price: Current spot price
        lot_size: Lot size
        
    Returns:
        Estimated margin in INR
    """
    if strategy_result.is_defined_risk:
        # Debit/defined risk: margin = premium paid + small buffer
        return abs(strategy_result.net_premium) * 1.1
    else:
        # Undefined risk: SPAN margin approximation
        # Typically 10-20% of notional for index options
        notional = spot_price * lot_size * len(strategy_result.legs) // 2
        return notional * 0.15
