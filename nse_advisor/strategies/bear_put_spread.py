"""
Bear Put Spread Strategy.

Buy ATM/ITM Put + Sell OTM Put.
"""

from __future__ import annotations

from datetime import date

from nse_advisor.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyResult,
)


class BearPutSpreadStrategy(BaseStrategy):
    """
    Bear Put Spread: Buy higher strike PE + Sell lower strike PE.
    
    Best when:
    - Moderately bearish outlook
    - Want to reduce cost of long put
    - Trending down market
    
    Characteristics:
    - Defined risk (debit paid)
    - Max profit = Spread width - Net debit
    - Max loss = Net debit paid
    - Negative delta
    """
    
    name = "bear_put_spread"
    description = "Buy higher strike put, sell lower strike put"
    
    is_bullish = False
    is_bearish = True
    is_neutral = False
    is_volatility_play = False
    is_defined_risk = True
    
    suitable_regimes = ["TRENDING_DOWN"]
    
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
        **kwargs
    ) -> StrategyResult:
        """
        Build bear put spread strategy.
        
        Args:
            spread_width: Distance between buy and sell strikes
        """
        legs = []
        
        # Buy ATM or slightly ITM PE
        buy_strike = atm_strike
        sell_strike = atm_strike - spread_width
        
        # Buy higher strike PE
        legs.append(self._build_leg(
            underlying=underlying,
            strike=buy_strike,
            expiry=expiry,
            option_type="PE",
            action="BUY",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Sell lower strike PE
        legs.append(self._build_leg(
            underlying=underlying,
            strike=sell_strike,
            expiry=expiry,
            option_type="PE",
            action="SELL",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Calculate P&L parameters
        net_debit = legs[0].premium - legs[1].premium
        max_profit = spread_width * lot_size * quantity_lots - net_debit
        max_loss = net_debit
        
        # Breakeven = Buy strike - net debit per unit
        breakeven = buy_strike - (net_debit / (lot_size * quantity_lots))
        
        greeks = self.calculate_greeks(legs)
        
        return StrategyResult(
            name=self.name,
            underlying=underlying,
            expiry=expiry,
            legs=legs,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_levels=[breakeven],
            net_premium=-net_debit,
            net_delta=greeks["delta"],
            net_gamma=greeks["gamma"],
            net_theta=greeks["theta"],
            net_vega=greeks["vega"],
            is_defined_risk=True,
            margin_required=net_debit,
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
            intrinsic = max(0, leg.strike - spot_at_expiry)
            intrinsic_value = intrinsic * leg.total_quantity
            
            if leg.action == "SELL":
                pnl += premium - intrinsic_value
            else:
                pnl += intrinsic_value - premium
        
        return pnl


def get_bear_put_spread_strategy() -> BearPutSpreadStrategy:
    """Get bear put spread strategy instance."""
    return BearPutSpreadStrategy()
