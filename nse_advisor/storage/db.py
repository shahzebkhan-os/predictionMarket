"""
Database Management.

Async session management for SQLAlchemy.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from nse_advisor.config import get_settings
from nse_advisor.storage.models import Base

logger = logging.getLogger(__name__)


class Database:
    """
    Async database manager.
    
    Handles connection pooling and session management.
    """
    
    def __init__(self, database_url: str | None = None) -> None:
        """
        Initialize database manager.
        
        Args:
            database_url: Database URL (defaults to config)
        """
        if database_url is None:
            settings = get_settings()
            database_url = settings.database_url
        
        self._database_url = database_url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
    
    async def connect(self) -> None:
        """Create database engine and session factory."""
        if self._engine is not None:
            return
        
        # Create async engine
        self._engine = create_async_engine(
            self._database_url,
            echo=False,
            poolclass=NullPool,  # For SQLite compatibility
        )
        
        # Create session factory
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        
        logger.info(f"Database connected: {self._database_url.split('?')[0]}")
    
    async def disconnect(self) -> None:
        """Close database connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database disconnected")
    
    async def create_tables(self) -> None:
        """Create all tables from models."""
        if self._engine is None:
            await self.connect()
        
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        logger.info("Database tables created")
    
    async def drop_tables(self) -> None:
        """Drop all tables."""
        if self._engine is None:
            return
        
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        
        logger.info("Database tables dropped")
    
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get a database session.
        
        Usage:
            async with db.session() as session:
                result = await session.execute(query)
        """
        if self._session_factory is None:
            await self.connect()
        
        assert self._session_factory is not None
        
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


# Global database instance
_database: Database | None = None


def get_database() -> Database:
    """Get or create global database instance."""
    global _database
    if _database is None:
        _database = Database()
    return _database


async def init_database() -> Database:
    """Initialize database and create tables."""
    db = get_database()
    await db.connect()
    await db.create_tables()
    return db


async def close_database() -> None:
    """Close database connection."""
    global _database
    if _database is not None:
        await _database.disconnect()
        _database = None
