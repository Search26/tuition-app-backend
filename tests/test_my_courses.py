"""GET /me/progress?course_id= and GET /me/courses."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from app.models import Course, Enrollment, Lesson, LessonStatus, User
from tests.factories import auth_headers, make_course, make_lesson, make_user

pytestmark = pytest.mark.usefixtures("db")

T0 = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


async def lessons(db: AsyncSession, course: Course, count: int) -> list[Lesson]:
    return [
        await make_lesson(db, course, n, status=LessonStatus.READY, duration_seconds=600)
        for n in range(1, count + 1)
    ]


async def enroll(db: AsyncSession, user: User, course: Course, minutes: int = 0) -> None:
    db.add(
        Enrollment(user_id=user.id, course_id=course.id, created_at=T0 + timedelta(minutes=minutes))
    )
    await db.commit()


async def watch(client: AsyncClient, user: User, *pairs: tuple[Lesson, float]) -> None:
    items = [
        {
            "lesson_id": str(lesson.id),
            "position_seconds": position,
            "client_updated_at": T0.isoformat(),
        }
        for lesson, position in pairs
    ]
    response = await client.put("/me/progress", json={"items": items}, headers=auth_headers(user))
    assert response.status_code == 200


async def test_course_progress_lists_my_rows_in_lesson_order(
    client: AsyncClient, db: AsyncSession
) -> None:
    course = await make_course(db)
    l1, l2, l3 = await lessons(db, course, 3)
    student, other = await make_user(db), await make_user(db)
    await enroll(db, student, course)
    await enroll(db, other, course)
    await watch(client, student, (l3, 30), (l1, 590))
    await watch(client, other, (l2, 10))

    response = await client.get(
        "/me/progress", params={"course_id": str(course.id)}, headers=auth_headers(student)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(i["lesson_id"], i["position_seconds"], i["completed"]) for i in items] == [
        (str(l1.id), 590, True),
        (str(l3.id), 30, False),
    ]


async def test_course_progress_errors(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)
    hidden = await make_course(db, "Hidden", published=False)
    student = await make_user(db)
    await enroll(db, student, hidden)
    headers = auth_headers(student)

    not_enrolled = await client.get(
        "/me/progress", params={"course_id": str(course.id)}, headers=headers
    )
    unpublished = await client.get(
        "/me/progress", params={"course_id": str(hidden.id)}, headers=headers
    )
    unknown = await client.get(
        "/me/progress", params={"course_id": str(uuid.uuid4())}, headers=headers
    )
    missing = await client.get("/me/progress", headers=headers)

    assert (not_enrolled.status_code, not_enrolled.json()["error"]["code"]) == (403, "NOT_ENROLLED")
    assert (unpublished.status_code, unpublished.json()["error"]["code"]) == (
        404,
        "COURSE_NOT_FOUND",
    )
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "COURSE_NOT_FOUND")
    assert missing.status_code == 422


async def test_admin_reads_course_progress_without_enrolling(
    client: AsyncClient, db: AsyncSession
) -> None:
    hidden = await make_course(db, published=False)
    admin = await make_user(db, is_admin=True)

    response = await client.get(
        "/me/progress", params={"course_id": str(hidden.id)}, headers=auth_headers(admin)
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_my_courses_computes_percentages(client: AsyncClient, db: AsyncSession) -> None:
    untouched = await make_course(db, "Untouched")
    partial = await make_course(db, "Partial")
    done = await make_course(db, "Done")
    empty = await make_course(db, "Empty")
    await lessons(db, untouched, 2)
    p1, p2, p3 = await lessons(db, partial, 3)
    (d1,) = await lessons(db, done, 1)
    student, other = await make_user(db), await make_user(db)
    for minutes, course in enumerate((untouched, partial, done, empty)):
        await enroll(db, student, course, minutes)
    await enroll(db, other, partial)
    await watch(client, student, (p1, 600), (p2, 100), (d1, 580))
    await watch(client, other, (p3, 600))  # someone else's progress doesn't count

    response = await client.get("/me/courses", headers=auth_headers(student))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    rows = {c["title"]: c for c in body["items"]}
    assert [c["title"] for c in body["items"]] == ["Empty", "Done", "Partial", "Untouched"]
    assert {
        t: (c["lesson_count"], c["completed_count"], c["percent_complete"]) for t, c in rows.items()
    } == {
        "Untouched": (2, 0, 0),
        "Partial": (3, 1, 33),
        "Done": (1, 1, 100),
        "Empty": (0, 0, 0),
    }
    assert rows["Untouched"]["last_watched_at"] is None
    assert rows["Partial"]["last_watched_at"] is not None
    assert rows["Done"]["enrolled_at"] == (T0 + timedelta(minutes=2)).isoformat().replace(
        "+00:00", "Z"
    )


async def test_my_courses_hides_unpublished_and_unenrolled(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await make_course(db, "Mine")
    hidden = await make_course(db, "Hidden", published=False)
    await make_course(db, "Not enrolled")
    student = await make_user(db)
    await enroll(db, student, mine)
    await enroll(db, student, hidden)

    response = await client.get("/me/courses", headers=auth_headers(student))

    assert [c["title"] for c in response.json()["items"]] == ["Mine"]
    assert response.json()["total"] == 1


async def test_my_courses_pages(client: AsyncClient, db: AsyncSession) -> None:
    student = await make_user(db)
    for n in range(3):
        await enroll(db, student, await make_course(db, f"C{n}"), n)

    response = await client.get(
        "/me/courses", params={"page": 2, "page_size": 2}, headers=auth_headers(student)
    )

    body = response.json()
    assert (body["total"], [c["title"] for c in body["items"]]) == (3, ["C0"])


async def test_my_courses_is_one_query_for_the_page(client: AsyncClient, db: AsyncSession) -> None:
    student = await make_user(db)
    for n in range(5):
        course = await make_course(db, f"C{n}")
        await lessons(db, course, 3)
        await enroll(db, student, course, n)
    statements: list[str] = []

    def count(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(get_engine().sync_engine, "before_cursor_execute", count)
    try:
        response = await client.get("/me/courses", headers=auth_headers(student))
    finally:
        event.remove(get_engine().sync_engine, "before_cursor_execute", count)

    assert response.status_code == 200
    # Login's user lookup, the page with its counts, the total.
    assert len(statements) == 3
