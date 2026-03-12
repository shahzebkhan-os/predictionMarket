"""
NSE Options Signal Advisor.

A signal-only options trading advisor for NSE indices.
No broker execution API - generates trade recommendations
displayed on dashboard and sent via Telegram.

Usage:
    python -m nse_advisor.main
    
Dashboard:
    streamlit run nse_advisor/dashboard/streamlit_app.py
"""

__version__ = "1.0.0"
__author__ = "NSE Advisor Team"

from nse_advisor.config import Settings, get_settings

__all__ = [
    "Settings",
    "get_settings",
    "__version__",
]
