import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Enrollment, Lesson
from tests.factories import auth_headers, make_course, make_lesson, make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def admin(db: AsyncSession) -> dict[str, str]:
    return auth_headers(await make_user(db, is_admin=True))


async def test_course_crud(client: AsyncClient, admin: dict[str, str]) -> None:
    created = await client.post("/admin/courses", json={"title": "  Toán lớp 10 "}, headers=admin)
    assert created.status_code == 201
    course = created.json()
    assert course["title"] == "Toán lớp 10"
    assert course["is_published"] is False
    assert course["description"] == ""
    assert course["lessons"] == []

    patched = await client.patch(
        f"/admin/courses/{course['id']}", json={"is_published": True}, headers=admin
    )
    assert patched.status_code == 200
    assert patched.json()["is_published"] is True
    assert patched.json()["title"] == "Toán lớp 10"
    assert patched.json()["updated_at"] >= course["updated_at"]

    fetched = await client.get(f"/admin/courses/{course['id']}", headers=admin)
    assert fetched.json()["is_published"] is True

    deleted = await client.delete(f"/admin/courses/{course['id']}", headers=admin)
    assert deleted.status_code == 204
    gone = await client.get(f"/admin/courses/{course['id']}", headers=admin)
    assert gone.status_code == 404


async def test_admin_list_includes_unpublished(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    await make_course(db, "Public")
    await make_course(db, "Draft", published=False, minutes_after_base=1)

    items = (await client.get("/admin/courses", headers=admin)).json()["items"]

    assert [(i["title"], i["is_published"]) for i in items] == [("Draft", False), ("Public", True)]


async def test_patch_empty_body_is_a_no_op(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    course = await make_course(db, "Giữ nguyên")

    response = await client.patch(f"/admin/courses/{course.id}", json={}, headers=admin)

    assert response.status_code == 200
    assert response.json()["title"] == "Giữ nguyên"


@pytest.mark.parametrize(
    "body",
    [{"title": None}, {"title": "   "}, {"title": "x" * 201}, {"is_published": None}, {"x": 1}],
)
async def test_patch_rejects_bad_bodies(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], body: dict
) -> None:
    course = await make_course(db)

    response = await client.patch(f"/admin/courses/{course.id}", json=body, headers=admin)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_delete_course_with_enrollments_is_refused(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    course = await make_course(db)
    student = await make_user(db)
    db.add(Enrollment(user_id=student.id, course_id=course.id))
    await db.commit()

    response = await client.delete(f"/admin/courses/{course.id}", headers=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COURSE_HAS_ENROLLMENTS"
    assert await db.get(Course, course.id) is not None


async def test_delete_course_cascades_to_lessons(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    course = await make_course(db)
    await make_lesson(db, course, 1)

    await client.delete(f"/admin/courses/{course.id}", headers=admin)

    assert (await db.scalars(select(Lesson))).all() == []


async def test_unknown_course(client: AsyncClient, admin: dict[str, str]) -> None:
    course_id = uuid.uuid4()
    for method in ("GET", "PATCH", "DELETE"):
        kwargs = {"json": {}} if method == "PATCH" else {}
        response = await client.request(
            method, f"/admin/courses/{course_id}", headers=admin, **kwargs
        )
        assert response.status_code == 404, method
        assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"


ADMIN_ROUTES = [
    ("GET", "/admin/courses"),
    ("POST", "/admin/courses"),
    ("GET", "/admin/courses/{id}"),
    ("PATCH", "/admin/courses/{id}"),
    ("DELETE", "/admin/courses/{id}"),
    ("POST", "/admin/courses/{id}/lessons"),
    ("PATCH", "/admin/lessons/{id}"),
    ("DELETE", "/admin/lessons/{id}"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
async def test_admin_routes_need_an_admin(
    client: AsyncClient, db: AsyncSession, method: str, path: str
) -> None:
    url = path.format(id=uuid.uuid4())
    student = auth_headers(await make_user(db))

    anonymous = await client.request(method, url)
    not_admin = await client.request(method, url, headers=student)

    assert anonymous.status_code == 401
    assert not_admin.status_code == 403
    assert not_admin.json()["error"]["code"] == "FORBIDDEN"
