import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from asyncpg.exceptions import InvalidAuthorizationSpecificationError, InvalidPasswordError
from minio.error import S3Error
from redis.exceptions import AuthenticationError
from sqlalchemy import text

from app.clients.redis import get_redis
from app.clients.storage import get_minio
from app.core.config import get_settings
from app.db.session import get_engine
from app.schemas.health import CheckResult, HealthResponse

logger = logging.getLogger(__name__)

Check = Callable[[], Awaitable[None]]

_S3_AUTH_CODES = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}
_AUTH_ERRORS = (AuthenticationError, InvalidPasswordError, InvalidAuthorizationSpecificationError)


async def check_database() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis() -> None:
    await get_redis().ping()


async def check_minio() -> None:
    # The MinIO SDK is sync; listing buckets proves both the endpoint and the credentials.
    await asyncio.to_thread(get_minio().list_buckets)


def default_checks() -> dict[str, Check]:
    return {"database": check_database, "redis": check_redis, "minio": check_minio}


def _classify(exc: BaseException) -> str:
    """Map an exception to a fixed word so no hostnames or credentials reach the response."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    # Drivers wrap their errors (SQLAlchemy wraps asyncpg), so walk the whole cause chain.
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _AUTH_ERRORS):
            return "auth_failed"
        if isinstance(current, S3Error) and current.code in _S3_AUTH_CODES:
            return "auth_failed"
        current = current.__cause__ or current.__context__
    return "unreachable"


async def _run_check(name: str, check: Check, limit_seconds: float) -> CheckResult:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(limit_seconds):
            await check()
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000)
        logger.warning("Health check %s failed: %r", name, exc)
        return CheckResult(status="error", latency_ms=latency_ms, detail=_classify(exc))
    latency_ms = round((time.perf_counter() - start) * 1000)
    return CheckResult(status="ok", latency_ms=latency_ms)


async def run_health_checks(checks: dict[str, Check]) -> HealthResponse:
    limit_seconds = get_settings().health_check_timeout_seconds
    results = await asyncio.gather(
        *(_run_check(name, check, limit_seconds) for name, check in checks.items())
    )
    by_name = dict(zip(checks, results, strict=True))
    overall = "ok" if all(r.status == "ok" for r in results) else "degraded"
    return HealthResponse(status=overall, checks=by_name)
