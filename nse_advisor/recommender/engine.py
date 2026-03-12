"""
Trade Recommendation Engine.

Builds trade recommendations from signals and strategies.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Literal

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.market.regime import MarketRegime, RegimeClassification
from nse_advisor.signals.engine import AggregatedSignal
from nse_advisor.strategies.base_strategy import BaseStrategy, StrategyResult
from nse_advisor.strategies.short_straddle import get_short_straddle_strategy
from nse_advisor.strategies.iron_condor import get_iron_condor_strategy
from nse_advisor.strategies.bull_call_spread import get_bull_call_spread_strategy
from nse_advisor.strategies.bear_put_spread import get_bear_put_spread_strategy
from nse_advisor.strategies.long_straddle import get_long_straddle_strategy

logger = logging.getLogger(__name__)


@dataclass
class RecommendedLeg:
    """A recommended leg for a trade."""
    tradingsymbol: str
    expiry: date
    strike: float
    option_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    suggested_lots: int
    suggested_entry_price: float
    suggested_entry_range: tuple[float, float]  # (min, max)
    
    # Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


@dataclass
class TradeRecommendation:
    """Trade recommendation output."""
    recommendation_id: str
    generated_at: datetime
    underlying: str
    strategy_name: str
    regime: str
    
    # Signal data
    composite_score: float
    confidence: float
    direction: str
    
    # Legs
    legs: list[RecommendedLeg]
    
    # P&L parameters
    max_profit_inr: float
    max_loss_inr: float
    suggested_stop_loss_inr: float
    suggested_take_profit_inr: float
    breakeven_levels: list[float]
    
    # Signal breakdown
    individual_signal_scores: dict[str, dict]
    
    # Explanation
    reasoning: str
    expiry_note: str
    risk_warnings: list[str] = field(default_factory=list)
    urgency: Literal["WATCH", "ACT_NOW", "URGENT"] = "WATCH"


class RecommenderEngine:
    """
    Builds trade recommendations from signals and strategies.
    
    Workflow:
    1. Receive aggregated signals and regime classification
    2. Select appropriate strategy based on regime and signal direction
    3. Build strategy with current market data
    4. Size the position using Kelly criterion
    5. Generate recommendation with reasoning
    """
    
    # Strategy mapping by regime
    REGIME_STRATEGIES = {
        MarketRegime.RANGE_BOUND: ["iron_condor", "short_straddle"],
        MarketRegime.TRENDING_UP: ["bull_call_spread"],
        MarketRegime.TRENDING_DOWN: ["bear_put_spread"],
        MarketRegime.HIGH_VOLATILITY: ["long_straddle"],
    }
    
    def __init__(self) -> None:
        """Initialize recommender engine."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        
        # Strategy instances
        self._strategies: dict[str, BaseStrategy] = {
            "short_straddle": get_short_straddle_strategy(),
            "iron_condor": get_iron_condor_strategy(),
            "bull_call_spread": get_bull_call_spread_strategy(),
            "bear_put_spread": get_bear_put_spread_strategy(),
            "long_straddle": get_long_straddle_strategy(),
        }
    
    def build_recommendation(
        self,
        signal: AggregatedSignal,
        regime: RegimeClassification,
        chain: OptionChainSnapshot,
        lot_size: int,
        underlying: str = "NIFTY",
        expiry: date | None = None,
    ) -> TradeRecommendation | None:
        """
        Build trade recommendation from signal and regime.
        
        Args:
            signal: Aggregated signal with composite score
            regime: Current market regime
            chain: Option chain snapshot
            lot_size: Lot size for underlying
            underlying: Underlying symbol
            expiry: Expiry date (defaults to nearest)
            
        Returns:
            TradeRecommendation or None if not recommending
        """
        now = datetime.now(self._ist)
        
        # Check if should recommend
        if not signal.should_recommend:
            logger.debug(f"Signal rejected: {signal.rejection_reason}")
            return None
        
        # Select strategy
        strategy_name = self._select_strategy(signal, regime)
        if not strategy_name:
            logger.debug("No suitable strategy for current conditions")
            return None
        
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            logger.error(f"Strategy not found: {strategy_name}")
            return None
        
        # Get expiry
        if expiry is None:
            expiry = chain.expiry
        
        # Build chain data for strategy
        chain_data = self._build_chain_data(chain)
        
        # Calculate lots using sizer
        from nse_advisor.recommender.sizer import calculate_position_size
        quantity_lots = calculate_position_size(
            strategy=strategy,
            chain=chain,
            underlying=underlying,
        )
        
        # Build strategy
        try:
            result = strategy.build(
                underlying=underlying,
                spot_price=chain.spot_price,
                expiry=expiry,
                lot_size=lot_size,
                atm_strike=chain.get_atm_strike(),
                chain_data=chain_data,
                quantity_lots=quantity_lots,
            )
        except Exception as e:
            logger.error(f"Failed to build strategy: {e}")
            return None
        
        # Build recommendation
        legs = self._convert_legs(result)
        
        # Calculate stop loss and take profit
        stop_loss = abs(result.max_loss) * self._settings.stop_loss_pct_of_max_loss
        take_profit = abs(result.max_profit) * self._settings.take_profit_pct_of_max_profit
        
        # Build reasoning
        reasoning = self._build_reasoning(signal, regime, strategy_name)
        
        # Build expiry note
        dte = (expiry - date.today()).days
        expiry_note = self._build_expiry_note(dte)
        
        # Build risk warnings
        risk_warnings = self._build_risk_warnings(signal, regime, chain)
        
        # Determine urgency
        urgency = self._determine_urgency(signal, regime)
        
        return TradeRecommendation(
            recommendation_id=str(uuid.uuid4()),
            generated_at=now,
            underlying=underlying,
            strategy_name=strategy_name,
            regime=regime.regime.value,
            composite_score=signal.composite_score,
            confidence=signal.composite_confidence,
            direction=signal.direction,
            legs=legs,
            max_profit_inr=result.max_profit,
            max_loss_inr=result.max_loss,
            suggested_stop_loss_inr=stop_loss,
            suggested_take_profit_inr=take_profit,
            breakeven_levels=result.breakeven_levels,
            individual_signal_scores=signal.get_signal_breakdown(),
            reasoning=reasoning,
            expiry_note=expiry_note,
            risk_warnings=risk_warnings,
            urgency=urgency,
        )
    
    def _select_strategy(
        self,
        signal: AggregatedSignal,
        regime: RegimeClassification
    ) -> str | None:
        """Select appropriate strategy based on signal and regime."""
        available = self.REGIME_STRATEGIES.get(regime.regime, [])
        
        if not available:
            return None
        
        # For directional signals, pick accordingly
        if signal.is_bullish and "bull_call_spread" in available:
            return "bull_call_spread"
        if signal.is_bearish and "bear_put_spread" in available:
            return "bear_put_spread"
        
        # For range-bound, prefer iron condor over short straddle (lower risk)
        if regime.regime == MarketRegime.RANGE_BOUND:
            return "iron_condor"
        
        # Default to first available
        return available[0] if available else None
    
    def _build_chain_data(self, chain: OptionChainSnapshot) -> dict:
        """Convert option chain to strategy-friendly format."""
        chain_data = {}
        
        for strike in chain.strikes:
            # CE data
            ce_key = f"{strike.strike_price}_CE"
            chain_data[ce_key] = {
                "ltp": strike.ce_ltp,
                "delta": strike.ce_delta,
                "gamma": strike.ce_gamma,
                "theta": strike.ce_theta,
                "vega": strike.ce_vega,
            }
            
            # PE data
            pe_key = f"{strike.strike_price}_PE"
            chain_data[pe_key] = {
                "ltp": strike.pe_ltp,
                "delta": strike.pe_delta,
                "gamma": strike.pe_gamma,
                "theta": strike.pe_theta,
                "vega": strike.pe_vega,
            }
        
        return chain_data
    
    def _convert_legs(self, result: StrategyResult) -> list[RecommendedLeg]:
        """Convert strategy legs to recommended legs."""
        return [
            RecommendedLeg(
                tradingsymbol=leg.tradingsymbol,
                expiry=leg.expiry,
                strike=leg.strike,
                option_type=leg.option_type,
                action=leg.action,
                suggested_lots=leg.quantity_lots,
                suggested_entry_price=leg.entry_price,
                suggested_entry_range=(
                    leg.entry_price * 0.98,
                    leg.entry_price * 1.02
                ),
                delta=leg.delta,
                gamma=leg.gamma,
                theta=leg.theta,
                vega=leg.vega,
            )
            for leg in result.legs
        ]
    
    def _build_reasoning(
        self,
        signal: AggregatedSignal,
        regime: RegimeClassification,
        strategy_name: str
    ) -> str:
        """Build human-readable reasoning for recommendation."""
        parts = []
        
        # Regime context
        parts.append(f"Market is {regime.regime.value.replace('_', ' ').lower()}.")
        
        # Signal summary
        if signal.is_bullish:
            parts.append(f"Signals are bullish (score: {signal.composite_score:.2f}).")
        elif signal.is_bearish:
            parts.append(f"Signals are bearish (score: {signal.composite_score:.2f}).")
        else:
            parts.append(f"Signals are neutral (score: {signal.composite_score:.2f}).")
        
        # Strategy explanation
        strategy_desc = {
            "iron_condor": "Iron Condor captures theta decay in range-bound conditions.",
            "short_straddle": "Short Straddle collects premium expecting low movement.",
            "bull_call_spread": "Bull Call Spread profits from moderate upside move.",
            "bear_put_spread": "Bear Put Spread profits from moderate downside move.",
            "long_straddle": "Long Straddle profits from large move in either direction.",
        }
        parts.append(strategy_desc.get(strategy_name, ""))
        
        return " ".join(parts)
    
    def _build_expiry_note(self, dte: int) -> str:
        """Build expiry-related note."""
        if dte == 0:
            return "⚠️ EXPIRY TODAY - Exit before 15:20"
        elif dte == 1:
            return "⚠️ 1 day to expiry - Use wider stops, rapid theta decay"
        elif dte <= 3:
            return f"{dte} days to expiry - Gamma risk elevated"
        else:
            return f"{dte} days to expiry"
    
    def _build_risk_warnings(
        self,
        signal: AggregatedSignal,
        regime: RegimeClassification,
        chain: OptionChainSnapshot
    ) -> list[str]:
        """Build risk warnings for recommendation."""
        warnings = []
        
        # Regime-based warnings
        if regime.regime == MarketRegime.HIGH_VOLATILITY:
            warnings.append("High volatility environment - reduce position size")
        
        # Confidence warning
        if signal.composite_confidence < 0.7:
            warnings.append(f"Moderate confidence ({signal.composite_confidence:.0%})")
        
        # Check for event blackout in signals
        for name, sig_data in signal.signals.items():
            if "blackout" in sig_data.reason.lower():
                warnings.append("Event blackout period active")
                break
        
        return warnings
    
    def _determine_urgency(
        self,
        signal: AggregatedSignal,
        regime: RegimeClassification
    ) -> Literal["WATCH", "ACT_NOW", "URGENT"]:
        """Determine urgency level for recommendation."""
        if abs(signal.composite_score) >= 0.7 and signal.composite_confidence >= 0.8:
            return "URGENT"
        elif abs(signal.composite_score) >= 0.55:
            return "ACT_NOW"
        else:
            return "WATCH"


# Global instance
_recommender_engine: RecommenderEngine | None = None


def get_recommender_engine() -> RecommenderEngine:
    """Get or create global recommender engine."""
    global _recommender_engine
    if _recommender_engine is None:
        _recommender_engine = RecommenderEngine()
    return _recommender_engine
