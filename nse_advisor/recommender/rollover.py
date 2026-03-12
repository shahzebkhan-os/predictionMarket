"""
Rollover Manager.

Suggests rolling positions to next expiry when DTE <= 1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from zoneinfo import ZoneInfo

from nse_advisor.market.instruments import get_instrument_master
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.strategies.base_strategy import StrategyLeg

logger = logging.getLogger(__name__)


@dataclass
class RolloverLeg:
    """A leg to roll."""
    old_tradingsymbol: str
    new_tradingsymbol: str
    strike: float
    option_type: str
    action: str
    current_price: float
    new_price: float
    roll_cost: float  # Positive = cost, Negative = credit


@dataclass
class RolloverSuggestion:
    """Suggestion to roll a position."""
    current_expiry: date
    new_expiry: date
    legs: list[RolloverLeg]
    total_roll_cost: float
    current_position_pnl: float
    reasoning: str


class RolloverManager:
    """
    Manages position rollover suggestions.
    
    When DTE <= 1 and position still open:
    1. Find equivalent position in next weekly expiry
    2. Calculate roll cost (exit current + enter new)
    3. Compare Greeks of old vs new position
    4. Present as rollover card on dashboard
    """
    
    def __init__(self) -> None:
        """Initialize rollover manager."""
        self._ist = ZoneInfo("Asia/Kolkata")
    
    def suggest_rollover(
        self,
        legs: list[StrategyLeg],
        current_chain: OptionChainSnapshot,
        next_chain: OptionChainSnapshot | None,
        current_pnl: float = 0.0,
    ) -> RolloverSuggestion | None:
        """
        Generate rollover suggestion for a position.
        
        Args:
            legs: Current position legs
            current_chain: Current expiry option chain
            next_chain: Next expiry option chain
            current_pnl: Current unrealized P&L
            
        Returns:
            RolloverSuggestion or None if not recommended
        """
        if not next_chain:
            logger.debug("No next expiry chain available for rollover")
            return None
        
        current_expiry = current_chain.expiry
        new_expiry = next_chain.expiry
        
        # Calculate DTE
        dte = (current_expiry - date.today()).days
        if dte > 1:
            logger.debug(f"DTE={dte}, no rollover needed yet")
            return None
        
        rollover_legs = []
        total_roll_cost = 0.0
        
        for leg in legs:
            # Get current exit price
            current_price = self._get_exit_price(leg, current_chain)
            
            # Get new entry price at same strike
            new_price = self._get_entry_price(leg.strike, leg.option_type, next_chain)
            
            # Calculate roll cost
            # For SELL positions: buy back current + sell new
            # For BUY positions: sell current + buy new
            if leg.action == "SELL":
                roll_cost = (current_price - new_price) * leg.total_quantity
            else:
                roll_cost = (new_price - current_price) * leg.total_quantity
            
            # Build new tradingsymbol
            year_short = new_expiry.strftime("%y")
            month = new_expiry.strftime("%b").upper()
            new_symbol = f"{leg.underlying}{year_short}{month}{int(leg.strike)}{leg.option_type}"
            
            rollover_legs.append(RolloverLeg(
                old_tradingsymbol=leg.tradingsymbol,
                new_tradingsymbol=new_symbol,
                strike=leg.strike,
                option_type=leg.option_type,
                action=leg.action,
                current_price=current_price,
                new_price=new_price,
                roll_cost=roll_cost,
            ))
            
            total_roll_cost += roll_cost
        
        # Build reasoning
        if total_roll_cost > 0:
            reasoning = (
                f"Rolling to {new_expiry} will cost ₹{total_roll_cost:.0f}. "
                f"Current P&L: ₹{current_pnl:.0f}. "
                "Consider rolling to avoid expiry-day gamma risk."
            )
        else:
            reasoning = (
                f"Rolling to {new_expiry} earns ₹{abs(total_roll_cost):.0f} credit. "
                f"Current P&L: ₹{current_pnl:.0f}. "
                "Favorable roll - extends position duration."
            )
        
        return RolloverSuggestion(
            current_expiry=current_expiry,
            new_expiry=new_expiry,
            legs=rollover_legs,
            total_roll_cost=total_roll_cost,
            current_position_pnl=current_pnl,
            reasoning=reasoning,
        )
    
    def _get_exit_price(
        self,
        leg: StrategyLeg,
        chain: OptionChainSnapshot
    ) -> float:
        """Get exit price for a leg from chain."""
        strike_data = chain.get_strike(leg.strike)
        if not strike_data:
            return leg.entry_price  # Fallback
        
        if leg.option_type == "CE":
            return strike_data.ce_ltp
        else:
            return strike_data.pe_ltp
    
    def _get_entry_price(
        self,
        strike: float,
        option_type: str,
        chain: OptionChainSnapshot
    ) -> float:
        """Get entry price for a strike from chain."""
        strike_data = chain.get_strike(strike)
        if not strike_data:
            return 0.0
        
        if option_type == "CE":
            return strike_data.ce_ltp
        else:
            return strike_data.pe_ltp


# Global instance
_rollover_manager: RolloverManager | None = None


def get_rollover_manager() -> RolloverManager:
    """Get or create global rollover manager."""
    global _rollover_manager
    if _rollover_manager is None:
        _rollover_manager = RolloverManager()
    return _rollover_manager
