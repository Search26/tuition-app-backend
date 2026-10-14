import asyncio
import time

from fastapi import FastAPI
from httpx import AsyncClient

from app.api.deps import get_health_checks
from app.services.health import default_checks


async def test_health_ok_when_all_dependencies_are_up(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis", "minio"}
    assert all(check["status"] == "ok" for check in body["checks"].values())


async def test_health_degraded_when_one_dependency_fails(app: FastAPI, client: AsyncClient) -> None:
    async def broken_redis() -> None:
        raise ConnectionError("redis://user:password@redis:6379 refused")

    checks = default_checks() | {"redis": broken_redis}
    app.dependency_overrides[get_health_checks] = lambda: checks

    response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "error"
    assert body["checks"]["redis"]["detail"] == "unreachable"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["minio"]["status"] == "ok"
    assert "password" not in response.text


async def test_health_times_out_a_hanging_dependency(app: FastAPI, client: AsyncClient) -> None:
    async def hanging_minio() -> None:
        await asyncio.sleep(30)

    checks = default_checks() | {"minio": hanging_minio}
    app.dependency_overrides[get_health_checks] = lambda: checks

    start = time.perf_counter()
    response = await client.get("/health")
    elapsed = time.perf_counter() - start

    assert response.status_code == 503
    assert response.json()["checks"]["minio"]["detail"] == "timeout"
    assert elapsed < 3
