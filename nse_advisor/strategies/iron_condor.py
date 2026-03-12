"""
Iron Condor Strategy.

Sell OTM Put + Buy further OTM Put + Sell OTM Call + Buy further OTM Call.
"""

from __future__ import annotations

from datetime import date

from nse_advisor.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyResult,
)


class IronCondorStrategy(BaseStrategy):
    """
    Iron Condor: Credit spread on both sides.
    
    Structure:
    - Buy OTM Put (wing protection)
    - Sell OTM Put (closer to ATM)
    - Sell OTM Call (closer to ATM)
    - Buy OTM Call (wing protection)
    
    Best when:
    - Range-bound market
    - Elevated IV (premium collection)
    - Low event risk
    
    Characteristics:
    - Defined risk (width of spread - net premium)
    - Max profit = Net premium received
    - Max loss = Width of spread - net premium
    - Benefits from time decay
    """
    
    name = "iron_condor"
    description = "Sell OTM credit spreads on both sides"
    
    is_bullish = False
    is_bearish = False
    is_neutral = True
    is_volatility_play = True
    is_defined_risk = True
    
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
        spread_width: float = 100.0,
        otm_distance: float = 200.0,
        **kwargs
    ) -> StrategyResult:
        """
        Build iron condor strategy.
        
        Args:
            spread_width: Distance between short and long strikes
            otm_distance: Distance of short strikes from ATM
        """
        legs = []
        
        # Calculate strikes
        put_sell_strike = atm_strike - otm_distance
        put_buy_strike = put_sell_strike - spread_width
        call_sell_strike = atm_strike + otm_distance
        call_buy_strike = call_sell_strike + spread_width
        
        # Buy OTM Put (protection)
        legs.append(self._build_leg(
            underlying=underlying,
            strike=put_buy_strike,
            expiry=expiry,
            option_type="PE",
            action="BUY",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Sell OTM Put
        legs.append(self._build_leg(
            underlying=underlying,
            strike=put_sell_strike,
            expiry=expiry,
            option_type="PE",
            action="SELL",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Sell OTM Call
        legs.append(self._build_leg(
            underlying=underlying,
            strike=call_sell_strike,
            expiry=expiry,
            option_type="CE",
            action="SELL",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Buy OTM Call (protection)
        legs.append(self._build_leg(
            underlying=underlying,
            strike=call_buy_strike,
            expiry=expiry,
            option_type="CE",
            action="BUY",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Calculate P&L parameters
        net_premium = sum(
            leg.premium if leg.action == "SELL" else -leg.premium
            for leg in legs
        )
        
        max_profit = net_premium
        max_loss = spread_width * lot_size * quantity_lots - net_premium
        
        # Breakeven levels
        breakeven_lower = put_sell_strike - (net_premium / (lot_size * quantity_lots))
        breakeven_upper = call_sell_strike + (net_premium / (lot_size * quantity_lots))
        
        greeks = self.calculate_greeks(legs)
        
        return StrategyResult(
            name=self.name,
            underlying=underlying,
            expiry=expiry,
            legs=legs,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_levels=[breakeven_lower, breakeven_upper],
            net_premium=net_premium,
            net_delta=greeks["delta"],
            net_gamma=greeks["gamma"],
            net_theta=greeks["theta"],
            net_vega=greeks["vega"],
            is_defined_risk=True,
            margin_required=max_loss * 1.2,  # Approx margin
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
            
            if leg.action == "SELL":
                pnl += premium - intrinsic_value
            else:
                pnl += intrinsic_value - premium
        
        return pnl


def get_iron_condor_strategy() -> IronCondorStrategy:
    """Get iron condor strategy instance."""
    return IronCondorStrategy()
