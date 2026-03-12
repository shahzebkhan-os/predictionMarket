"""
Strategies package initialization.
"""

from nse_advisor.strategies.base_strategy import (
    BaseStrategy,
    StrategyLeg,
    StrategyResult,
)
from nse_advisor.strategies.short_straddle import (
    ShortStraddleStrategy,
    get_short_straddle_strategy,
)
from nse_advisor.strategies.iron_condor import (
    IronCondorStrategy,
    get_iron_condor_strategy,
)
from nse_advisor.strategies.bull_call_spread import (
    BullCallSpreadStrategy,
    get_bull_call_spread_strategy,
)
from nse_advisor.strategies.bear_put_spread import (
    BearPutSpreadStrategy,
    get_bear_put_spread_strategy,
)
from nse_advisor.strategies.long_straddle import (
    LongStraddleStrategy,
    get_long_straddle_strategy,
)

__all__ = [
    # Base
    "BaseStrategy",
    "StrategyLeg",
    "StrategyResult",
    # Strategies
    "ShortStraddleStrategy",
    "get_short_straddle_strategy",
    "IronCondorStrategy",
    "get_iron_condor_strategy",
    "BullCallSpreadStrategy",
    "get_bull_call_spread_strategy",
    "BearPutSpreadStrategy",
    "get_bear_put_spread_strategy",
    "LongStraddleStrategy",
    "get_long_straddle_strategy",
]
