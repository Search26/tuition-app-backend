import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Point the app at the test database before any settings are read, so tests never touch dev data.
_test_db_url = os.environ.get("TEST_DATABASE_URL")
if not _test_db_url:
    pytest.exit("TEST_DATABASE_URL must be set to run tests", returncode=2)
os.environ["DATABASE_URL"] = _test_db_url

from alembic import command  # noqa: E402
from app.clients.redis import get_redis  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def app():
    get_settings.cache_clear()
    application = create_app()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
async def migrated_db() -> None:
    """Bring the test database to head once per run."""
    config = Config("alembic.ini")
    config.attributes["database_url"] = _test_db_url
    # env.py calls asyncio.run, so run it in a worker thread with its own loop.
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture
async def db(migrated_db: None) -> AsyncIterator[AsyncSession]:
    """A session on a clean database, with login rate-limit counters cleared."""
    async with get_sessionmaker()() as session:
        await session.execute(
            text("TRUNCATE users, refresh_tokens, courses, lessons, enrollments CASCADE")
        )
        await session.commit()
        redis = get_redis()
        async for key in redis.scan_iter("ratelimit:login:*"):
            await redis.delete(key)
        yield session
