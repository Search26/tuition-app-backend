import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from app.models import Course, Enrollment, Lesson, LessonStatus, User
from app.services import media_urls
from tests.factories import auth_headers, make_course, make_lesson, make_user

pytestmark = pytest.mark.usefixtures("db")


def _run(course: Course) -> str:
    return f"hls/{course.id}/{uuid.uuid4()}/{uuid.uuid4()}/"


async def _ready_lesson(db: AsyncSession, course: Course, position: int = 1) -> Lesson:
    run = _run(course)
    lesson = await make_lesson(
        db,
        course,
        position,
        status=LessonStatus.READY,
        hls_path=run + "master.m3u8",
        thumbnail_path=run + "thumbnail.jpg",
    )
    lesson.duration_seconds = 30
    await db.commit()
    return lesson


async def _student(db: AsyncSession, course: Course | None = None) -> User:
    user = await make_user(db)
    if course is not None:
        db.add(Enrollment(user_id=user.id, course_id=course.id))
        await db.commit()
    return user


async def test_enrolled_student_gets_a_signed_master_url_for_4_hours(
    client: AsyncClient, db: AsyncSession
) -> None:
    course = await make_course(db)
    lesson = await _ready_lesson(db, course)
    student = await _student(db, course)

    before = time.time()
    response = await client.get(f"/lessons/{lesson.id}/stream", headers=auth_headers(student))

    assert response.status_code == 200
    body = response.json()
    assert body["duration_seconds"] == 30
    assert body["url"].startswith("http://localhost:8080/media/v/")
    assert media_urls.verify(urlsplit(body["url"]).path) == lesson.hls_path
    expires = datetime.fromisoformat(body["expires_at"]).timestamp()
    assert before + 4 * 3600 - 1 <= expires <= time.time() + 4 * 3600


async def test_admin_streams_any_lesson_even_unpublished_without_enrolling(
    client: AsyncClient, db: AsyncSession
) -> None:
    lesson = await _ready_lesson(db, await make_course(db, published=False))
    admin = await make_user(db, is_admin=True)

    response = await client.get(f"/lessons/{lesson.id}/stream", headers=auth_headers(admin))

    assert response.status_code == 200


async def test_not_enrolled_is_403_even_before_the_video_is_ready(
    client: AsyncClient, db: AsyncSession
) -> None:
    course = await make_course(db)
    ready = await _ready_lesson(db, course)
    draft = await make_lesson(db, course, 2)
    enrolled_elsewhere = await _student(db, await make_course(db, "Other"))

    for lesson in (ready, draft):
        response = await client.get(
            f"/lessons/{lesson.id}/stream", headers=auth_headers(enrolled_elsewhere)
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NOT_ENROLLED"


@pytest.mark.parametrize("status", [LessonStatus.DRAFT, LessonStatus.PROCESSING])
async def test_enrolled_but_no_live_video_is_409(
    client: AsyncClient, db: AsyncSession, status: LessonStatus
) -> None:
    course = await make_course(db)
    lesson = await make_lesson(db, course, 1, status=status)
    student = await _student(db, course)

    response = await client.get(f"/lessons/{lesson.id}/stream", headers=auth_headers(student))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_NOT_READY"


async def test_unknown_lesson_or_unpublished_course_is_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    hidden = await make_course(db, published=False)
    lesson = await _ready_lesson(db, hidden)
    student = await _student(db, hidden)  # enrolled before it was unpublished

    for lesson_id in (lesson.id, uuid.uuid4()):
        response = await client.get(f"/lessons/{lesson_id}/stream", headers=auth_headers(student))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "LESSON_NOT_FOUND"


async def test_stream_needs_a_valid_login(client: AsyncClient, db: AsyncSession) -> None:
    lesson = await _ready_lesson(db, await make_course(db))

    missing = await client.get(f"/lessons/{lesson.id}/stream")
    bad = await client.get(
        f"/lessons/{lesson.id}/stream", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "NOT_AUTHENTICATED")
    assert (bad.status_code, bad.json()["error"]["code"]) == (401, "INVALID_TOKEN")


async def test_stream_is_one_query_after_login(client: AsyncClient, db: AsyncSession) -> None:
    """Lesson, course and enrollment in a single SELECT (plus the user lookup for the JWT)."""
    course = await make_course(db)
    lesson = await _ready_lesson(db, course)
    student = await _student(db, course)
    statements: list[str] = []

    def count(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(get_engine().sync_engine, "before_cursor_execute", count)
    try:
        response = await client.get(f"/lessons/{lesson.id}/stream", headers=auth_headers(student))
    finally:
        event.remove(get_engine().sync_engine, "before_cursor_execute", count)

    assert response.status_code == 200
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 2


async def test_cors_allows_the_configured_dev_origin_only(client: AsyncClient) -> None:
    allowed = await client.options(
        "/lessons/x/stream",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    other = await client.options(
        "/lessons/x/stream",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in other.headers
