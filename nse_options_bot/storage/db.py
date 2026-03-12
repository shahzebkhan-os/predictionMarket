"""Async database session management.

Provides async SQLAlchemy session for database operations.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nse_options_bot.config import Settings
from nse_options_bot.storage.models import Base

logger = structlog.get_logger(__name__)


class DatabaseManager:
    """Async database manager.

    Handles connection pooling and session management.
    """

    def __init__(self, database_url: str | None = None) -> None:
        """Initialize database manager.

        Args:
            database_url: Database URL (async format)
        """
        self._database_url = database_url or "sqlite+aiosqlite:///./nse_bot.db"
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        logger.info("initializing_database", url=self._database_url.split("@")[-1])

        self._engine = create_async_engine(
            self._database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Create tables
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("database_initialized")

    async def close(self) -> None:
        """Close database connection."""
        if self._engine:
            await self._engine.dispose()
            logger.info("database_closed")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get async database session.

        Yields:
            AsyncSession
        """
        if not self._session_factory:
            raise RuntimeError("Database not initialized")

        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def readonly_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get read-only database session.

        Yields:
            AsyncSession
        """
        if not self._session_factory:
            raise RuntimeError("Database not initialized")

        session = self._session_factory()
        try:
            yield session
        finally:
            await session.close()


# Global database manager instance
_db_manager: DatabaseManager | None = None


async def get_db() -> DatabaseManager:
    """Get global database manager.

    Returns:
        DatabaseManager instance
    """
    global _db_manager

    if _db_manager is None:
        _db_manager = DatabaseManager()
        await _db_manager.initialize()

    return _db_manager


async def init_db(database_url: str | None = None) -> DatabaseManager:
    """Initialize database with custom URL.

    Args:
        database_url: Database URL

    Returns:
        DatabaseManager instance
    """
    global _db_manager

    _db_manager = DatabaseManager(database_url)
    await _db_manager.initialize()

    return _db_manager


async def close_db() -> None:
    """Close database connection."""
    global _db_manager

    if _db_manager:
        await _db_manager.close()
        _db_manager = None
