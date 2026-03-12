"""
Short Straddle Strategy.

Sell ATM CE + ATM PE (high IVR environment).
"""

from __future__ import annotations

from datetime import date

from nse_advisor.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyResult,
)


class ShortStraddleStrategy(BaseStrategy):
    """
    Short Straddle: Sell ATM CE + Sell ATM PE.
    
    Best when:
    - IVR > 60 (selling expensive premium)
    - Range-bound market expectation
    - Low event risk
    
    Characteristics:
    - Unlimited risk (theoretically)
    - Max profit = Net premium received
    - Breakeven = ATM ± total premium
    - Benefits from time decay (positive theta)
    - Negative vega (loses if IV rises)
    """
    
    name = "short_straddle"
    description = "Sell ATM Call and Put to collect premium"
    
    is_bullish = False
    is_bearish = False
    is_neutral = True
    is_volatility_play = True
    is_defined_risk = False
    
    suitable_regimes = ["RANGE_BOUND"]
    
    def build(
        self,
        underlying: str,
        spot_price: float,
        expiry: date,
        lot_size: int,
        atm_strike: float,
        chain_data: dict,
        quantity_lots: int = 1,
        **kwargs
    ) -> StrategyResult:
        """Build short straddle strategy."""
        legs = []
        
        # Sell ATM CE
        ce_leg = self._build_leg(
            underlying=underlying,
            strike=atm_strike,
            expiry=expiry,
            option_type="CE",
            action="SELL",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        )
        legs.append(ce_leg)
        
        # Sell ATM PE
        pe_leg = self._build_leg(
            underlying=underlying,
            strike=atm_strike,
            expiry=expiry,
            option_type="PE",
            action="SELL",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        )
        legs.append(pe_leg)
        
        # Calculate P&L parameters
        total_premium = ce_leg.premium + pe_leg.premium
        max_profit = total_premium
        max_loss = float('inf')  # Unlimited
        
        straddle_value = ce_leg.entry_price + pe_leg.entry_price
        breakeven_upper = atm_strike + straddle_value
        breakeven_lower = atm_strike - straddle_value
        
        # Calculate net Greeks
        greeks = self.calculate_greeks(legs)
        
        return StrategyResult(
            name=self.name,
            underlying=underlying,
            expiry=expiry,
            legs=legs,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_levels=[breakeven_lower, breakeven_upper],
            net_premium=total_premium,
            net_delta=greeks["delta"],
            net_gamma=greeks["gamma"],
            net_theta=greeks["theta"],
            net_vega=greeks["vega"],
            is_defined_risk=False,
            margin_required=spot_price * lot_size * quantity_lots * 0.15,  # Approx 15%
        )
    
    def calculate_payoff(
        self,
        legs: list[StrategyLeg],
        spot_at_expiry: float
    ) -> float:
        """Calculate P&L at expiry."""
        pnl = 0.0
        
        for leg in legs:
            # Premium received/paid
            premium = leg.premium
            
            # Intrinsic value at expiry
            if leg.option_type == "CE":
                intrinsic = max(0, spot_at_expiry - leg.strike)
            else:
                intrinsic = max(0, leg.strike - spot_at_expiry)
            
            intrinsic_value = intrinsic * leg.total_quantity
            
            # For SELL: P&L = premium received - intrinsic value
            # For BUY: P&L = intrinsic value - premium paid
            if leg.action == "SELL":
                pnl += premium - intrinsic_value
            else:
                pnl += intrinsic_value - premium
        
        return pnl


def get_short_straddle_strategy() -> ShortStraddleStrategy:
    """Get short straddle strategy instance."""
    return ShortStraddleStrategy()
