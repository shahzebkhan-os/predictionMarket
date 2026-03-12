"""
NSE Options Signal Advisor Configuration.

All configuration via Pydantic BaseSettings with .env support.
All timestamps in IST (Asia/Kolkata). Market hours: 09:15-15:30 IST Mon-Fri.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # ===== IndMoney =====
    indmoney_bearer_token: str = Field(
        default="",
        description="Bearer token for IndMoney API (read-only portfolio sync)"
    )
    
    # ===== Capital =====
    paper_capital: float = Field(
        default=500000.0,
        description="Virtual capital for paper trading (INR)"
    )
    actual_capital: float = Field(
        default=0.0,
        description="Actual capital when tracking real trades (INR)"
    )
    
    # ===== Instruments =====
    primary_underlying: str = Field(
        default="NIFTY",
        description="Primary underlying for signals"
    )
    secondary_underlying: str = Field(
        default="BANKNIFTY",
        description="Secondary underlying for signals"
    )
    scan_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Interval between signal scans"
    )
    chain_stale_seconds: int = Field(
        default=10,
        ge=5,
        le=60,
        description="Option chain staleness threshold"
    )
    nse_session_refresh_minutes: int = Field(
        default=25,
        ge=10,
        le=30,
        description="NSE session cookie refresh interval"
    )
    ticker_stale_seconds: int = Field(
        default=15,
        ge=5,
        le=60,
        description="Alert if no price update for this long"
    )
    circuit_breaker_flat_minutes: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Minutes of no price change before halt suspected"
    )
    
    # ===== NSE Session =====
    nse_max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum retries for NSE API calls"
    )
    nse_rate_limit_backoff_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Backoff time after NSE 429 response"
    )
    nse_ip_ban_backoff_seconds: int = Field(
        default=3600,
        ge=1800,
        le=21600,
        description="Backoff time after NSE IP ban (1-6 hours)"
    )
    nse_health_check_interval_seconds: int = Field(
        default=300,
        ge=60,
        le=900,
        description="NSE session health check interval (5 min)"
    )
    use_playwright_fallback: bool = Field(
        default=False,
        description="Use Playwright for NSE fetches (slower, for IP bans)"
    )
    
    # ===== Paper Trading =====
    paper_trading: bool = Field(
        default=True,
        description="True = paper mode, False = live tracking"
    )
    
    # ===== Options Math =====
    rfr_rate: float = Field(
        default=0.068,
        ge=0.0,
        le=0.20,
        description="Risk-free rate (India 91-day T-bill, ~6.8%)"
    )
    nifty_div_yield: float = Field(
        default=0.012,
        ge=0.0,
        le=0.10,
        description="NIFTY dividend yield (~1.2%)"
    )
    banknifty_div_yield: float = Field(
        default=0.0,
        ge=0.0,
        le=0.10,
        description="BANKNIFTY dividend yield (0%)"
    )
    
    # ===== Risk Thresholds =====
    max_loss_per_trade_inr: float = Field(
        default=3000.0,
        ge=500.0,
        le=50000.0,
        description="Maximum loss per trade (INR)"
    )
    max_open_trades: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum concurrent open trades"
    )
    max_lots_per_trade_nifty: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum lots per NIFTY trade"
    )
    max_lots_per_trade_banknifty: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Maximum lots per BANKNIFTY trade"
    )
    kelly_fraction: float = Field(
        default=0.5,
        ge=0.1,
        le=1.0,
        description="Kelly criterion fraction"
    )
    max_undefined_risk_pct: float = Field(
        default=0.10,
        ge=0.01,
        le=0.50,
        description="Maximum undefined risk as % of capital"
    )
    
    # ===== Signal Thresholds =====
    min_composite_score: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="Minimum composite score for recommendation"
    )
    min_confidence: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for recommendation"
    )
    min_ivr_for_selling: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        description="Minimum IV Rank for selling premium"
    )
    max_ivr_for_buying: float = Field(
        default=35.0,
        ge=0.0,
        le=100.0,
        description="Maximum IV Rank for buying premium"
    )
    min_oi_lots: int = Field(
        default=500,
        ge=100,
        le=10000,
        description="Minimum OI in lots for valid strike"
    )
    
    # ===== Exit Thresholds =====
    take_profit_pct_of_max_profit: float = Field(
        default=0.75,
        ge=0.1,
        le=1.0,
        description="Take profit as % of max profit"
    )
    stop_loss_pct_of_max_loss: float = Field(
        default=1.5,
        ge=1.0,
        le=3.0,
        description="Stop loss as multiple of max loss"
    )
    theta_burn_limit_inr_per_day: float = Field(
        default=2000.0,
        ge=100.0,
        le=10000.0,
        description="Daily theta burn limit (INR)"
    )
    delta_hedge_threshold: float = Field(
        default=0.30,
        ge=0.1,
        le=0.8,
        description="Delta threshold for hedge alert"
    )
    vega_threshold: float = Field(
        default=500.0,
        ge=100.0,
        le=5000.0,
        description="Portfolio vega threshold"
    )
    
    # ===== Market Timing (IST) =====
    no_new_signals_after: str = Field(
        default="15:00",
        description="No new signals after this time (HH:MM IST)"
    )
    timezone: str = Field(
        default="Asia/Kolkata",
        description="Timezone for all timestamps"
    )
    market_open_time: str = Field(
        default="09:15",
        description="Market open time (HH:MM IST)"
    )
    market_close_time: str = Field(
        default="15:30",
        description="Market close time (HH:MM IST)"
    )
    
    # ===== Alerts =====
    telegram_bot_token: str = Field(
        default="",
        description="Telegram Bot API token"
    )
    telegram_chat_id: str = Field(
        default="",
        description="Telegram chat ID for alerts"
    )
    
    # ===== Database =====
    database_url: str = Field(
        default="sqlite+aiosqlite:///nse_advisor.db",
        description="Database connection URL"
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for caching"
    )
    
    # ===== Logging =====
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level"
    )
    
    # ===== Regime Signal Weights =====
    # Range-bound regime weights
    weight_oi_range: float = 0.20
    weight_iv_range: float = 0.20
    weight_max_pain_range: float = 0.15
    weight_straddle_range: float = 0.15
    weight_greeks_range: float = 0.10
    weight_price_action_range: float = 0.08
    weight_technicals_range: float = 0.05
    weight_vix_range: float = 0.07
    
    # Trending regime weights
    weight_price_action_trend: float = 0.25
    weight_technicals_trend: float = 0.20
    weight_global_trend: float = 0.15
    weight_oi_trend: float = 0.15
    weight_fii_trend: float = 0.10
    weight_vix_trend: float = 0.08
    weight_iv_trend: float = 0.07
    
    # High volatility regime weights
    weight_vix_highvol: float = 0.25
    weight_news_highvol: float = 0.20
    weight_iv_highvol: float = 0.20
    weight_straddle_highvol: float = 0.15
    weight_greeks_highvol: float = 0.10
    weight_oi_highvol: float = 0.10
    
    @field_validator("no_new_signals_after", "market_open_time", "market_close_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Validate time format is HH:MM."""
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("Time must be in HH:MM format")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid time value")
        return v
    
    def get_div_yield(self, underlying: str) -> float:
        """Get dividend yield for an underlying."""
        if underlying.upper() == "NIFTY":
            return self.nifty_div_yield
        elif underlying.upper() == "BANKNIFTY":
            return self.banknifty_div_yield
        return 0.0
    
    def get_max_lots(self, underlying: str) -> int:
        """Get maximum lots for an underlying."""
        if underlying.upper() == "NIFTY":
            return self.max_lots_per_trade_nifty
        elif underlying.upper() == "BANKNIFTY":
            return self.max_lots_per_trade_banknifty
        return self.max_lots_per_trade_nifty


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
