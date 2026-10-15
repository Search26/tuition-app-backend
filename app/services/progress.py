"""Watch progress: batch sync from offline clients, and the reads built on it.

Merge rule (design doc, Sprint 6): position is last-write-wins by the client's timestamp, and
`completed` is sticky, so the final row is the same whatever order items arrive in and however
often they are resent. One INSERT ... ON CONFLICT does the merge atomically; nothing is read
before the write to decide it.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import Exists, and_, case, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import Course, Enrollment, Lesson, Progress, User
from app.schemas.pagination import PageParams
from app.schemas.progress import (
    MyCourse,
    ProgressBatch,
    ProgressIn,
    ProgressOut,
    ProgressResult,
    SyncStatus,
)
from app.services.courses import course_not_found

# A lesson counts as completed once the position reaches this share of its duration.
COMPLETE_RATIO = 0.9


def not_enrolled() -> AppError:
    return AppError("NOT_ENROLLED", "Enroll in this course first", status.HTTP_403_FORBIDDEN)


@dataclass
class _Row:
    index: int  # position of the item in the request
    lesson_id: uuid.UUID
    position_seconds: float
    completed: bool
    client_updated_at: datetime


def _enrolled(user: User) -> Exists:
    return exists().where(Enrollment.user_id == user.id, Enrollment.course_id == Lesson.course_id)


async def _lesson_access(
    session: AsyncSession, user: User, lesson_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[int | None, str | None]]:
    """Duration and rejection reason (None = allowed) for each existing lesson, in one query."""
    rows = await session.execute(
        select(Lesson.id, Lesson.duration_seconds, Course.is_published, _enrolled(user))
        .join(Course, Course.id == Lesson.course_id)
        .where(Lesson.id.in_(lesson_ids))
    )
    access: dict[uuid.UUID, tuple[int | None, str | None]] = {}
    for lesson_id, duration, is_published, is_enrolled in rows:
        reason = None
        if not user.is_admin:
            if not is_published:
                reason = "LESSON_NOT_FOUND"
            elif not is_enrolled:
                reason = "NOT_ENROLLED"
        access[lesson_id] = (duration, reason)
    return access


def _prepare(
    item: ProgressIn, index: int, duration: int | None, latest_ok: datetime, now: datetime
) -> _Row:
    """Clamp a too-far-ahead clock and a past-the-end position, and decide completion."""
    client_time = item.client_updated_at if item.client_updated_at <= latest_ok else now
    position = item.position_seconds
    completed = False
    if duration:  # None or 0: not transcoded yet, so completion can't be judged
        position = min(position, float(duration))
        completed = position >= COMPLETE_RATIO * duration
    return _Row(index, item.lesson_id, position, completed, client_time)


async def sync(
    session: AsyncSession, user: User, batch: ProgressBatch, now: datetime | None = None
) -> list[ProgressResult]:
    now = now or datetime.now(UTC)
    latest_ok = now + timedelta(seconds=get_settings().progress_max_clock_skew_seconds)
    access = await _lesson_access(session, user, {item.lesson_id for item in batch.items})

    statuses: list[tuple[SyncStatus, str | None]] = [(SyncStatus.SKIPPED_STALE, None)] * len(
        batch.items
    )
    # One row per lesson: Postgres can't update the same row twice in one statement. The newest
    # item wins; on equal timestamps the later one in the array does.
    winners: dict[uuid.UUID, _Row] = {}
    for index, item in enumerate(batch.items):
        duration, reason = access.get(item.lesson_id, (None, "LESSON_NOT_FOUND"))
        if reason is not None:
            statuses[index] = (SyncStatus.REJECTED, reason)
            continue
        row = _prepare(item, index, duration, latest_ok, now)
        current = winners.get(item.lesson_id)
        if current is None or row.client_updated_at >= current.client_updated_at:
            winners[item.lesson_id] = row

    stored: dict[uuid.UUID, ProgressOut] = {}
    if winners:
        # Sorted, so concurrent batches lock shared rows in the same order and can't deadlock.
        rows = sorted(winners.values(), key=lambda r: r.lesson_id)
        applied = await _upsert(session, user.id, rows)
        for row in rows:
            if applied.get(row.lesson_id) == row.client_updated_at:
                statuses[row.index] = (SyncStatus.APPLIED, None)
        result = await session.scalars(
            select(Progress).where(Progress.user_id == user.id, Progress.lesson_id.in_(winners))
        )
        stored = {p.lesson_id: ProgressOut.model_validate(p) for p in result}
    await session.commit()

    return [
        ProgressResult(
            lesson_id=item.lesson_id,
            status=item_status,
            reason=reason,
            progress=None if item_status == SyncStatus.REJECTED else stored[item.lesson_id],
        )
        for item, (item_status, reason) in zip(batch.items, statuses, strict=True)
    ]


async def _upsert(
    session: AsyncSession, user_id: uuid.UUID, rows: list[_Row]
) -> dict[uuid.UUID, datetime]:
    """The whole merge, as one statement. Returns the stored clock of every row it changed."""
    stmt = insert(Progress).values(
        [
            {
                "user_id": user_id,
                "lesson_id": r.lesson_id,
                "position_seconds": r.position_seconds,
                "completed": r.completed,
                "client_updated_at": r.client_updated_at,
            }
            for r in rows
        ]
    )
    incoming = stmt.excluded
    newer = incoming.client_updated_at > Progress.client_updated_at
    stmt = stmt.on_conflict_do_update(
        index_elements=[Progress.user_id, Progress.lesson_id],
        set_={
            "position_seconds": case(
                (newer, incoming.position_seconds), else_=Progress.position_seconds
            ),
            "client_updated_at": func.greatest(
                incoming.client_updated_at, Progress.client_updated_at
            ),
            # Sticky: a late report of a finished lesson still counts.
            "completed": Progress.completed | incoming.completed,
            "updated_at": func.now(),
        },
        # Neither newer nor newly completed: the row isn't touched at all, not even updated_at.
        where=newer | (incoming.completed & ~Progress.completed),
    ).returning(Progress.lesson_id, Progress.client_updated_at)
    result = await session.execute(stmt)
    return {lesson_id: client_time for lesson_id, client_time in result}


async def course_progress(
    session: AsyncSession, user: User, course_id: uuid.UUID
) -> list[ProgressOut]:
    """My rows for one course, in lesson order. 404 hidden course, 403 not enrolled."""
    enrolled = exists().where(Enrollment.user_id == user.id, Enrollment.course_id == Course.id)
    row = (
        await session.execute(select(Course.is_published, enrolled).where(Course.id == course_id))
    ).first()
    if row is None or (not user.is_admin and not row.is_published):
        raise course_not_found()
    if not user.is_admin and not row[1]:
        raise not_enrolled()

    result = await session.scalars(
        select(Progress)
        .join(Lesson, Lesson.id == Progress.lesson_id)
        .where(Progress.user_id == user.id, Lesson.course_id == course_id)
        .order_by(Lesson.position, Lesson.created_at, Lesson.id)
    )
    return [ProgressOut.model_validate(p) for p in result]


async def my_courses(
    session: AsyncSession, user: User, params: PageParams
) -> tuple[list[MyCourse], int]:
    """My enrolled, published courses with completion computed on read, plus the total count."""
    conditions = (Enrollment.user_id == user.id, Course.is_published.is_(True))
    rows = await session.execute(
        select(
            Course,
            Enrollment.created_at,
            func.count(Lesson.id),
            func.count(Progress.lesson_id).filter(Progress.completed),
            func.max(Progress.updated_at),
        )
        .select_from(Enrollment)
        .join(Course, Course.id == Enrollment.course_id)
        .outerjoin(Lesson, Lesson.course_id == Course.id)
        .outerjoin(Progress, and_(Progress.lesson_id == Lesson.id, Progress.user_id == user.id))
        .where(*conditions)
        .group_by(Course.id, Enrollment.created_at)
        .order_by(Enrollment.created_at.desc(), Course.id.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    items = [MyCourse.from_progress_row(*row) for row in rows.all()]
    total = await session.scalar(
        select(func.count())
        .select_from(Enrollment)
        .join(Course, Course.id == Enrollment.course_id)
        .where(*conditions)
    )
    return items, total or 0
