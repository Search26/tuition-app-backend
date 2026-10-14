import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models import Course, Lesson, LessonStatus, User

_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


async def make_user(db: AsyncSession, *, is_admin: bool = False) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("not-used-in-these-tests"),
        name="Test User",
        is_admin=is_admin,
    )
    db.add(user)
    await db.commit()
    return user


def auth_headers(user: User) -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(
        user.id, settings.jwt_secret.get_secret_value(), settings.access_token_ttl_seconds
    )
    return {"Authorization": f"Bearer {token}"}


async def make_course(
    db: AsyncSession,
    title: str = "Khoá học mẫu",
    *,
    published: bool = True,
    minutes_after_base: int = 0,
) -> Course:
    course = Course(
        title=title,
        is_published=published,
        created_at=_BASE_TIME + timedelta(minutes=minutes_after_base),
    )
    db.add(course)
    await db.commit()
    return course


async def make_lesson(
    db: AsyncSession,
    course: Course,
    position: int,
    *,
    title: str | None = None,
    status: LessonStatus = LessonStatus.DRAFT,
    hls_path: str | None = None,
) -> Lesson:
    lesson = Lesson(
        course_id=course.id,
        title=title or f"Bài {position}",
        position=position,
        status=status,
        hls_path=hls_path,
    )
    db.add(lesson)
    await db.commit()
    return lesson
