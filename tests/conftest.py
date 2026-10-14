import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

# Point the app at the test database before any settings are read, so tests never touch dev data.
_test_db_url = os.environ.get("TEST_DATABASE_URL")
if not _test_db_url:
    pytest.exit("TEST_DATABASE_URL must be set to run tests", returncode=2)
os.environ["DATABASE_URL"] = _test_db_url

from app.core.config import get_settings  # noqa: E402
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
