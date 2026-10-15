"""Who may watch a lesson, and the signed URL they get."""

import uuid

from fastapi import status
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError
from app.models import Course, Enrollment, Lesson, LessonStatus, User
from app.schemas.stream import StreamOut
from app.services import media_urls
from app.services.lessons import lesson_not_found


async def stream(session: AsyncSession, user: User, lesson_id: uuid.UUID) -> StreamOut:
    enrolled = exists().where(
        Enrollment.user_id == user.id, Enrollment.course_id == Lesson.course_id
    )
    row = (
        await session.execute(
            select(Lesson, Course.is_published, enrolled.label("enrolled"))
            .join(Course, Course.id == Lesson.course_id)
            .where(Lesson.id == lesson_id)
        )
    ).first()
    if row is None:
        raise lesson_not_found()
    lesson, is_published, is_enrolled = row
    if not user.is_admin:
        if not is_published:
            raise lesson_not_found()
        # Checked before readiness, so someone not enrolled can't learn which lessons have video.
        if not is_enrolled:
            raise AppError(
                "NOT_ENROLLED", "Enroll in this course to watch it", status.HTTP_403_FORBIDDEN
            )
    if lesson.status != LessonStatus.READY or lesson.hls_path is None:
        raise ConflictError("VIDEO_NOT_READY", "This lesson's video isn't ready yet")
    url, expires_at = media_urls.video_url(lesson.hls_path)
    return StreamOut(url=url, expires_at=expires_at, duration_seconds=lesson.duration_seconds)
