import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import queue
from app.clients.redis import get_redis
from app.models import Lesson, MediaAsset, MediaAssetStatus
from tests.factories import auth_headers, make_course, make_lesson, make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def admin(db: AsyncSession) -> dict[str, str]:
    return auth_headers(await make_user(db, is_admin=True))


@pytest.fixture
async def lesson(db: AsyncSession) -> Lesson:
    return await make_lesson(db, await make_course(db), 1)


async def _asset(
    db: AsyncSession, lesson: Lesson, status: MediaAssetStatus, error: str | None = None
) -> MediaAsset:
    asset_id = uuid.uuid4()
    asset = MediaAsset(
        id=asset_id,
        lesson_id=lesson.id,
        original_key=f"original/{lesson.course_id}/{lesson.id}/{asset_id}.mp4",
        content_type="video/mp4",
        status=status,
        error_msg=error,
    )
    db.add(asset)
    await db.commit()
    return asset


async def _queued() -> list[bytes]:
    return await get_redis().zrange("arq:queue", 0, -1)


@pytest.mark.parametrize(
    "status", [MediaAssetStatus.UPLOADED, MediaAssetStatus.FAILED, MediaAssetStatus.READY]
)
async def test_retranscode_resets_and_enqueues(
    client: AsyncClient,
    db: AsyncSession,
    admin: dict[str, str],
    lesson: Lesson,
    status: MediaAssetStatus,
) -> None:
    asset = await _asset(db, lesson, status, error="old error")

    response = await client.post(f"/admin/assets/{asset.id}/retranscode", headers=admin)

    assert response.status_code == 202
    assert response.json()["status"] == "uploaded"
    assert response.json()["error_msg"] is None
    assert await _queued() == [f"transcode:{asset.id}".encode()]


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (MediaAssetStatus.PROCESSING, "ASSET_PROCESSING"),
        (MediaAssetStatus.PENDING, "UPLOAD_NOT_COMPLETE"),
    ],
)
async def test_retranscode_refuses_busy_or_incomplete_assets(
    client: AsyncClient,
    db: AsyncSession,
    admin: dict[str, str],
    lesson: Lesson,
    status: MediaAssetStatus,
    code: str,
) -> None:
    asset = await _asset(db, lesson, status)

    response = await client.post(f"/admin/assets/{asset.id}/retranscode", headers=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    assert await _queued() == []


async def test_retranscode_unknown_asset_is_404(client: AsyncClient, admin: dict[str, str]) -> None:
    response = await client.post(f"/admin/assets/{uuid.uuid4()}/retranscode", headers=admin)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ASSET_NOT_FOUND"


async def test_retranscode_with_the_queue_down_is_503_and_changes_nothing(
    client: AsyncClient,
    db: AsyncSession,
    admin: dict[str, str],
    lesson: Lesson,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_: uuid.UUID) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(queue, "enqueue_transcode", broken)
    asset = await _asset(db, lesson, MediaAssetStatus.FAILED, error="bad file")

    response = await client.post(f"/admin/assets/{asset.id}/retranscode", headers=admin)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    await db.refresh(asset)
    assert (asset.status, asset.error_msg) == (MediaAssetStatus.FAILED, "bad file")


async def test_list_lesson_assets_newest_first(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson
) -> None:
    first = await _asset(db, lesson, MediaAssetStatus.READY)
    second = await _asset(db, lesson, MediaAssetStatus.FAILED, error="ffmpeg exited")

    response = await client.get(f"/admin/lessons/{lesson.id}/assets", headers=admin)
    missing = await client.get(f"/admin/lessons/{uuid.uuid4()}/assets", headers=admin)

    assert response.status_code == 200
    assert [a["id"] for a in response.json()] == [str(second.id), str(first.id)]
    assert response.json()[0]["error_msg"] == "ffmpeg exited"
    assert missing.status_code == 404


async def test_asset_routes_need_an_admin(
    client: AsyncClient, db: AsyncSession, lesson: Lesson
) -> None:
    user = auth_headers(await make_user(db))
    asset = await _asset(db, lesson, MediaAssetStatus.FAILED)
    calls = [
        ("POST", f"/admin/assets/{asset.id}/retranscode"),
        ("GET", f"/admin/lessons/{lesson.id}/assets"),
    ]

    for method, path in calls:
        anonymous = await client.request(method, path)
        non_admin = await client.request(method, path, headers=user)
        assert (anonymous.status_code, non_admin.status_code) == (401, 403)
