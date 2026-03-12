"""
Database Models.

SQLAlchemy ORM models for persisting data.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Date,
    JSON,
    ForeignKey,
    Text,
    Enum as SQLEnum,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Signal(Base):
    """Persisted signal data."""
    __tablename__ = "signals"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timestamp = Column(DateTime, nullable=False, index=True)
    underlying = Column(String(20), nullable=False, index=True)
    
    # Signal data
    name = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    reason = Column(Text)
    
    # Metadata
    regime = Column(String(20))
    spot_price = Column(Float)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class Recommendation(Base):
    """Persisted trade recommendation."""
    __tablename__ = "recommendations"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    generated_at = Column(DateTime, nullable=False, index=True)
    underlying = Column(String(20), nullable=False, index=True)
    
    # Strategy
    strategy_name = Column(String(50), nullable=False)
    regime = Column(String(20))
    
    # Signal data
    composite_score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    direction = Column(String(10))
    
    # P&L parameters
    max_profit_inr = Column(Float)
    max_loss_inr = Column(Float)
    stop_loss_inr = Column(Float)
    take_profit_inr = Column(Float)
    
    # Legs (JSON)
    legs = Column(JSON)
    breakeven_levels = Column(JSON)
    
    # Individual signals (JSON)
    signal_scores = Column(JSON)
    
    # Explanation
    reasoning = Column(Text)
    urgency = Column(String(20))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to trades
    trades = relationship("Trade", back_populates="recommendation")


class Trade(Base):
    """Persisted trade."""
    __tablename__ = "trades"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recommendation_id = Column(String(36), ForeignKey("recommendations.id"), nullable=True)
    
    # Trade details
    underlying = Column(String(20), nullable=False, index=True)
    strategy_name = Column(String(50), nullable=False)
    expiry = Column(Date, nullable=False)
    
    # Entry
    entry_time = Column(DateTime, nullable=False, index=True)
    regime_at_entry = Column(String(20))
    signal_scores_at_entry = Column(JSON)
    dte_at_entry = Column(Integer)
    
    # P&L limits
    max_profit = Column(Float)
    max_loss = Column(Float)
    stop_loss_inr = Column(Float)
    take_profit_inr = Column(Float)
    
    # Legs (JSON)
    legs = Column(JSON)
    
    # Status
    status = Column(String(20), default="PAPER")
    paper_mode = Column(Boolean, default=True)
    
    # Exit
    exit_time = Column(DateTime, nullable=True)
    exit_reason = Column(Text)
    realized_pnl = Column(Float)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    recommendation = relationship("Recommendation", back_populates="trades")
    postmortem = relationship("TradePostmortemRecord", back_populates="trade", uselist=False)


class TradePostmortemRecord(Base):
    """Persisted trade postmortem."""
    __tablename__ = "trade_postmortems"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    trade_id = Column(String(36), ForeignKey("trades.id"), nullable=False, unique=True)
    
    # P&L
    realized_pnl_inr = Column(Float)
    max_adverse_excursion = Column(Float)
    max_favorable_excursion = Column(Float)
    
    # Exit quality
    exit_quality_score = Column(Float)
    
    # Signal accuracy
    signal_accuracy = Column(JSON)
    
    # Greeks attribution
    delta_pnl = Column(Float)
    theta_pnl = Column(Float)
    vega_pnl = Column(Float)
    gamma_pnl = Column(Float)
    residual_pnl = Column(Float)
    
    # Verdict
    verdict = Column(String(50))
    verdict_reason = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    trade = relationship("Trade", back_populates="postmortem")


class OptionChainRecord(Base):
    """Persisted option chain snapshot (for backtesting)."""
    __tablename__ = "option_chain_snapshots"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timestamp = Column(DateTime, nullable=False, index=True)
    underlying = Column(String(20), nullable=False, index=True)
    expiry = Column(Date, nullable=False)
    
    # Spot
    spot_price = Column(Float, nullable=False)
    
    # Chain data (JSON - strikes with all data)
    chain_data = Column(JSON, nullable=False)
    
    # Metadata
    pcr = Column(Float)
    atm_strike = Column(Float)
    max_pain = Column(Float)
    atm_iv = Column(Float)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class IVHistory(Base):
    """Historical IV data for IVR/IVP calculation."""
    __tablename__ = "iv_history"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    date = Column(Date, nullable=False, index=True)
    underlying = Column(String(20), nullable=False, index=True)
    
    # IV values
    atm_iv = Column(Float, nullable=False)
    iv_high = Column(Float)
    iv_low = Column(Float)
    vix = Column(Float)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class DailyReport(Base):
    """Daily performance report."""
    __tablename__ = "daily_reports"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_date = Column(Date, nullable=False, unique=True, index=True)
    
    # Stats
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    win_rate = Column(Float)
    
    total_pnl = Column(Float)
    paper_pnl = Column(Float)
    actual_pnl = Column(Float)
    
    # Breakdown (JSON)
    strategy_stats = Column(JSON)
    regime_stats = Column(JSON)
    signal_accuracy = Column(JSON)
    
    # Greeks
    total_delta_pnl = Column(Float)
    total_theta_pnl = Column(Float)
    total_vega_pnl = Column(Float)
    total_gamma_pnl = Column(Float)
    
    # Recommendations (JSON)
    recommendations = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
