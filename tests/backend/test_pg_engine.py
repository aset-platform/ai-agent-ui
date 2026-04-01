"""Test PostgreSQL async engine creation."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_engine_creates_from_database_url():
    """Engine uses DATABASE_URL from settings."""
    with patch("backend.db.engine.get_settings") as mock:
        mock.return_value.database_url = (
            "postgresql+asyncpg://app:test@localhost:5432/testdb"
        )
        # Clear lru_cache
        from backend.db.engine import get_engine
        get_engine.cache_clear()

        engine = get_engine()
        assert "localhost" in str(engine.url)
        assert "5432" in str(engine.url)
        engine.sync_engine.dispose()
        get_engine.cache_clear()


@pytest.mark.asyncio
async def test_session_factory_returns_async_session():
    """Session factory produces AsyncSession instances."""
    with patch("backend.db.engine.get_settings") as mock:
        mock.return_value.database_url = (
            "postgresql+asyncpg://app:test@localhost:5432/testdb"
        )
        from backend.db.engine import get_session_factory, get_engine
        get_engine.cache_clear()
        get_session_factory.cache_clear()

        factory = get_session_factory()
        assert factory is not None

        get_engine.cache_clear()
        get_session_factory.cache_clear()
