"""Admin lesson CRUD. Status, duration and hls_path belong to the media sprints."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Course, Lesson
from app.services.courses import course_not_found


def _lesson_not_found() -> NotFoundError:
    return NotFoundError("LESSON_NOT_FOUND", "Lesson not found")


async def _get_lesson(session: AsyncSession, lesson_id: uuid.UUID) -> Lesson:
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise _lesson_not_found()
    return lesson


async def create_lesson(
    session: AsyncSession, course_id: uuid.UUID, values: Mapping[str, Any]
) -> Lesson:
    if await session.get(Course, course_id) is None:
        raise course_not_found()
    fields = dict(values)
    if fields.get("position") is None:
        last = await session.scalar(
            select(func.max(Lesson.position)).where(Lesson.course_id == course_id)
        )
        fields["position"] = (last or 0) + 1
    lesson = Lesson(course_id=course_id, **fields)
    session.add(lesson)
    await session.commit()
    await session.refresh(lesson)
    return lesson


async def update_lesson(
    session: AsyncSession, lesson_id: uuid.UUID, values: Mapping[str, Any]
) -> Lesson:
    lesson = await _get_lesson(session, lesson_id)
    if values:
        for field, value in values.items():
            setattr(lesson, field, value)
        await session.commit()
        await session.refresh(lesson)
    return lesson


async def delete_lesson(session: AsyncSession, lesson_id: uuid.UUID) -> None:
    lesson = await _get_lesson(session, lesson_id)
    await session.delete(lesson)
    await session.commit()
