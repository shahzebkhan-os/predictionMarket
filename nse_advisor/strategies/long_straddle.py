"""
Long Straddle Strategy.

Buy ATM CE + Buy ATM PE (pre-event volatility play).
"""

from __future__ import annotations

from datetime import date

from nse_advisor.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyResult,
)


class LongStraddleStrategy(BaseStrategy):
    """
    Long Straddle: Buy ATM CE + Buy ATM PE.
    
    Best when:
    - Expecting large move (direction uncertain)
    - Low IVR (buying cheap premium)
    - High-impact event approaching
    
    Characteristics:
    - Defined risk (premium paid)
    - Max profit = Unlimited
    - Max loss = Total premium paid
    - Benefits from large moves (positive gamma)
    - Positive vega (benefits from IV rise)
    """
    
    name = "long_straddle"
    description = "Buy ATM Call and Put for large move"
    
    is_bullish = False
    is_bearish = False
    is_neutral = False
    is_volatility_play = True
    is_defined_risk = True
    
    suitable_regimes = ["HIGH_VOLATILITY"]
    
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
        """Build long straddle strategy."""
        legs = []
        
        # Buy ATM CE
        ce_leg = self._build_leg(
            underlying=underlying,
            strike=atm_strike,
            expiry=expiry,
            option_type="CE",
            action="BUY",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        )
        legs.append(ce_leg)
        
        # Buy ATM PE
        pe_leg = self._build_leg(
            underlying=underlying,
            strike=atm_strike,
            expiry=expiry,
            option_type="PE",
            action="BUY",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        )
        legs.append(pe_leg)
        
        # Calculate P&L parameters
        total_premium = ce_leg.premium + pe_leg.premium
        max_profit = float('inf')  # Unlimited
        max_loss = total_premium
        
        straddle_value = ce_leg.entry_price + pe_leg.entry_price
        breakeven_upper = atm_strike + straddle_value
        breakeven_lower = atm_strike - straddle_value
        
        greeks = self.calculate_greeks(legs)
        
        return StrategyResult(
            name=self.name,
            underlying=underlying,
            expiry=expiry,
            legs=legs,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_levels=[breakeven_lower, breakeven_upper],
            net_premium=-total_premium,  # Debit
            net_delta=greeks["delta"],
            net_gamma=greeks["gamma"],
            net_theta=greeks["theta"],
            net_vega=greeks["vega"],
            is_defined_risk=True,
            margin_required=total_premium,
        )
    
    def calculate_payoff(
        self,
        legs: list[StrategyLeg],
        spot_at_expiry: float
    ) -> float:
        """Calculate P&L at expiry."""
        pnl = 0.0
        
        for leg in legs:
            premium = leg.premium
            
            if leg.option_type == "CE":
                intrinsic = max(0, spot_at_expiry - leg.strike)
            else:
                intrinsic = max(0, leg.strike - spot_at_expiry)
            
            intrinsic_value = intrinsic * leg.total_quantity
            
            # For BUY: P&L = intrinsic value - premium paid
            pnl += intrinsic_value - premium
        
        return pnl


def get_long_straddle_strategy() -> LongStraddleStrategy:
    """Get long straddle strategy instance."""
    return LongStraddleStrategy()
