"""
Bull Call Spread Strategy.

Buy ATM/ITM Call + Sell OTM Call.
"""

from __future__ import annotations

from datetime import date

from nse_advisor.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyResult,
)


class BullCallSpreadStrategy(BaseStrategy):
    """
    Bull Call Spread: Buy lower strike CE + Sell higher strike CE.
    
    Best when:
    - Moderately bullish outlook
    - Want to reduce cost of long call
    - Trending up market
    
    Characteristics:
    - Defined risk (debit paid)
    - Max profit = Spread width - Net debit
    - Max loss = Net debit paid
    - Positive delta
    """
    
    name = "bull_call_spread"
    description = "Buy lower strike call, sell higher strike call"
    
    is_bullish = True
    is_bearish = False
    is_neutral = False
    is_volatility_play = False
    is_defined_risk = True
    
    suitable_regimes = ["TRENDING_UP"]
    
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
        Build bull call spread strategy.
        
        Args:
            spread_width: Distance between buy and sell strikes
        """
        legs = []
        
        # Buy ATM or slightly ITM CE
        buy_strike = atm_strike
        sell_strike = atm_strike + spread_width
        
        # Buy lower strike CE
        legs.append(self._build_leg(
            underlying=underlying,
            strike=buy_strike,
            expiry=expiry,
            option_type="CE",
            action="BUY",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Sell higher strike CE
        legs.append(self._build_leg(
            underlying=underlying,
            strike=sell_strike,
            expiry=expiry,
            option_type="CE",
            action="SELL",
            quantity_lots=quantity_lots,
            lot_size=lot_size,
            chain_data=chain_data,
        ))
        
        # Calculate P&L parameters
        net_debit = legs[0].premium - legs[1].premium
        max_profit = spread_width * lot_size * quantity_lots - net_debit
        max_loss = net_debit
        
        # Breakeven = Buy strike + net debit per unit
        breakeven = buy_strike + (net_debit / (lot_size * quantity_lots))
        
        greeks = self.calculate_greeks(legs)
        
        return StrategyResult(
            name=self.name,
            underlying=underlying,
            expiry=expiry,
            legs=legs,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_levels=[breakeven],
            net_premium=-net_debit,  # Negative because debit
            net_delta=greeks["delta"],
            net_gamma=greeks["gamma"],
            net_theta=greeks["theta"],
            net_vega=greeks["vega"],
            is_defined_risk=True,
            margin_required=net_debit,  # Debit spread, no additional margin
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
            intrinsic = max(0, spot_at_expiry - leg.strike)
            intrinsic_value = intrinsic * leg.total_quantity
            
            if leg.action == "SELL":
                pnl += premium - intrinsic_value
            else:
                pnl += intrinsic_value - premium
        
        return pnl


def get_bull_call_spread_strategy() -> BullCallSpreadStrategy:
    """Get bull call spread strategy instance."""
    return BullCallSpreadStrategy()
