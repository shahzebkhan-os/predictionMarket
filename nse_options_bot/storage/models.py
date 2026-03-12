"""SQLAlchemy ORM models for storage.

Database models for trades, signals, events, etc.
"""

from __future__ import annotations

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def utc_now() -> datetime:
    """Get current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Trade(Base):
    """Trade record."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(50), unique=True, nullable=False, index=True)

    # Strategy info
    strategy_type = Column(String(50), nullable=False)
    underlying = Column(String(20), nullable=False)
    expiry_date = Column(Date, nullable=False)

    # Entry
    entry_time = Column(DateTime(timezone=True), nullable=False)
    entry_spot_price = Column(Numeric(12, 2), nullable=False)
    entry_signals = Column(JSON)
    entry_regime = Column(String(30))

    # Exit
    exit_time = Column(DateTime(timezone=True))
    exit_spot_price = Column(Numeric(12, 2))
    exit_reason = Column(String(50))
    exit_notes = Column(Text)

    # P&L
    gross_pnl = Column(Numeric(12, 2), default=0)
    net_pnl = Column(Numeric(12, 2), default=0)
    commissions = Column(Numeric(10, 2), default=0)
    slippage = Column(Numeric(10, 2), default=0)

    # Greeks at entry
    entry_delta = Column(Float)
    entry_gamma = Column(Float)
    entry_theta = Column(Float)
    entry_vega = Column(Float)
    entry_iv = Column(Float)

    # Position
    capital_allocated = Column(Numeric(12, 2))
    max_loss_amount = Column(Numeric(12, 2))
    lots = Column(Integer)

    # Status
    status = Column(String(20), default="OPEN")

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), onupdate=utc_now)

    # Relationships
    legs = relationship("TradeLeg", back_populates="trade", cascade="all, delete-orphan")
    events = relationship("TradeEvent", back_populates="trade", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_trades_entry_time", "entry_time"),
        Index("idx_trades_underlying", "underlying"),
        Index("idx_trades_status", "status"),
    )


class TradeLeg(Base):
    """Trade leg record."""

    __tablename__ = "trade_legs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    leg_id = Column(String(50), nullable=False)

    # Instrument
    tradingsymbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NFO")
    strike = Column(Numeric(10, 2), nullable=False)
    option_type = Column(String(2), nullable=False)  # CE or PE

    # Position
    is_long = Column(Boolean, nullable=False)
    quantity = Column(Integer, nullable=False)
    lot_size = Column(Integer, nullable=False)

    # Prices
    entry_price = Column(Numeric(10, 2))
    exit_price = Column(Numeric(10, 2))
    avg_price = Column(Numeric(10, 2))

    # Greeks at entry
    delta = Column(Float)
    gamma = Column(Float)
    theta = Column(Float)
    vega = Column(Float)
    iv = Column(Float)

    # Orders
    entry_order_id = Column(String(50))
    exit_order_id = Column(String(50))
    sl_order_id = Column(String(50))

    # P&L
    pnl = Column(Numeric(12, 2), default=0)

    # Status
    status = Column(String(20), default="PENDING")

    # Relationship
    trade = relationship("Trade", back_populates="legs")

    __table_args__ = (
        Index("idx_trade_legs_trade_id", "trade_id"),
        Index("idx_trade_legs_symbol", "tradingsymbol"),
    )


class TradeEvent(Base):
    """Trade event log."""

    __tablename__ = "trade_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)

    event_type = Column(String(50), nullable=False)
    event_time = Column(DateTime(timezone=True), nullable=False)
    event_data = Column(JSON)

    # Context
    spot_price = Column(Numeric(12, 2))
    portfolio_pnl = Column(Numeric(12, 2))

    # Relationship
    trade = relationship("Trade", back_populates="events")

    __table_args__ = (
        Index("idx_trade_events_trade_id", "trade_id"),
        Index("idx_trade_events_time", "event_time"),
    )


class Signal(Base):
    """Signal snapshot."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)

    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    underlying = Column(String(20), nullable=False)

    # Signal values
    signal_type = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)
    confidence = Column(Float)
    reason = Column(Text)
    components = Column(JSON)

    # Market context
    spot_price = Column(Numeric(12, 2))
    vix = Column(Float)
    regime = Column(String(30))

    __table_args__ = (
        Index("idx_signals_underlying_time", "underlying", "timestamp"),
    )


class DailyStats(Base):
    """Daily statistics."""

    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False, index=True)

    # Trades
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)

    # P&L
    gross_pnl = Column(Numeric(12, 2), default=0)
    net_pnl = Column(Numeric(12, 2), default=0)
    commissions = Column(Numeric(10, 2), default=0)

    # Greeks P&L
    delta_pnl = Column(Numeric(12, 2), default=0)
    theta_pnl = Column(Numeric(12, 2), default=0)
    vega_pnl = Column(Numeric(12, 2), default=0)

    # Market
    open_price = Column(Numeric(12, 2))
    close_price = Column(Numeric(12, 2))
    high_price = Column(Numeric(12, 2))
    low_price = Column(Numeric(12, 2))
    vix_open = Column(Float)
    vix_close = Column(Float)

    # Strategy breakdown (JSON)
    strategy_pnl = Column(JSON)
    strategy_counts = Column(JSON)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)


class OptionChainCache(Base):
    """Option chain cache."""

    __tablename__ = "option_chain_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    underlying = Column(String(20), nullable=False)
    expiry_date = Column(Date, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)

    # Chain data (JSON)
    chain_data = Column(JSON, nullable=False)

    # Summary
    atm_strike = Column(Numeric(10, 2))
    pcr = Column(Float)
    max_pain = Column(Numeric(10, 2))
    total_ce_oi = Column(Integer)
    total_pe_oi = Column(Integer)

    __table_args__ = (
        Index("idx_chain_underlying_expiry", "underlying", "expiry_date"),
        Index("idx_chain_timestamp", "timestamp"),
    )


class Order(Base):
    """Order record."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), unique=True, nullable=False, index=True)

    # Instrument
    tradingsymbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NFO")

    # Order details
    transaction_type = Column(String(4), nullable=False)  # BUY or SELL
    order_type = Column(String(10), nullable=False)  # LIMIT, MARKET, SL-M
    product = Column(String(10), nullable=False)  # NRML, MIS

    # Quantities
    quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, default=0)
    pending_quantity = Column(Integer)

    # Prices
    price = Column(Numeric(10, 2))
    trigger_price = Column(Numeric(10, 2))
    average_price = Column(Numeric(10, 2))

    # Status
    status = Column(String(20), nullable=False)
    status_message = Column(Text)

    # Timestamps
    placed_at = Column(DateTime(timezone=True))
    filled_at = Column(DateTime(timezone=True))

    # Related trade
    trade_leg_id = Column(Integer, ForeignKey("trade_legs.id"))

    # Metadata
    tag = Column(String(50))

    __table_args__ = (
        Index("idx_orders_status", "status"),
        Index("idx_orders_symbol", "tradingsymbol"),
    )


class SystemConfig(Base):
    """System configuration storage."""

    __tablename__ = "system_config"

    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=utc_now)
    description = Column(Text)
