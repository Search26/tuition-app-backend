"""PUT /me/progress: the merge rule, per-item statuses and concurrency (Sprint 6 design doc)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from app.models import Course, Enrollment, Lesson, LessonStatus, Progress, User
from app.services import progress as progress_service
from tests.factories import auth_headers, make_course, make_lesson, make_user

pytestmark = pytest.mark.usefixtures("db")

T0 = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def at(hours: float) -> str:
    return (T0 + timedelta(hours=hours)).isoformat()


def item(lesson: Lesson, position: float, when: str, **extra: Any) -> dict[str, Any]:
    return {
        "lesson_id": str(lesson.id),
        "position_seconds": position,
        "client_updated_at": when,
        **extra,
    }


async def lesson_600s(db: AsyncSession, course: Course, position: int = 1) -> Lesson:
    return await make_lesson(db, course, position, status=LessonStatus.READY, duration_seconds=600)


async def enrolled_student(db: AsyncSession, course: Course) -> User:
    user = await make_user(db)
    db.add(Enrollment(user_id=user.id, course_id=course.id))
    await db.commit()
    return user


async def put(client: AsyncClient, user: User, *items: dict[str, Any]) -> Response:
    return await client.put("/me/progress", json={"items": list(items)}, headers=auth_headers(user))


async def stored(db: AsyncSession, user: User, lesson: Lesson) -> Progress | None:
    # populate_existing: other requests changed the row since this session last loaded it.
    return await db.scalar(
        select(Progress)
        .where(Progress.user_id == user.id, Progress.lesson_id == lesson.id)
        .execution_options(populate_existing=True)
    )


@pytest.fixture
async def course(db: AsyncSession) -> Course:
    return await make_course(db)


async def test_worked_examples_from_the_design_doc(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    """Each step starts from the row the step before left: rows 1 to 6 of the doc's table."""
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)
    steps = [
        # (position, client time, expected status, stored position, stored time, completed)
        (120, 0, "applied", 120, 0, False),
        (120, 0, "skipped_stale", 120, 0, False),  # same item again
        (80, -1, "skipped_stale", 120, 0, False),  # older
        (590, 1, "applied", 590, 1, True),  # past 90% of 600 s
        (30, 2, "applied", 30, 2, True),  # rewatch: position moves, still done
        (600, -2, "skipped_stale", 30, 2, True),  # late device: already done, nothing changes
    ]
    for position, hours, status, want_position, want_hours, want_done in steps:
        response = await put(client, student, item(lesson, position, at(hours)))

        assert response.status_code == 200
        (result,) = response.json()["results"]
        assert result["status"] == status, (position, hours)
        assert result["reason"] is None
        progress = result["progress"]
        assert progress["position_seconds"] == want_position
        assert datetime.fromisoformat(progress["client_updated_at"]) == T0 + timedelta(
            hours=want_hours
        )
        assert progress["completed"] is want_done


async def test_same_batch_twice_changes_nothing_not_even_updated_at(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lessons = [await lesson_600s(db, course, n) for n in range(1, 4)]
    student = await enrolled_student(db, course)
    batch = [item(lesson, 100 * n, at(n)) for n, lesson in enumerate(lessons, 1)]

    first = await put(client, student, *batch)
    rows_after_first = [(await stored(db, student, lesson)) for lesson in lessons]
    snapshot = [
        (p.position_seconds, p.completed, p.client_updated_at, p.updated_at)
        for p in rows_after_first
        if p
    ]
    second = await put(client, student, *batch)
    rows_after_second = [(await stored(db, student, lesson)) for lesson in lessons]

    assert [r["status"] for r in first.json()["results"]] == ["applied"] * 3
    assert [r["status"] for r in second.json()["results"]] == ["skipped_stale"] * 3
    assert snapshot == [
        (p.position_seconds, p.completed, p.client_updated_at, p.updated_at)
        for p in rows_after_second
        if p
    ]
    assert len(snapshot) == 3
    assert [r["progress"] for r in first.json()["results"]] == [
        r["progress"] for r in second.json()["results"]
    ]


async def test_late_completion_turns_the_flag_on_but_keeps_the_newer_position(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)
    await put(client, student, item(lesson, 30, at(2)))  # phone, opened later

    response = await put(client, student, item(lesson, 600, at(1)))  # iPad finished earlier

    (result,) = response.json()["results"]
    assert result["status"] == "skipped_stale"
    assert result["progress"]["position_seconds"] == 30
    assert result["progress"]["completed"] is True
    assert datetime.fromisoformat(result["progress"]["client_updated_at"]) == T0 + timedelta(
        hours=2
    )


async def test_either_arrival_order_ends_in_the_same_row(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    ipad, phone = item, item
    results = []
    for order in ((600, 1, 30, 2), (30, 2, 600, 1)):
        lesson = await lesson_600s(db, course, len(results) + 1)
        student = await enrolled_student(db, course)
        await put(client, student, ipad(lesson, order[0], at(order[1])))
        await put(client, student, phone(lesson, order[2], at(order[3])))
        row = await stored(db, student, lesson)
        assert row is not None
        results.append((row.position_seconds, row.completed, row.client_updated_at))

    assert results[0] == results[1] == (30, True, T0 + timedelta(hours=2))


async def test_server_decides_completion_and_clamps_position(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    no_video = await make_lesson(db, course, 2)  # no duration yet
    student = await enrolled_student(db, course)

    response = await put(
        client,
        student,
        item(lesson, 100, at(0), completed=True),  # client says done; 100 < 540
        item(no_video, 9999, at(0), completed=True),
    )
    past_end = await put(client, student, item(lesson, 700, at(1)))

    claimed, unknown = response.json()["results"]
    assert claimed["progress"]["completed"] is False
    assert unknown["progress"] == {
        **unknown["progress"],
        "position_seconds": 9999,
        "completed": False,
    }
    (result,) = past_end.json()["results"]
    assert (result["progress"]["position_seconds"], result["progress"]["completed"]) == (600, True)


async def test_exactly_90_percent_is_complete(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)

    below = await put(client, student, item(lesson, 539.9, at(0)))
    exact = await put(client, student, item(lesson, 540, at(1)))

    assert below.json()["results"][0]["progress"]["completed"] is False
    assert exact.json()["results"][0]["progress"]["completed"] is True


async def test_duplicate_lesson_in_one_batch_keeps_the_newest(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)

    response = await put(
        client,
        student,
        item(lesson, 300, at(2)),
        item(lesson, 100, at(1)),
        item(lesson, 200, at(2)),  # equal time: the later one in the array wins
    )

    statuses = [r["status"] for r in response.json()["results"]]
    assert statuses == ["skipped_stale", "skipped_stale", "applied"]
    assert all(r["progress"]["position_seconds"] == 200 for r in response.json()["results"])


async def test_bad_items_are_rejected_and_the_rest_still_merges(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    good = await lesson_600s(db, course)
    other_course = await lesson_600s(db, await make_course(db, "Other"))
    hidden = await lesson_600s(db, await make_course(db, "Hidden", published=False))
    student = await enrolled_student(db, course)
    db.add(Enrollment(user_id=student.id, course_id=hidden.course_id))
    await db.commit()
    missing = {"lesson_id": str(uuid.uuid4()), "position_seconds": 1, "client_updated_at": at(0)}

    response = await put(
        client,
        student,
        item(other_course, 10, at(0)),
        missing,
        item(good, 10, at(0)),
        item(hidden, 10, at(0)),
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [(r["status"], r["reason"]) for r in results] == [
        ("rejected", "NOT_ENROLLED"),
        ("rejected", "LESSON_NOT_FOUND"),
        ("applied", None),
        ("rejected", "LESSON_NOT_FOUND"),
    ]
    assert [r["progress"] is None for r in results] == [True, True, False, True]
    assert await stored(db, student, other_course) is None


async def test_all_rejected_is_still_200(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    stranger = await make_user(db)

    response = await put(client, stranger, item(lesson, 10, at(0)))

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "rejected"


async def test_admin_records_progress_without_enrolling(
    client: AsyncClient, db: AsyncSession
) -> None:
    lesson = await lesson_600s(db, await make_course(db, published=False))
    admin = await make_user(db, is_admin=True)

    response = await put(client, admin, item(lesson, 10, at(0)))

    assert response.json()["results"][0]["status"] == "applied"


async def test_clock_more_than_a_day_ahead_is_clamped_to_server_time(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)
    now = datetime.now(UTC)

    far = await put(client, student, item(lesson, 10, (now + timedelta(days=30)).isoformat()))
    near_time = now + timedelta(hours=23)
    near = await put(client, student, item(lesson, 20, near_time.isoformat()))

    far_time = datetime.fromisoformat(far.json()["results"][0]["progress"]["client_updated_at"])
    assert now <= far_time <= datetime.now(UTC)
    # 23 h ahead is within the allowance, so it's kept as sent and wins over the clamped one.
    near_result = near.json()["results"][0]
    assert near_result["status"] == "applied"
    assert datetime.fromisoformat(near_result["progress"]["client_updated_at"]) == near_time


async def test_offsets_are_normalised_so_ordering_is_by_instant(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)
    await put(client, student, item(lesson, 100, "2026-10-15T10:00:00+00:00"))

    # 16:30 in Hanoi is 09:30 UTC: older, despite the bigger wall-clock number.
    response = await put(client, student, item(lesson, 200, "2026-10-15T16:30:00+07:00"))

    assert response.json()["results"][0]["status"] == "skipped_stale"


@pytest.mark.parametrize(
    "body",
    [
        {"items": []},
        {
            "items": [
                {
                    "lesson_id": "x",
                    "position_seconds": 1,
                    "client_updated_at": "2026-10-15T10:00:00Z",
                }
            ]
        },
        {
            "items": [
                {
                    "lesson_id": str(uuid.uuid4()),
                    "position_seconds": 1,
                    "client_updated_at": "2026-10-15T10:00:00",
                }
            ]
        },
        {
            "items": [
                {
                    "lesson_id": str(uuid.uuid4()),
                    "position_seconds": -1,
                    "client_updated_at": "2026-10-15T10:00:00Z",
                }
            ]
        },
        {
            "items": [
                {
                    "lesson_id": str(uuid.uuid4()),
                    "position_seconds": "NaN",
                    "client_updated_at": "2026-10-15T10:00:00Z",
                }
            ]
        },
        {
            "items": [
                {
                    "lesson_id": str(uuid.uuid4()),
                    "position_seconds": 1,
                    "client_updated_at": "2026-10-15T10:00:00Z",
                    "extra": 1,
                }
            ]
        },
        {
            "items": [
                {
                    "lesson_id": str(uuid.uuid4()),
                    "position_seconds": 1,
                    "client_updated_at": "2026-10-15T10:00:00Z",
                }
            ]
            * 501
        },
        [
            {
                "lesson_id": str(uuid.uuid4()),
                "position_seconds": 1,
                "client_updated_at": "2026-10-15T10:00:00Z",
            }
        ],
    ],
    ids=[
        "empty",
        "bad-uuid",
        "naive-time",
        "negative",
        "nan",
        "unknown-field",
        "501-items",
        "bare-array",
    ],
)
async def test_invalid_body_is_422(client: AsyncClient, db: AsyncSession, body: Any) -> None:
    user = await make_user(db)

    response = await client.put("/me/progress", json=body, headers=auth_headers(user))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_needs_a_login(client: AsyncClient) -> None:
    response = await client.put("/me/progress", json={"items": []})

    assert response.status_code == 401


async def test_concurrent_requests_on_one_lesson_all_succeed_and_the_newest_wins(
    client: AsyncClient, db: AsyncSession, course: Course
) -> None:
    lesson = await lesson_600s(db, course)
    student = await enrolled_student(db, course)
    minutes = list(range(20))
    minutes.reverse()  # newest first, so most requests arrive stale

    responses = await asyncio.gather(
        *(put(client, student, item(lesson, m * 10, at(m / 60))) for m in minutes)
    )

    assert [r.status_code for r in responses] == [200] * 20
    row = await stored(db, student, lesson)
    assert row is not None
    assert (row.position_seconds, row.client_updated_at) == (190, T0 + timedelta(minutes=19))


async def test_overlapping_batches_in_opposite_order_do_not_deadlock(
    client: AsyncClient, db: AsyncSession, course: Course, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Start both upserts at the same moment and hold each transaction open afterwards, so the two
    # really overlap. Without the sort by lesson id this deadlocks.
    upsert = progress_service._upsert
    both_ready = asyncio.Barrier(2)

    async def slow_upsert(*args: Any) -> Any:
        await both_ready.wait()
        result = await upsert(*args)
        await asyncio.sleep(0.05)
        return result

    monkeypatch.setattr(progress_service, "_upsert", slow_upsert)
    lessons = [
        Lesson(course_id=course.id, title=f"L{n}", position=n, duration_seconds=600)
        for n in range(1, 201)
    ]
    db.add_all(lessons)
    await db.commit()
    student = await enrolled_student(db, course)
    forward = [item(lesson, 10, at(0)) for lesson in lessons]
    backward = [item(lesson, 20, at(1)) for lesson in reversed(lessons)]

    for _ in range(3):
        responses = await asyncio.gather(
            put(client, student, *forward), put(client, student, *backward)
        )
        assert [r.status_code for r in responses] == [200, 200]

    rows = (await db.scalars(select(Progress).where(Progress.user_id == student.id))).all()
    assert len(rows) == 200
    assert {r.position_seconds for r in rows} == {20}


@pytest.mark.parametrize("size", [1, 200])
async def test_a_batch_is_one_insert_whatever_its_size(
    client: AsyncClient, db: AsyncSession, course: Course, size: int
) -> None:
    lessons = [
        Lesson(course_id=course.id, title=f"L{n}", position=n, duration_seconds=600)
        for n in range(1, size + 1)
    ]
    db.add_all(lessons)
    await db.commit()
    student = await enrolled_student(db, course)
    statements: list[str] = []

    def count(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(get_engine().sync_engine, "before_cursor_execute", count)
    try:
        response = await put(client, student, *(item(lesson, 10, at(0)) for lesson in lessons))
    finally:
        event.remove(get_engine().sync_engine, "before_cursor_execute", count)

    assert response.status_code == 200
    # Login's user lookup, then: access check, the upsert, the read-back.
    assert statements == ["SELECT", "SELECT", "INSERT", "SELECT"]
