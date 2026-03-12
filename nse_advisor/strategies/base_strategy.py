"""
Base Strategy.

Abstract base class for all option strategies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from zoneinfo import ZoneInfo


@dataclass
class StrategyLeg:
    """A single leg of a strategy."""
    tradingsymbol: str
    underlying: str
    strike: float
    expiry: date
    option_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    quantity_lots: int
    entry_price: float
    lot_size: int
    
    # Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    
    @property
    def total_quantity(self) -> int:
        """Total quantity in units."""
        return self.quantity_lots * self.lot_size
    
    @property
    def premium(self) -> float:
        """Total premium for this leg."""
        return self.entry_price * self.total_quantity
    
    @property
    def greeks_multiplier(self) -> int:
        """Multiplier for Greeks (1 for BUY, -1 for SELL)."""
        return 1 if self.action == "BUY" else -1


@dataclass
class StrategyResult:
    """Result of building a strategy."""
    name: str
    underlying: str
    expiry: date
    legs: list[StrategyLeg]
    
    # P&L parameters
    max_profit: float
    max_loss: float
    breakeven_levels: list[float]
    
    # Premium
    net_premium: float  # Positive = credit, Negative = debit
    
    # Aggregated Greeks
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    
    # Risk classification
    is_defined_risk: bool = True
    margin_required: float = 0.0


class BaseStrategy(ABC):
    """
    Abstract base class for option strategies.
    
    All strategies must implement:
    - build(): Create strategy legs from current market data
    - calculate_payoff(): Calculate P&L at expiry for a given spot
    """
    
    name: str = "base_strategy"
    description: str = "Base strategy"
    
    # Strategy characteristics
    is_bullish: bool = False
    is_bearish: bool = False
    is_neutral: bool = False
    is_volatility_play: bool = False
    is_defined_risk: bool = True
    
    # Suitable regimes
    suitable_regimes: list[str] = []
    
    def __init__(self) -> None:
        """Initialize strategy."""
        self._ist = ZoneInfo("Asia/Kolkata")
    
    @abstractmethod
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
        """
        Build strategy from market data.
        
        Args:
            underlying: Underlying symbol
            spot_price: Current spot price
            expiry: Option expiry date
            lot_size: Lot size for underlying
            atm_strike: ATM strike price
            chain_data: Option chain data (prices, Greeks)
            quantity_lots: Number of lots per leg
            **kwargs: Strategy-specific parameters
            
        Returns:
            StrategyResult with all legs and P&L parameters
        """
        pass
    
    @abstractmethod
    def calculate_payoff(
        self,
        legs: list[StrategyLeg],
        spot_at_expiry: float
    ) -> float:
        """
        Calculate P&L at expiry for given spot.
        
        Args:
            legs: Strategy legs
            spot_at_expiry: Spot price at expiry
            
        Returns:
            P&L in INR
        """
        pass
    
    def calculate_greeks(self, legs: list[StrategyLeg]) -> dict[str, float]:
        """
        Calculate net Greeks for strategy.
        
        Returns:
            Dictionary with net delta, gamma, theta, vega
        """
        net_delta = 0.0
        net_gamma = 0.0
        net_theta = 0.0
        net_vega = 0.0
        
        for leg in legs:
            multiplier = leg.greeks_multiplier * leg.total_quantity
            net_delta += leg.delta * multiplier
            net_gamma += leg.gamma * multiplier
            net_theta += leg.theta * multiplier
            net_vega += leg.vega * multiplier
        
        return {
            "delta": net_delta,
            "gamma": net_gamma,
            "theta": net_theta,
            "vega": net_vega,
        }
    
    def _get_strike_price(
        self,
        chain_data: dict,
        strike: float,
        option_type: Literal["CE", "PE"]
    ) -> float:
        """Get LTP for a strike from chain data."""
        key = f"{strike}_{option_type}"
        if key in chain_data:
            return chain_data[key].get("ltp", 0.0)
        return 0.0
    
    def _get_strike_greeks(
        self,
        chain_data: dict,
        strike: float,
        option_type: Literal["CE", "PE"]
    ) -> dict[str, float]:
        """Get Greeks for a strike from chain data."""
        key = f"{strike}_{option_type}"
        if key in chain_data:
            return {
                "delta": chain_data[key].get("delta", 0.0),
                "gamma": chain_data[key].get("gamma", 0.0),
                "theta": chain_data[key].get("theta", 0.0),
                "vega": chain_data[key].get("vega", 0.0),
            }
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    
    def _build_leg(
        self,
        underlying: str,
        strike: float,
        expiry: date,
        option_type: Literal["CE", "PE"],
        action: Literal["BUY", "SELL"],
        quantity_lots: int,
        lot_size: int,
        chain_data: dict
    ) -> StrategyLeg:
        """Helper to build a strategy leg."""
        price = self._get_strike_price(chain_data, strike, option_type)
        greeks = self._get_strike_greeks(chain_data, strike, option_type)
        
        # Generate tradingsymbol
        year_short = expiry.strftime("%y")
        month = expiry.strftime("%b").upper()
        tradingsymbol = f"{underlying}{year_short}{month}{int(strike)}{option_type}"
        
        return StrategyLeg(
            tradingsymbol=tradingsymbol,
            underlying=underlying,
            strike=strike,
            expiry=expiry,
            option_type=option_type,
            action=action,
            quantity_lots=quantity_lots,
            entry_price=price,
            lot_size=lot_size,
            delta=greeks["delta"],
            gamma=greeks["gamma"],
            theta=greeks["theta"],
            vega=greeks["vega"],
        )
