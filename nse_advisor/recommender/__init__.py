"""
Recommender package initialization.
"""

from nse_advisor.recommender.engine import (
    RecommendedLeg,
    TradeRecommendation,
    RecommenderEngine,
    get_recommender_engine,
)
from nse_advisor.recommender.sizer import (
    calculate_position_size,
    calculate_kelly_fraction,
    calculate_margin_requirement,
)
from nse_advisor.recommender.rollover import (
    RolloverLeg,
    RolloverSuggestion,
    RolloverManager,
    get_rollover_manager,
)

__all__ = [
    # Engine
    "RecommendedLeg",
    "TradeRecommendation",
    "RecommenderEngine",
    "get_recommender_engine",
    # Sizer
    "calculate_position_size",
    "calculate_kelly_fraction",
    "calculate_margin_requirement",
    # Rollover
    "RolloverLeg",
    "RolloverSuggestion",
    "RolloverManager",
    "get_rollover_manager",
]
