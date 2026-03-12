"""Configuration management using Pydantic BaseSettings.

All timestamps in IST (Asia/Kolkata). Market hours: 09:15-15:30 IST Mon-Fri.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotMode(str, Enum):
    """Bot execution mode."""

    PAPER = "paper"
    LIVE = "live"


class LogFormat(str, Enum):
    """Logging format options."""

    JSON = "json"
    CONSOLE = "console"


class ProductType(str, Enum):
    """Kite product types."""

    NRML = "NRML"  # Normal (overnight positions)
    MIS = "MIS"  # Margin Intraday Square-off


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Bot Mode
    bot_mode: BotMode = Field(default=BotMode.PAPER, description="paper or live mode")

    # Kite Connect API
    kite_api_key: str = Field(default="", description="Kite Connect API key")
    kite_api_secret: SecretStr = Field(
        default=SecretStr(""), description="Kite Connect API secret"
    )
    kite_access_token: SecretStr = Field(
        default=SecretStr(""), description="Kite Connect access token (daily)"
    )

    # IndMoney API
    indmoney_bearer_token: SecretStr = Field(
        default=SecretStr(""), description="IndMoney Bearer token"
    )

    # Telegram Alerts
    telegram_bot_token: SecretStr = Field(
        default=SecretStr(""), description="Telegram bot token"
    )
    telegram_chat_id: str = Field(default="", description="Telegram chat ID for alerts")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./nse_bot.db",
        description="Database connection URL",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # Risk Parameters
    max_capital: float = Field(
        default=500000.0, description="Maximum capital allocation in INR", ge=0
    )
    max_loss_per_trade: float = Field(
        default=10000.0, description="Maximum loss per trade in INR", ge=0
    )
    max_daily_loss: float = Field(
        default=25000.0, description="Maximum daily loss limit in INR", ge=0
    )
    max_position_size_pct: float = Field(
        default=0.20, description="Maximum position size as % of capital", ge=0, le=1
    )
    intraday_loss_limit: float = Field(
        default=15000.0, description="Intraday loss limit for kill switch", ge=0
    )

    # Paper Trading Settings
    paper_initial_capital: float = Field(
        default=500000.0, description="Initial paper trading capital", ge=0
    )
    paper_brokerage_per_order: float = Field(
        default=20.0, description="Brokerage per order in INR", ge=0
    )
    paper_stt_rate: float = Field(
        default=0.000625, description="STT rate (0.0625%)", ge=0
    )

    # Trading Parameters
    default_product: ProductType = Field(
        default=ProductType.NRML, description="Default product type"
    )
    prefer_mis_for_expiry_day: bool = Field(
        default=True, description="Use MIS on expiry day"
    )
    sl_buffer_pct: float = Field(
        default=0.30, description="Stop loss buffer percentage", ge=0, le=1
    )

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )
    log_format: LogFormat = Field(
        default=LogFormat.JSON, description="Logging format"
    )

    # Market Timings (IST)
    market_open_hour: int = Field(default=9, ge=0, le=23)
    market_open_minute: int = Field(default=15, ge=0, le=59)
    market_close_hour: int = Field(default=15, ge=0, le=23)
    market_close_minute: int = Field(default=30, ge=0, le=59)

    # API Rate Limits
    kite_requests_per_second: int = Field(
        default=3, description="Kite API rate limit", ge=1
    )

    # Cache TTL
    tick_cache_ttl_seconds: int = Field(
        default=10, description="Tick data cache TTL in seconds", ge=1
    )

    @field_validator("bot_mode", mode="before")
    @classmethod
    def validate_bot_mode(cls, v: str | BotMode) -> BotMode:
        """Validate and convert bot mode."""
        if isinstance(v, BotMode):
            return v
        return BotMode(v.lower())

    @property
    def is_paper_mode(self) -> bool:
        """Check if running in paper trading mode."""
        return self.bot_mode == BotMode.PAPER

    @property
    def is_live_mode(self) -> bool:
        """Check if running in live trading mode."""
        return self.bot_mode == BotMode.LIVE


# Global settings instance
settings = Settings()
