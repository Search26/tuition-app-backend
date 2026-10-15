import unicodedata
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from app.models import LessonStatus
from tests.factories import make_course, make_lesson

pytestmark = pytest.mark.usefixtures("db")


async def test_pagination_full_partial_and_past_the_end(
    client: AsyncClient, db: AsyncSession
) -> None:
    for i in range(25):
        await make_course(db, f"Course {i:02}", minutes_after_base=i)

    pages = [
        (await client.get("/courses", params={"page": p, "page_size": 10})).json()
        for p in (1, 2, 3, 4)
    ]

    assert [len(p["items"]) for p in pages] == [10, 10, 5, 0]
    assert all(p["total"] == 25 for p in pages)
    ids = [item["id"] for p in pages for item in p["items"]]
    assert len(set(ids)) == 25
    # Newest first.
    assert pages[0]["items"][0]["title"] == "Course 24"
    assert pages[2]["items"][-1]["title"] == "Course 00"


async def test_empty_catalogue(client: AsyncClient) -> None:
    response = await client.get("/courses")

    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page": 0}, "query.page"),
        ({"page_size": 0}, "query.page_size"),
        ({"page_size": 101}, "query.page_size"),
        ({"q": "x" * 101}, "query.q"),
    ],
)
async def test_bad_query_params(client: AsyncClient, params: dict, field: str) -> None:
    response = await client.get("/courses", params=params)

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["field"] == field


@pytest.mark.parametrize(
    "q",
    [
        "lập trình",
        "LẬP TRÌNH",
        "lap trinh",
        unicodedata.normalize("NFD", "lập trình"),
        "  python  ",
    ],
)
async def test_search_is_case_and_accent_insensitive(
    client: AsyncClient, db: AsyncSession, q: str
) -> None:
    await make_course(db, "Lập trình Python cơ bản")
    await make_course(db, "Tiếng Anh giao tiếp")

    response = await client.get("/courses", params={"q": q})

    titles = [item["title"] for item in response.json()["items"]]
    assert titles == ["Lập trình Python cơ bản"]


@pytest.mark.parametrize("q", ["đặt", "dat", "ĐẶT"])
async def test_search_matches_d_with_stroke(client: AsyncClient, db: AsyncSession, q: str) -> None:
    await make_course(db, "Đặt món ăn bằng tiếng Anh")

    response = await client.get("/courses", params={"q": q})

    assert response.json()["total"] == 1


async def test_search_treats_wildcards_literally(client: AsyncClient, db: AsyncSession) -> None:
    await make_course(db, "Giảm 50% học phí")
    await make_course(db, "Một khoá khác")

    percent = (await client.get("/courses", params={"q": "%"})).json()
    underscore = (await client.get("/courses", params={"q": "_"})).json()

    assert [i["title"] for i in percent["items"]] == ["Giảm 50% học phí"]
    assert underscore["total"] == 0


async def test_blank_search_means_no_filter(client: AsyncClient, db: AsyncSession) -> None:
    await make_course(db, "A")
    await make_course(db, "B")

    response = await client.get("/courses", params={"q": "   "})

    assert response.json()["total"] == 2


async def test_unpublished_course_is_hidden(client: AsyncClient, db: AsyncSession) -> None:
    hidden = await make_course(db, "Bản nháp", published=False)

    listing = await client.get("/courses")
    detail = await client.get(f"/courses/{hidden.id}")

    assert listing.json()["total"] == 0
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "COURSE_NOT_FOUND"


async def test_list_includes_lesson_count(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)
    await make_course(db, "Empty", minutes_after_base=-1)
    for position in (1, 2, 3):
        await make_lesson(db, course, position)

    items = (await client.get("/courses")).json()["items"]

    assert [(i["title"], i["lesson_count"]) for i in items] == [("Khoá học mẫu", 3), ("Empty", 0)]


async def test_detail_sorts_lessons_by_position(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)
    for position in (3, 1, 2):
        await make_lesson(db, course, position)

    response = await client.get(f"/courses/{course.id}")

    assert response.status_code == 200
    assert [lesson["position"] for lesson in response.json()["lessons"]] == [1, 2, 3]


async def test_detail_shows_hls_url_only_when_ready(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)
    await make_lesson(db, course, 1, status=LessonStatus.READY, hls_path="hls/c/l1/master.m3u8")
    await make_lesson(db, course, 2, status=LessonStatus.PROCESSING, hls_path="hls/c/l2/x.m3u8")
    await make_lesson(db, course, 3)

    lessons = (await client.get(f"/courses/{course.id}")).json()["lessons"]

    assert lessons[0]["hls_url"] == "http://localhost:8080/media/hls/c/l1/master.m3u8"
    assert lessons[1]["hls_url"] is None
    assert lessons[2]["hls_url"] is None
    assert [lesson["status"] for lesson in lessons] == ["ready", "processing", "draft"]
    assert all("hls_path" not in lesson for lesson in lessons)


async def test_detail_query_count_does_not_grow_with_lessons(
    client: AsyncClient, db: AsyncSession
) -> None:
    small = await make_course(db, "Small")
    big = await make_course(db, "Big")
    await make_lesson(db, small, 1)
    for position in range(1, 21):
        await make_lesson(db, big, position)

    async def count_queries(course_id: uuid.UUID) -> int:
        statements: list[str] = []

        def record(*args: object) -> None:
            statements.append(str(args[2]))

        sync_engine = get_engine().sync_engine
        event.listen(sync_engine, "before_cursor_execute", record)
        try:
            response = await client.get(f"/courses/{course_id}")
        finally:
            event.remove(sync_engine, "before_cursor_execute", record)
        assert response.status_code == 200
        return len(statements)

    assert await count_queries(small.id) == await count_queries(big.id) == 2


async def test_detail_bad_and_unknown_ids(client: AsyncClient) -> None:
    bad = await client.get("/courses/not-a-uuid")
    unknown = await client.get(f"/courses/{uuid.uuid4()}")

    assert bad.status_code == 422
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "COURSE_NOT_FOUND"
