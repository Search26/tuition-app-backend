"""Course queries for the public and admin routers."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models import Course, Enrollment, Lesson
from app.schemas.pagination import PageParams


def course_not_found() -> NotFoundError:
    return NotFoundError("COURSE_NOT_FOUND", "Course not found")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _searchable(expr: Any) -> Any:
    # Must match the expression of ix_courses_title_search so the trigram index is used.
    return func.f_unaccent(func.lower(expr))


async def list_courses(
    session: AsyncSession, params: PageParams, q: str | None, *, published_only: bool
) -> tuple[list[tuple[Course, int]], int]:
    """One page of courses with their lesson counts, plus the total matching count."""
    conditions = []
    if published_only:
        conditions.append(Course.is_published.is_(True))
    if q:
        pattern = f"%{_escape_like(q)}%"
        conditions.append(_searchable(Course.title).like(_searchable(pattern), escape="\\"))

    lesson_count = (
        select(func.count(Lesson.id))
        .where(Lesson.course_id == Course.id)
        .correlate(Course)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Course, lesson_count)
        .where(*conditions)
        .order_by(Course.created_at.desc(), Course.id.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    total = await session.scalar(select(func.count(Course.id)).where(*conditions))
    return [(course, count) for course, count in rows.all()], total or 0


async def get_course(
    session: AsyncSession, course_id: uuid.UUID, *, published_only: bool
) -> Course:
    """The course with its lessons in two queries (selectinload), whatever the lesson count."""
    query = select(Course).options(selectinload(Course.lessons)).where(Course.id == course_id)
    if published_only:
        query = query.where(Course.is_published.is_(True))
    course = await session.scalar(query)
    if course is None:
        raise course_not_found()
    return course


async def create_course(session: AsyncSession, values: Mapping[str, Any]) -> Course:
    course = Course(**values)
    session.add(course)
    await session.commit()
    return await get_course(session, course.id, published_only=False)


async def update_course(
    session: AsyncSession, course_id: uuid.UUID, values: Mapping[str, Any]
) -> Course:
    course = await get_course(session, course_id, published_only=False)
    if values:
        for field, value in values.items():
            setattr(course, field, value)
        await session.commit()
        session.expire_all()
    return await get_course(session, course_id, published_only=False)


def _has_enrollments() -> ConflictError:
    return ConflictError(
        "COURSE_HAS_ENROLLMENTS", "Course has enrollments; unpublish it instead of deleting"
    )


async def delete_course(session: AsyncSession, course_id: uuid.UUID) -> None:
    course = await session.get(Course, course_id)
    if course is None:
        raise course_not_found()
    if await session.scalar(select(exists().where(Enrollment.course_id == course_id))):
        raise _has_enrollments()
    await session.delete(course)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Someone enrolled between the check and the delete; the FK is RESTRICT.
        await session.rollback()
        raise _has_enrollments() from exc
