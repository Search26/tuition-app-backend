"""Presigned video upload: issue a PUT URL, then verify what landed in MinIO."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import queue, storage
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Lesson, MediaAsset, MediaAssetStatus
from app.schemas.media import UploadTicket
from app.services.lessons import lesson_not_found
from app.services.media_keys import VIDEO_EXTENSIONS, original_key

logger = logging.getLogger(__name__)

# Statuses a newer completed upload replaces. Sprint 4 owns processing and ready assets.
_SUPERSEDABLE = (MediaAssetStatus.PENDING, MediaAssetStatus.UPLOADED, MediaAssetStatus.FAILED)


def _unprocessable(code: str, message: str) -> AppError:
    return AppError(code, message, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _asset_not_found() -> NotFoundError:
    return NotFoundError("ASSET_NOT_FOUND", "Media asset not found for this lesson")


async def request_upload(
    session: AsyncSession, lesson_id: uuid.UUID, content_type: str, size_bytes: int
) -> UploadTicket:
    settings = get_settings()
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise lesson_not_found()
    if content_type not in VIDEO_EXTENSIONS:
        allowed = ", ".join(VIDEO_EXTENSIONS)
        raise _unprocessable("UNSUPPORTED_CONTENT_TYPE", f"Content type must be one of: {allowed}")
    if size_bytes > settings.upload_max_bytes:
        raise _unprocessable(
            "FILE_TOO_LARGE", f"File is larger than the {settings.upload_max_bytes}-byte limit"
        )

    await storage.ensure_bucket()
    asset_id = uuid.uuid4()
    key = original_key(lesson.course_id, lesson.id, asset_id, content_type)
    ttl = settings.upload_url_ttl_seconds
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
    upload_url = storage.presign_put(key, ttl)

    session.add(
        MediaAsset(id=asset_id, lesson_id=lesson.id, original_key=key, content_type=content_type)
    )
    await session.commit()
    return UploadTicket(
        asset_id=asset_id,
        upload_url=upload_url,
        headers={"Content-Type": content_type},
        expires_at=expires_at,
    )


async def _reject(session: AsyncSession, asset: MediaAsset, code: str, message: str) -> AppError:
    """Delete the stored object, mark the asset failed, and return the error to raise."""
    await storage.delete_object(asset.original_key)
    asset.status = MediaAssetStatus.FAILED
    asset.error_msg = message
    await session.commit()
    return _unprocessable(code, message)


async def complete_upload(
    session: AsyncSession, lesson_id: uuid.UUID, asset_id: uuid.UUID
) -> MediaAsset:
    settings = get_settings()
    # Lock the lesson so completes for one lesson run one at a time; supersede can't deadlock.
    lesson = await session.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
    if lesson is None:
        raise lesson_not_found()
    asset = await session.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == asset_id, MediaAsset.lesson_id == lesson_id)
        .with_for_update()
    )
    if asset is None:
        raise _asset_not_found()
    if asset.status == MediaAssetStatus.FAILED:
        raise ConflictError("ASSET_FAILED", "This upload failed; request a new upload URL")
    if asset.status != MediaAssetStatus.PENDING:
        return asset  # already confirmed: idempotent retry

    stored = await storage.stat(asset.original_key)
    if stored is None:
        raise ConflictError("UPLOAD_NOT_FOUND", "No uploaded file found for this asset yet")
    if stored.size > settings.upload_max_bytes:
        raise await _reject(
            session,
            asset,
            "UPLOAD_TOO_LARGE",
            f"File is larger than the {settings.upload_max_bytes}-byte limit",
        )
    stored_type = stored.content_type.split(";", 1)[0].strip().lower()
    if stored_type != asset.content_type:
        raise await _reject(
            session,
            asset,
            "UPLOAD_CONTENT_TYPE_MISMATCH",
            f"Uploaded Content-Type {stored_type!r} does not match {asset.content_type!r}",
        )

    asset.status = MediaAssetStatus.UPLOADED
    asset.size_bytes = stored.size
    superseded = list(
        await session.scalars(
            delete(MediaAsset)
            .where(
                MediaAsset.lesson_id == lesson_id,
                MediaAsset.id != asset.id,
                MediaAsset.status.in_(_SUPERSEDABLE),
            )
            .returning(MediaAsset.original_key)
        )
    )
    await session.commit()
    await session.refresh(asset)
    await storage.delete_keys_best_effort(superseded)
    try:
        await queue.enqueue_transcode(asset.id)
    except Exception:
        # The upload itself succeeded; the asset stays `uploaded` and retranscode enqueues it.
        logger.exception("Could not enqueue transcode for asset %s", asset.id)
    return asset
