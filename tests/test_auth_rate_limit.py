import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.deps import get_login_rate_limiter
from app.core.config import get_settings
from app.services.rate_limit import LoginRateLimiter
from tests.auth_helpers import login, register

pytestmark = pytest.mark.usefixtures("db")


async def test_sixth_failed_login_for_an_email_is_rate_limited(client: AsyncClient) -> None:
    await register(client)
    for _ in range(5):
        assert (await login(client, password="wrong-password")).status_code == 401

    response = await login(client)  # even the right password is held back

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert 0 < int(response.headers["retry-after"]) <= 900


async def test_a_successful_login_resets_the_failure_count(client: AsyncClient) -> None:
    await register(client)
    for _ in range(4):
        await login(client, password="wrong-password")
    assert (await login(client)).status_code == 200

    for _ in range(4):
        assert (await login(client, password="wrong-password")).status_code == 401
    assert (await login(client)).status_code == 200


async def test_failures_for_unknown_emails_are_limited_too(client: AsyncClient) -> None:
    for _ in range(5):
        await login(client, email="ghost@example.com")

    response = await login(client, email="ghost@example.com")

    assert response.status_code == 429


async def test_ip_limit_applies_across_emails(client: AsyncClient) -> None:
    for i in range(20):
        response = await login(client, email=f"user{i}@example.com")
        assert response.status_code == 401

    response = await login(client, email="user20@example.com")

    assert response.status_code == 429
    assert 0 < int(response.headers["retry-after"]) <= 60


async def test_login_still_works_when_redis_is_down(app: FastAPI, client: AsyncClient) -> None:
    class _DownRedis:
        def pipeline(self, *args: object, **kwargs: object) -> None:
            raise RedisConnectionError("redis down")

        async def delete(self, *keys: str) -> None:
            raise RedisConnectionError("redis down")

    app.dependency_overrides[get_login_rate_limiter] = lambda: LoginRateLimiter(
        _DownRedis(),  # type: ignore[arg-type]
        get_settings(),
    )
    await register(client)

    assert (await login(client)).status_code == 200
    assert (await login(client, password="wrong-password")).status_code == 401
