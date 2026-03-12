"""
Slippage Model.

Simulates realistic bid-ask slippage and transaction costs.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from nse_advisor.paper.paper_ledger import PaperTradeLeg

logger = logging.getLogger(__name__)


class SlippageModel:
    """
    Simulates slippage and transaction costs.
    
    Slippage:
    - ATM options: 0.5-1.5 pts
    - OTM (>200pts from ATM): 1.5-4 pts
    - BUY adds slippage, SELL subtracts
    - Random factor: 0.8-1.3×
    
    Costs:
    - STT: 0.0625% of premium on SELL side
    - Brokerage: ₹20 flat per leg
    """
    
    # Slippage parameters
    ATM_SLIPPAGE_MIN = 0.5
    ATM_SLIPPAGE_MAX = 1.5
    OTM_SLIPPAGE_MIN = 1.5
    OTM_SLIPPAGE_MAX = 4.0
    OTM_DISTANCE_THRESHOLD = 200  # Points from ATM
    
    RANDOM_FACTOR_MIN = 0.8
    RANDOM_FACTOR_MAX = 1.3
    
    # Cost parameters
    STT_RATE = 0.000625  # 0.0625%
    BROKERAGE_PER_LEG = 20.0
    
    def __init__(self, atm_strike: float = 0.0) -> None:
        """
        Initialize slippage model.
        
        Args:
            atm_strike: Current ATM strike (for OTM determination)
        """
        self._atm_strike = atm_strike
    
    def set_atm_strike(self, atm_strike: float) -> None:
        """Update ATM strike."""
        self._atm_strike = atm_strike
    
    def calculate_slippage(
        self,
        strike: float = 0.0,
        action: Literal["BUY", "SELL"] = "BUY",
    ) -> float:
        """
        Calculate slippage points.
        
        Args:
            strike: Strike price
            action: BUY or SELL
            
        Returns:
            Slippage in points (always positive)
        """
        # Determine if OTM
        if self._atm_strike > 0:
            distance = abs(strike - self._atm_strike)
            is_otm = distance > self.OTM_DISTANCE_THRESHOLD
        else:
            is_otm = False
        
        # Base slippage
        if is_otm:
            base_slippage = random.uniform(self.OTM_SLIPPAGE_MIN, self.OTM_SLIPPAGE_MAX)
        else:
            base_slippage = random.uniform(self.ATM_SLIPPAGE_MIN, self.ATM_SLIPPAGE_MAX)
        
        # Apply random factor
        random_factor = random.uniform(self.RANDOM_FACTOR_MIN, self.RANDOM_FACTOR_MAX)
        slippage = base_slippage * random_factor
        
        return slippage
    
    def apply_slippage(
        self,
        ltp: float,
        action: Literal["BUY", "SELL"],
        strike: float = 0.0,
        quantity_lots: int = 1,
    ) -> float:
        """
        Apply slippage to a price.
        
        Args:
            ltp: Last traded price
            action: BUY or SELL
            strike: Strike price (for OTM determination)
            quantity_lots: Number of lots (larger = more slippage)
            
        Returns:
            Price after slippage
        """
        slippage = self.calculate_slippage(strike, action)
        
        # Scale slightly with size
        size_factor = 1 + (quantity_lots - 1) * 0.1
        slippage *= size_factor
        
        # Apply direction
        if action == "BUY":
            return ltp + slippage
        else:
            return max(0.05, ltp - slippage)  # Floor at tick size
    
    def calculate_stt(
        self,
        premium: float,
        action: Literal["BUY", "SELL"],
        quantity: int,
    ) -> float:
        """
        Calculate STT (Securities Transaction Tax).
        
        STT applies on SELL side at 0.0625% of premium.
        
        Args:
            premium: Option premium per unit
            action: BUY or SELL
            quantity: Total quantity
            
        Returns:
            STT amount
        """
        if action == "SELL":
            turnover = premium * quantity
            return turnover * self.STT_RATE
        return 0.0
    
    def calculate_brokerage(self, num_legs: int) -> float:
        """
        Calculate total brokerage.
        
        Args:
            num_legs: Number of legs in trade
            
        Returns:
            Total brokerage
        """
        return self.BROKERAGE_PER_LEG * num_legs * 2  # Entry + exit
    
    def calculate_transaction_costs(
        self,
        legs: list,  # list[PaperTradeLeg]
    ) -> float:
        """
        Calculate total transaction costs for legs.
        
        Args:
            legs: List of paper trade legs
            
        Returns:
            Total transaction costs
        """
        total_stt = 0.0
        
        for leg in legs:
            # STT on entry (only SELL)
            total_stt += self.calculate_stt(
                leg.entry_price, leg.action, leg.total_quantity
            )
            
            # STT on exit (opposite action)
            exit_action: Literal["BUY", "SELL"] = "BUY" if leg.action == "SELL" else "SELL"
            exit_price = leg.exit_price or leg.current_price
            total_stt += self.calculate_stt(
                exit_price, exit_action, leg.total_quantity
            )
        
        brokerage = self.calculate_brokerage(len(legs))
        
        return total_stt + brokerage


# Global instance
_slippage_model: SlippageModel | None = None


def get_slippage_model() -> SlippageModel:
    """Get or create global slippage model."""
    global _slippage_model
    if _slippage_model is None:
        _slippage_model = SlippageModel()
    return _slippage_model
