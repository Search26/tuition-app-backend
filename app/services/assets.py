"""Admin views of media assets and the retranscode action."""

import logging
import uuid

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import queue
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Lesson, MediaAsset, MediaAssetStatus
from app.services.lessons import lesson_not_found

logger = logging.getLogger(__name__)

_RETRANSCODABLE = (MediaAssetStatus.UPLOADED, MediaAssetStatus.FAILED, MediaAssetStatus.READY)


async def list_lesson_assets(session: AsyncSession, lesson_id: uuid.UUID) -> list[MediaAsset]:
    if await session.get(Lesson, lesson_id) is None:
        raise lesson_not_found()
    rows = await session.scalars(
        select(MediaAsset)
        .where(MediaAsset.lesson_id == lesson_id)
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    )
    return list(rows)


async def retranscode(session: AsyncSession, asset_id: uuid.UUID) -> MediaAsset:
    asset = await session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    if asset is None:
        raise NotFoundError("ASSET_NOT_FOUND", "Media asset not found")
    if asset.status == MediaAssetStatus.PROCESSING:
        raise ConflictError("ASSET_PROCESSING", "This asset is being transcoded right now")
    if asset.status not in _RETRANSCODABLE:
        raise ConflictError("UPLOAD_NOT_COMPLETE", "Finish the upload before transcoding")

    previous = (asset.status, asset.error_msg)
    asset.status = MediaAssetStatus.UPLOADED
    asset.error_msg = None
    # Commit before enqueueing, so the worker never sees the old status.
    await session.commit()
    try:
        await queue.enqueue_transcode(asset.id)
    except Exception as exc:
        logger.exception("Could not enqueue transcode for asset %s", asset.id)
        asset.status, asset.error_msg = previous
        await session.commit()
        raise AppError(
            "QUEUE_UNAVAILABLE",
            "The job queue is unavailable; try again shortly",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    await session.refresh(asset)
    return asset
