import uuid

from fastapi import APIRouter, status

from app.api.deps import DbSession
from app.schemas.lesson import AdminLesson, LessonUpdate
from app.services import lessons as lesson_service

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
