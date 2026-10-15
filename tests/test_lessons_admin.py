import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import auth_headers, make_course, make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def admin(db: AsyncSession) -> dict[str, str]:
    return auth_headers(await make_user(db, is_admin=True))


async def test_lessons_get_next_position_by_default(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    course = await make_course(db)

    positions = [
        (
            await client.post(
                f"/admin/courses/{course.id}/lessons", json={"title": f"Bài {i}"}, headers=admin
            )
        ).json()["position"]
        for i in range(3)
    ]

    assert positions == [1, 2, 3]


async def test_lesson_create_update_delete(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    course = await make_course(db)

    created = await client.post(
        f"/admin/courses/{course.id}/lessons",
        json={"title": "Giới thiệu", "position": 5},
        headers=admin,
    )
    assert created.status_code == 201
    lesson = created.json()
    assert lesson["status"] == "draft"
    assert lesson["course_id"] == str(course.id)
    assert lesson["hls_path"] is None

    patched = await client.patch(
        f"/admin/lessons/{lesson['id']}", json={"position": 1}, headers=admin
    )
    assert patched.status_code == 200
    assert patched.json()["position"] == 1
    assert patched.json()["title"] == "Giới thiệu"

    detail = (await client.get(f"/admin/courses/{course.id}", headers=admin)).json()
    assert detail["lesson_count"] == 1

    deleted = await client.delete(f"/admin/lessons/{lesson['id']}", headers=admin)
    assert deleted.status_code == 204
    again = await client.delete(f"/admin/lessons/{lesson['id']}", headers=admin)
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "LESSON_NOT_FOUND"


@pytest.mark.parametrize(
    "body",
    [
        {"title": "x", "status": "ready"},
        {"title": "x", "hls_path": "hls/x.m3u8"},
        {"title": "x", "duration_seconds": 10},
        {"title": "x", "position": 0},
        {"title": ""},
    ],
)
async def test_lesson_create_rejects_bad_bodies(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], body: dict
) -> None:
    course = await make_course(db)

    response = await client.post(f"/admin/courses/{course.id}/lessons", json=body, headers=admin)

    assert response.status_code == 422


async def test_lesson_for_unknown_course(client: AsyncClient, admin: dict[str, str]) -> None:
    response = await client.post(
        f"/admin/courses/{uuid.uuid4()}/lessons", json={"title": "x"}, headers=admin
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"
