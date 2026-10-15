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
# A separate bucket for tests, and URLs signed for the host the tests themselves can reach.
os.environ["MINIO_BUCKET"] = "tuition-media-test"
os.environ["MINIO_PUBLIC_ENDPOINT"] = os.environ.get("MINIO_ENDPOINT", "minio:9000")
os.environ["MINIO_PUBLIC_SECURE"] = os.environ.get("MINIO_SECURE", "false")
# Tests get their own Redis database: the Compose worker watches the dev queue and would otherwise
# pick up (and consume) the transcode jobs tests enqueue.
os.environ["REDIS_URL"] = os.environ.get(
    "TEST_REDIS_URL", os.environ.get("REDIS_URL", "redis://redis:6379/0").rsplit("/", 1)[0] + "/1"
)

from alembic import command  # noqa: E402
from app.clients.redis import get_redis  # noqa: E402
from app.clients.storage import ensure_bucket, get_minio  # noqa: E402
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
            text(
                "TRUNCATE users, refresh_tokens, courses, lessons, enrollments, media_assets "
                "CASCADE"
            )
        )
        await session.commit()
        redis = get_redis()
        for pattern in ("ratelimit:login:*", "arq:*"):
            async for key in redis.scan_iter(pattern):
                await redis.delete(key)
        yield session


@pytest.fixture
async def bucket() -> str:
    """The test bucket, emptied before each test."""
    await ensure_bucket()
    name = get_settings().minio_bucket
    client = get_minio()
    for obj in client.list_objects(name, recursive=True):
        client.remove_object(name, obj.object_name)
    return name
