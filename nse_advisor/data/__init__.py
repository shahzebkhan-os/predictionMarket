"""
Data package initialization.
"""

from nse_advisor.data.nse_session import (
    NseSession,
    NseSessionError,
    get_nse_session,
)
from nse_advisor.data.nse_fetcher import (
    IndexData,
    OptionData,
    FiiDiiData,
    NseFetcher,
    get_nse_fetcher,
)
from nse_advisor.data.yfinance_fetcher import (
    OHLCVData,
    GlobalCues,
    YFinanceFetcher,
    get_yfinance_fetcher,
)
from nse_advisor.data.indmoney_client import (
    IndMoneyPosition,
    IndMoneyPortfolio,
    IndMoneyClient,
    get_indmoney_client,
    close_indmoney_client,
)

__all__ = [
    # NSE Session
    "NseSession",
    "NseSessionError",
    "get_nse_session",
    # NSE Fetcher
    "IndexData",
    "OptionData",
    "FiiDiiData",
    "NseFetcher",
    "get_nse_fetcher",
    # yfinance Fetcher
    "OHLCVData",
    "GlobalCues",
    "YFinanceFetcher",
    "get_yfinance_fetcher",
    # IndMoney Client
    "IndMoneyPosition",
    "IndMoneyPortfolio",
    "IndMoneyClient",
    "get_indmoney_client",
    "close_indmoney_client",
]
