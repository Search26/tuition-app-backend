"""Transcode worker: `arq app.worker.WorkerSettings` (the `worker` service in Compose)."""

import logging
from typing import Any

from arq import func

from app.clients.queue import redis_settings
from app.core.config import get_settings
from app.db.session import get_engine
from app.services.transcode_job import MAX_TRIES, delete_prefix, transcode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_settings = get_settings()


async def startup(_: dict[str, Any]) -> None:
    logging.getLogger(__name__).info(
        "Transcode worker up: max_jobs=%s timeout=%ss",
        _settings.worker_max_jobs,
        _settings.transcode_timeout_seconds,
    )


async def shutdown(_: dict[str, Any]) -> None:
    await get_engine().dispose()


class WorkerSettings:
    functions = [
        func(
            transcode,
            name="transcode",
            max_tries=MAX_TRIES,
            # Backstop only: ffmpeg has its own per-attempt timeout; this also covers download,
            # thumbnail and upload.
            timeout=_settings.transcode_timeout_seconds + 900,
        ),
        # Replaced videos, deferred until their signed URLs have expired.
        func(delete_prefix, name="delete_prefix", max_tries=3, timeout=600),
    ]
    redis_settings = redis_settings()
    max_jobs = _settings.worker_max_jobs
    # No stored results: the job id frees up when a job ends, so a retranscode can reuse it.
    keep_result = 0
    on_startup = startup
    on_shutdown = shutdown
