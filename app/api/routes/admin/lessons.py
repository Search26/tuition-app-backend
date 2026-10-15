import uuid

from fastapi import APIRouter, status

from app.api.deps import DbSession
from app.schemas.lesson import AdminLesson, LessonUpdate
from app.schemas.media import MediaAssetOut, UploadComplete, UploadRequest, UploadTicket
from app.services import lessons as lesson_service
from app.services import uploads as upload_service

router = APIRouter(prefix="/lessons")


@router.patch("/{lesson_id}")
async def update_lesson(
    lesson_id: uuid.UUID, body: LessonUpdate, session: DbSession
) -> AdminLesson:
    lesson = await lesson_service.update_lesson(
        session, lesson_id, body.model_dump(exclude_unset=True)
    )
    return AdminLesson.model_validate(lesson)


@router.delete("/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(lesson_id: uuid.UUID, session: DbSession) -> None:
    await lesson_service.delete_lesson(session, lesson_id)


@router.post("/{lesson_id}/upload-request", status_code=status.HTTP_201_CREATED)
async def request_upload(
    lesson_id: uuid.UUID, body: UploadRequest, session: DbSession
) -> UploadTicket:
    return await upload_service.request_upload(
        session, lesson_id, body.content_type, body.size_bytes
    )


@router.post("/{lesson_id}/upload-complete")
async def complete_upload(
    lesson_id: uuid.UUID, body: UploadComplete, session: DbSession
) -> MediaAssetOut:
    asset = await upload_service.complete_upload(session, lesson_id, body.asset_id)
    return MediaAssetOut.model_validate(asset)
