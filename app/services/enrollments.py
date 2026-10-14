import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Enrollment
from app.services.courses import course_not_found


async def enroll(
    session: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> tuple[datetime, bool]:
    """Enroll once, idempotently. Returns (enrolled_at, created)."""
    published = await session.scalar(
        select(Course.id).where(Course.id == course_id, Course.is_published.is_(True))
    )
    if published is None:
        raise course_not_found()

    # ON CONFLICT makes concurrent enrolls safe: exactly one insert wins, none error.
    created_at = await session.scalar(
        insert(Enrollment)
        .values(user_id=user_id, course_id=course_id)
        .on_conflict_do_nothing(index_elements=["user_id", "course_id"])
        .returning(Enrollment.created_at)
    )
    await session.commit()
    if created_at is not None:
        return created_at, True

    existing = await session.scalar(
        select(Enrollment.created_at).where(
            Enrollment.user_id == user_id, Enrollment.course_id == course_id
        )
    )
    assert existing is not None
    return existing, False
