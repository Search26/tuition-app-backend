import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Enrollment
from tests.auth_helpers import bearer, register_and_login
from tests.factories import auth_headers, make_course, make_user

pytestmark = pytest.mark.usefixtures("db")


async def _enrollment_count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(Enrollment)) or 0


async def test_enroll_is_idempotent(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)
    tokens = await register_and_login(client)
    headers = bearer(tokens["access_token"])

    first = await client.post(f"/courses/{course.id}/enroll", headers=headers)
    second = await client.post(f"/courses/{course.id}/enroll", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["course_id"] == str(course.id)
    assert await _enrollment_count(db) == 1


async def test_concurrent_enrolls_make_one_row(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)
    headers = auth_headers(await make_user(db))

    responses = await asyncio.gather(
        *(client.post(f"/courses/{course.id}/enroll", headers=headers) for _ in range(10))
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 9 + [201]
    assert len({r.json()["enrolled_at"] for r in responses}) == 1
    assert await _enrollment_count(db) == 1


async def test_enroll_requires_login(client: AsyncClient, db: AsyncSession) -> None:
    course = await make_course(db)

    response = await client.post(f"/courses/{course.id}/enroll")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_enroll_in_unpublished_or_missing_course(
    client: AsyncClient, db: AsyncSession
) -> None:
    hidden = await make_course(db, published=False)
    headers = auth_headers(await make_user(db))

    for course_id in (hidden.id, uuid.uuid4()):
        response = await client.post(f"/courses/{course_id}/enroll", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"
    assert await _enrollment_count(db) == 0
