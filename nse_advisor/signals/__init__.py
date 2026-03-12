"""
Signals package initialization.

Exports all signal computation functions.
"""

from nse_advisor.signals.engine import (
    SignalResult,
    AggregatedSignal,
    SignalEngine,
    get_signal_engine,
)
from nse_advisor.signals.oi_analysis import (
    OIAnalyzer,
    OIMetrics,
    get_oi_analyzer,
    compute_oi_signal,
)
from nse_advisor.signals.iv_analysis import (
    IVAnalyzer,
    IVMetrics,
    get_iv_analyzer,
    compute_iv_signal,
)
from nse_advisor.signals.max_pain import (
    MaxPainGEXAnalyzer,
    MaxPainGEXMetrics,
    get_max_pain_analyzer,
    compute_max_pain_signal,
)
from nse_advisor.signals.price_action import (
    PriceActionAnalyzer,
    PriceActionMetrics,
    get_price_action_analyzer,
    compute_price_action_signal,
)
from nse_advisor.signals.technicals import (
    TechnicalsAnalyzer,
    TechnicalMetrics,
    get_technicals_analyzer,
    compute_technicals_signal,
)
from nse_advisor.signals.global_cues import (
    GlobalCuesAnalyzer,
    GlobalCuesMetrics,
    get_global_cues_analyzer,
    compute_global_cues_signal,
)
from nse_advisor.signals.fii_dii import (
    FiiDiiAnalyzer,
    FiiDiiMetrics,
    get_fii_dii_analyzer,
    compute_fii_dii_signal,
)
from nse_advisor.signals.straddle_pricing import (
    StraddlePricingAnalyzer,
    StraddleMetrics,
    get_straddle_analyzer,
    compute_straddle_signal,
)
from nse_advisor.signals.news_scanner import (
    NewsEventsAnalyzer,
    NewsEventsMetrics,
    get_news_analyzer,
    compute_news_signal,
)
from nse_advisor.signals.greeks_signal import (
    GreeksAnalyzer,
    GreeksMetrics,
    get_greeks_analyzer,
    compute_greeks_signal,
)

__all__ = [
    # Engine
    "SignalResult",
    "AggregatedSignal",
    "SignalEngine",
    "get_signal_engine",
    # OI Analysis
    "OIAnalyzer",
    "OIMetrics",
    "get_oi_analyzer",
    "compute_oi_signal",
    # IV Analysis
    "IVAnalyzer",
    "IVMetrics",
    "get_iv_analyzer",
    "compute_iv_signal",
    # Max Pain & GEX
    "MaxPainGEXAnalyzer",
    "MaxPainGEXMetrics",
    "get_max_pain_analyzer",
    "compute_max_pain_signal",
    # Price Action
    "PriceActionAnalyzer",
    "PriceActionMetrics",
    "get_price_action_analyzer",
    "compute_price_action_signal",
    # Technicals
    "TechnicalsAnalyzer",
    "TechnicalMetrics",
    "get_technicals_analyzer",
    "compute_technicals_signal",
    # Global Cues
    "GlobalCuesAnalyzer",
    "GlobalCuesMetrics",
    "get_global_cues_analyzer",
    "compute_global_cues_signal",
    # FII/DII
    "FiiDiiAnalyzer",
    "FiiDiiMetrics",
    "get_fii_dii_analyzer",
    "compute_fii_dii_signal",
    # Straddle Pricing
    "StraddlePricingAnalyzer",
    "StraddleMetrics",
    "get_straddle_analyzer",
    "compute_straddle_signal",
    # News & Events
    "NewsEventsAnalyzer",
    "NewsEventsMetrics",
    "get_news_analyzer",
    "compute_news_signal",
    # Greeks
    "GreeksAnalyzer",
    "GreeksMetrics",
    "get_greeks_analyzer",
    "compute_greeks_signal",
]
