"""arq job queue on the stack's Redis. The API only enqueues; the worker runs the jobs."""

import logging
import uuid

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def transcode_job_id(asset_id: uuid.UUID) -> str:
    return f"transcode:{asset_id}"


async def enqueue_transcode(asset_id: uuid.UUID) -> None:
    """Enqueue once per asset: arq ignores a job id that is already queued or running."""
    queue = await get_queue()
    await queue.enqueue_job("transcode", str(asset_id), _job_id=transcode_job_id(asset_id))


async def enqueue_delete_prefix(prefix: str, delay_seconds: int) -> None:
    queue = await get_queue()
    await queue.enqueue_job(
        "delete_prefix", prefix, _job_id=f"delete:{prefix}", _defer_by=delay_seconds
    )
