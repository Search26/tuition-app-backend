"""arq job `transcode(asset_id)`: source video -> HLS renditions + thumbnail, then swap the lesson.

Status machine (asset): uploaded -> processing -> ready | failed. Each attempt writes to its own
`hls/{course}/{lesson}/{run_id}/` prefix and only the finishing transaction points the lesson at
it, so a video people are watching is never overwritten and a crashed attempt leaves nothing live.
"""

import asyncio
import logging
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arq import Retry
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import queue, storage
from app.core.config import get_settings
from app.core.logging import request_id_var
from app.db.session import get_sessionmaker
from app.models import Lesson, LessonStatus, MediaAsset, MediaAssetStatus
from app.services.media_keys import hls_run_prefix, prefix_of, transcode_log_key
from app.transcode import ffmpeg
from app.transcode.ladder import pick_renditions
from app.transcode.probe import ProbeError, SourceInfo, probe

logger = logging.getLogger(__name__)

MAX_TRIES = 2
_RUNNABLE = (MediaAssetStatus.UPLOADED, MediaAssetStatus.PROCESSING)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RUN_DIR = re.compile(rf"hls/{_UUID}/{_UUID}/{_UUID}/")


@dataclass(frozen=True)
class _Run:
    asset_id: uuid.UUID
    original_key: str
    attempt: int
    prefix: str
    stale_prefix: str | None  # output of an earlier attempt that never went live


async def _lock(session: AsyncSession, asset_id: uuid.UUID) -> tuple[Lesson, MediaAsset] | None:
    """Lock the lesson, then the asset: the same order as upload-complete, so no deadlock."""
    lesson_id = await session.scalar(select(MediaAsset.lesson_id).where(MediaAsset.id == asset_id))
    if lesson_id is None:
        return None
    lesson = await session.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
    asset = await session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    if lesson is None or asset is None:
        return None
    return lesson, asset


async def _start(asset_id: uuid.UUID) -> _Run | None:
    async with get_sessionmaker()() as session:
        locked = await _lock(session, asset_id)
        if locked is None:
            return None
        lesson, asset = locked
        if asset.status not in _RUNNABLE:
            return None  # superseded, already done, or failed and waiting for a retranscode
        live = prefix_of(lesson.hls_path) if lesson.hls_path else None
        stale = asset.output_prefix if asset.output_prefix not in (None, live) else None
        asset.attempts += 1
        asset.status = MediaAssetStatus.PROCESSING
        asset.error_msg = None
        asset.output_prefix = hls_run_prefix(lesson.course_id, lesson.id, uuid.uuid4())
        if lesson.hls_path is None:
            lesson.status = LessonStatus.PROCESSING
        run = _Run(asset.id, asset.original_key, asset.attempts, asset.output_prefix, stale)
        await session.commit()
        return run


async def _finish(run: _Run, info: SourceInfo) -> str:
    async with get_sessionmaker()() as session:
        locked = await _lock(session, run.asset_id)
        if locked is None or locked[1].output_prefix != run.prefix:
            await session.rollback()
            await storage.delete_prefix_best_effort(run.prefix)  # lesson or asset deleted mid-run
            return "discarded"
        lesson, asset = locked
        newer_is_live = await session.scalar(
            select(
                exists().where(
                    MediaAsset.lesson_id == lesson.id,
                    MediaAsset.status == MediaAssetStatus.READY,
                    MediaAsset.created_at > asset.created_at,
                )
            )
        )
        if newer_is_live:
            # A newer upload finished first; this one is out of date.
            await session.delete(asset)
            await session.commit()
            await storage.delete_prefix_best_effort(run.prefix)
            await storage.delete_keys_best_effort([run.original_key])
            return "superseded"

        replaced = list(
            await session.scalars(
                select(MediaAsset).where(
                    MediaAsset.lesson_id == lesson.id,
                    MediaAsset.id != asset.id,
                    MediaAsset.status == MediaAssetStatus.READY,
                )
            )
        )
        live = prefix_of(lesson.hls_path) if lesson.hls_path else None
        old_prefixes = {a.output_prefix for a in replaced if a.output_prefix} - {live, run.prefix}
        old_originals = [a.original_key for a in replaced]
        for old in replaced:
            await session.delete(old)

        asset.status = MediaAssetStatus.READY
        asset.error_msg = None
        lesson.status = LessonStatus.READY
        lesson.hls_path = run.prefix + "master.m3u8"
        lesson.thumbnail_path = run.prefix + "thumbnail.jpg"
        lesson.duration_seconds = round(info.duration_seconds)
        await session.commit()

    if live and live != run.prefix:
        await _retire(live)
    for prefix in sorted(old_prefixes):
        await storage.delete_prefix_best_effort(prefix)
    await storage.delete_keys_best_effort(old_originals)
    return "ready"


async def _retire(prefix: str) -> None:
    """Delete the replaced video once every signed URL to it has expired, so people watching or
    downloading it finish. Without the queue it goes now: an orphan is worse than a cut-off."""
    try:
        await queue.enqueue_delete_prefix(prefix, get_settings().media_url_ttl_seconds)
    except Exception:
        logger.exception("Could not schedule deletion of %s; deleting it now", prefix)
        await storage.delete_prefix_best_effort(prefix)


async def delete_prefix(ctx: dict[str, Any], prefix: str) -> str:
    """arq job: remove a replaced video's run folder (scheduled by `_retire`)."""
    if not _RUN_DIR.fullmatch(prefix):
        logger.error("Refusing to delete %r: not a transcode run folder", prefix)
        return "refused"
    async with get_sessionmaker()() as session:
        if await session.scalar(select(exists().where(Lesson.hls_path.startswith(prefix)))):
            return "live"  # can't happen with fresh run ids, but never delete a live video
    await storage.delete_prefix_best_effort(prefix)
    return "deleted"


async def _fail(run: _Run, message: str) -> None:
    async with get_sessionmaker()() as session:
        locked = await _lock(session, run.asset_id)
        if locked is not None and locked[1].output_prefix == run.prefix:
            lesson, asset = locked
            asset.status = MediaAssetStatus.FAILED
            asset.error_msg = message[:2000]
            if lesson.hls_path is None:
                lesson.status = LessonStatus.DRAFT
            await session.commit()
    await storage.delete_prefix_best_effort(run.prefix)


async def _encode(run: _Run, work: Path, log_path: Path) -> SourceInfo:
    settings = get_settings()
    source = work / "source"
    out_dir = work / "out"
    out_dir.mkdir()
    await storage.download(run.original_key, source)
    info = await probe(source)
    renditions = pick_renditions(info.width, info.height)
    args = ffmpeg.hls_args(
        source, out_dir, renditions, has_audio=info.has_audio, threads=settings.transcode_threads
    )
    await ffmpeg.run(args, log_path, settings.transcode_timeout_seconds)
    thumb = ffmpeg.thumbnail_args(source, out_dir / "thumbnail.jpg", info.duration_seconds)
    await ffmpeg.run(thumb, log_path, 120)
    await storage.upload_dir(run.prefix, out_dir)
    return info


async def _keep_log(run: _Run, log_path: Path) -> None:
    try:
        await storage.upload_file(transcode_log_key(run.asset_id, run.attempt), log_path)
    except FileNotFoundError:
        pass  # failed before ffmpeg ran
    except Exception:
        logger.exception("Could not upload transcode log for asset %s", run.asset_id)


def _make_workdir(root: str, asset_id: str) -> Path:
    Path(root).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"transcode-{asset_id}-", dir=root))


async def transcode(ctx: dict[str, Any], asset_id: str, request_id: str | None = None) -> str:
    """arq job. `request_id` is the id of the request that enqueued it, for the log lines."""
    request_id_var.set(request_id)  # each arq job runs in its own task, so this doesn't leak
    job = {"asset_id": asset_id, "job_try": ctx.get("job_try", 1)}
    started = time.monotonic()
    logger.info("Transcode started", extra=job)
    result = await _transcode(ctx, asset_id)
    logger.info(
        "Transcode %s",
        result,
        extra={**job, "result": result, "duration_s": round(time.monotonic() - started, 1)},
    )
    return result


async def _transcode(ctx: dict[str, Any], asset_id: str) -> str:
    settings = get_settings()
    run = await _start(uuid.UUID(asset_id))
    if run is None:
        return "skipped"
    if run.stale_prefix:
        await storage.delete_prefix_best_effort(run.stale_prefix)

    work = await asyncio.to_thread(_make_workdir, settings.transcode_tmp_dir, asset_id)
    log_path = work / "ffmpeg.log"
    try:
        try:
            info = await _encode(run, work, log_path)
        except ProbeError as exc:
            await _keep_log(run, log_path)
            # The worker's temp path means nothing to admins.
            detail = str(exc).replace(f"{work}/", "")
            await _fail(run, f"Not a readable video: {detail}")
            logger.warning("Asset %s is not a readable video: %s", asset_id, exc)
            return "failed"
        except Exception as exc:
            await _keep_log(run, log_path)
            reason = f"{exc}\n{ffmpeg.tail(log_path)}".strip()
            if ctx.get("job_try", 1) < MAX_TRIES:
                logger.warning("Transcode of %s failed, retrying: %s", asset_id, exc)
                await storage.delete_prefix_best_effort(run.prefix)
                raise Retry(defer=settings.transcode_retry_delay_seconds) from exc
            logger.error("Transcode of %s failed: %s", asset_id, exc)
            await _fail(run, reason)
            return "failed"
        await _keep_log(run, log_path)
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)
    return await _finish(run, info)
