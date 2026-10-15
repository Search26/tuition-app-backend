import asyncio
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import AsyncClient
from minio.error import S3Error
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.storage import ensure_bucket, get_minio
from app.core.config import get_settings
from app.models import Course, Lesson, MediaAsset, MediaAssetStatus
from tests.factories import auth_headers, make_course, make_lesson, make_user

pytestmark = pytest.mark.usefixtures("db", "bucket")

VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048


@pytest.fixture
async def bucket() -> str:
    """The test bucket, emptied before each test."""
    await ensure_bucket()
    name = get_settings().minio_bucket
    client = get_minio()
    for obj in client.list_objects(name, recursive=True):
        client.remove_object(name, obj.object_name)
    return name


@pytest.fixture
async def admin(db: AsyncSession) -> dict[str, str]:
    return auth_headers(await make_user(db, is_admin=True))


@pytest.fixture
async def lesson(db: AsyncSession) -> Lesson:
    return await make_lesson(db, await make_course(db), 1)


def _objects(bucket: str, prefix: str = "") -> list[str]:
    return [o.object_name for o in get_minio().list_objects(bucket, prefix=prefix, recursive=True)]


async def _request(
    client: AsyncClient,
    lesson_id: uuid.UUID,
    headers: dict[str, str],
    content_type: str = "video/mp4",
    size: int = len(VIDEO),
) -> httpx.Response:
    return await client.post(
        f"/admin/lessons/{lesson_id}/upload-request",
        json={"content_type": content_type, "size_bytes": size},
        headers=headers,
    )


async def _put(ticket: dict, body: bytes = VIDEO, content_type: str | None = None) -> int:
    headers = dict(ticket["headers"])
    if content_type is not None:
        headers["Content-Type"] = content_type
    async with httpx.AsyncClient() as http:
        response = await http.put(ticket["upload_url"], content=body, headers=headers)
    return response.status_code


async def _complete(
    client: AsyncClient, lesson_id: uuid.UUID, asset_id: str, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        f"/admin/lessons/{lesson_id}/upload-complete", json={"asset_id": asset_id}, headers=headers
    )


async def _upload(client: AsyncClient, lesson: Lesson, admin: dict[str, str]) -> dict:
    ticket = (await _request(client, lesson.id, admin)).json()
    assert await _put(ticket) == 200
    response = await _complete(client, lesson.id, ticket["asset_id"], admin)
    assert response.status_code == 200
    return response.json()


async def test_request_put_complete_happy_path(
    client: AsyncClient, admin: dict[str, str], lesson: Lesson, bucket: str
) -> None:
    requested = await _request(client, lesson.id, admin)
    assert requested.status_code == 201
    ticket = requested.json()
    assert ticket["method"] == "PUT"
    assert ticket["headers"] == {"Content-Type": "video/mp4"}

    assert await _put(ticket) == 200
    completed = await _complete(client, lesson.id, ticket["asset_id"], admin)

    assert completed.status_code == 200
    asset = completed.json()
    assert asset["status"] == "uploaded"
    assert asset["size_bytes"] == len(VIDEO)
    key = f"original/{lesson.course_id}/{lesson.id}/{ticket['asset_id']}.mp4"
    assert asset["original_key"] == key
    assert _objects(bucket) == [key]


async def test_ticket_is_signed_for_the_public_host_and_expires_in_15_minutes(
    client: AsyncClient, admin: dict[str, str], lesson: Lesson
) -> None:
    ticket = (await _request(client, lesson.id, admin, content_type="Video/QuickTime")).json()

    url = urlsplit(ticket["upload_url"])
    assert url.netloc == get_settings().minio_public_endpoint
    assert parse_qs(url.query)["X-Amz-Expires"] == ["900"]
    assert url.path.endswith(f"/{lesson.id}/{ticket['asset_id']}.mov")


@pytest.mark.parametrize(
    ("content_type", "size", "code"),
    [
        ("image/png", 100, "UNSUPPORTED_CONTENT_TYPE"),
        ("video/webm", 100, "UNSUPPORTED_CONTENT_TYPE"),
        ("video/mp4", 0, "VALIDATION_ERROR"),
        ("video/mp4", 2 * 1024**3 + 1, "FILE_TOO_LARGE"),
    ],
)
async def test_request_rejects_bad_type_or_size(
    client: AsyncClient,
    db: AsyncSession,
    admin: dict[str, str],
    lesson: Lesson,
    content_type: str,
    size: int,
    code: str,
) -> None:
    response = await _request(client, lesson.id, admin, content_type=content_type, size=size)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert await db.scalar(select(func.count(MediaAsset.id))) == 0


async def test_request_rejects_a_client_chosen_key(
    client: AsyncClient, admin: dict[str, str], lesson: Lesson
) -> None:
    response = await client.post(
        f"/admin/lessons/{lesson.id}/upload-request",
        json={"content_type": "video/mp4", "size_bytes": 10, "key": "original/x/y/z.mp4"},
        headers=admin,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_unknown_lesson_is_404_on_both_routes(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    missing = uuid.uuid4()

    requested = await _request(client, missing, admin)
    completed = await _complete(client, missing, str(uuid.uuid4()), admin)

    assert requested.status_code == completed.status_code == 404
    assert requested.json()["error"]["code"] == "LESSON_NOT_FOUND"
    assert completed.json()["error"]["code"] == "LESSON_NOT_FOUND"


async def test_complete_before_upload_is_409_and_stays_pending(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson
) -> None:
    ticket = (await _request(client, lesson.id, admin)).json()

    response = await _complete(client, lesson.id, ticket["asset_id"], admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UPLOAD_NOT_FOUND"
    asset = await db.get(MediaAsset, uuid.UUID(ticket["asset_id"]))
    assert asset is not None and asset.status == MediaAssetStatus.PENDING

    # Uploading afterwards and completing again works.
    assert await _put(ticket) == 200
    assert (await _complete(client, lesson.id, ticket["asset_id"], admin)).status_code == 200


async def test_complete_rejects_a_different_stored_content_type(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson, bucket: str
) -> None:
    ticket = (await _request(client, lesson.id, admin)).json()
    assert await _put(ticket, content_type="text/html") == 200

    response = await _complete(client, lesson.id, ticket["asset_id"], admin)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_CONTENT_TYPE_MISMATCH"
    assert _objects(bucket) == []
    asset = await db.get(MediaAsset, uuid.UUID(ticket["asset_id"]))
    await db.refresh(asset)
    assert asset.status == MediaAssetStatus.FAILED
    assert "text/html" in (asset.error_msg or "")

    again = await _complete(client, lesson.id, ticket["asset_id"], admin)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ASSET_FAILED"


async def test_complete_rejects_a_file_larger_than_the_limit(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson, bucket: str
) -> None:
    get_settings().upload_max_bytes = 1024
    ticket = (await _request(client, lesson.id, admin, size=100)).json()
    assert await _put(ticket, body=VIDEO) == 200  # declared 100 bytes, sent 2 KB

    response = await _complete(client, lesson.id, ticket["asset_id"], admin)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert _objects(bucket) == []
    asset = await db.get(MediaAsset, uuid.UUID(ticket["asset_id"]))
    await db.refresh(asset)
    assert asset.status == MediaAssetStatus.FAILED


async def test_complete_is_idempotent_and_safe_concurrently(
    client: AsyncClient, admin: dict[str, str], lesson: Lesson
) -> None:
    ticket = (await _request(client, lesson.id, admin)).json()
    assert await _put(ticket) == 200

    responses = await asyncio.gather(
        *(_complete(client, lesson.id, ticket["asset_id"], admin) for _ in range(5))
    )

    assert [r.status_code for r in responses] == [200] * 5
    assert {r.json()["status"] for r in responses} == {"uploaded"}
    assert len({r.json()["updated_at"] for r in responses}) == 1


async def test_complete_with_another_lessons_asset_is_404(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson
) -> None:
    other = await make_lesson(db, await make_course(db, "Khoá khác"), 1)
    ticket = (await _request(client, other.id, admin)).json()
    assert await _put(ticket) == 200

    response = await _complete(client, lesson.id, ticket["asset_id"], admin)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ASSET_NOT_FOUND"


async def test_a_new_upload_supersedes_the_old_one(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson, bucket: str
) -> None:
    first = await _upload(client, lesson, admin)
    abandoned = (await _request(client, lesson.id, admin)).json()
    second = await _upload(client, lesson, admin)

    ids = set(await db.scalars(select(MediaAsset.id).where(MediaAsset.lesson_id == lesson.id)))
    assert ids == {uuid.UUID(second["id"])}
    assert _objects(bucket) == [second["original_key"]]
    assert first["id"] != second["id"] != abandoned["asset_id"]


async def test_processing_and_ready_assets_are_not_superseded(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], lesson: Lesson
) -> None:
    first = await _upload(client, lesson, admin)
    asset = await db.get(MediaAsset, uuid.UUID(first["id"]))
    asset.status = MediaAssetStatus.PROCESSING
    await db.commit()

    await _upload(client, lesson, admin)

    count = await db.scalar(
        select(func.count(MediaAsset.id)).where(MediaAsset.lesson_id == lesson.id)
    )
    assert count == 2


async def test_deleting_a_lesson_or_course_removes_its_files(
    client: AsyncClient, db: AsyncSession, admin: dict[str, str], bucket: str
) -> None:
    course = await make_course(db)
    kept_course = await make_course(db, "Khoá giữ lại")
    lesson_a = await make_lesson(db, course, 1)
    lesson_b = await make_lesson(db, course, 2)
    kept = await make_lesson(db, kept_course, 1)
    for lesson in (lesson_a, lesson_b, kept):
        await _upload(client, lesson, admin)

    assert (await client.delete(f"/admin/lessons/{lesson_a.id}", headers=admin)).status_code == 204
    assert _objects(bucket, f"original/{course.id}/{lesson_a.id}/") == []
    assert len(_objects(bucket, f"original/{course.id}/")) == 1

    assert (await client.delete(f"/admin/courses/{course.id}", headers=admin)).status_code == 204
    assert _objects(bucket, f"original/{course.id}/") == []
    assert len(_objects(bucket)) == 1
    remaining = await db.scalar(select(func.count(MediaAsset.id)))
    assert remaining == 1
    assert await db.get(Course, kept_course.id) is not None


async def test_bucket_is_private(
    client: AsyncClient, admin: dict[str, str], lesson: Lesson, bucket: str
) -> None:
    asset = await _upload(client, lesson, admin)

    with pytest.raises(S3Error) as exc_info:
        get_minio().get_bucket_policy(bucket)
    assert exc_info.value.code == "NoSuchBucketPolicy"

    settings = get_settings()
    scheme = "https" if settings.minio_secure else "http"
    url = f"{scheme}://{settings.minio_endpoint}/{bucket}/{asset['original_key']}"
    async with httpx.AsyncClient() as http:
        assert (await http.get(url)).status_code == 403


async def test_put_after_the_url_expires_is_refused(
    client: AsyncClient, admin: dict[str, str], lesson: Lesson
) -> None:
    get_settings().upload_url_ttl_seconds = 1
    ticket = (await _request(client, lesson.id, admin)).json()
    await asyncio.sleep(2)

    assert await _put(ticket) == 403


async def test_upload_routes_need_an_admin(
    client: AsyncClient, db: AsyncSession, lesson: Lesson
) -> None:
    user = auth_headers(await make_user(db))
    body = {"asset_id": str(uuid.uuid4())}
    paths = [
        f"/admin/lessons/{lesson.id}/upload-request",
        f"/admin/lessons/{lesson.id}/upload-complete",
    ]

    for path in paths:
        anonymous = await client.post(path, json=body)
        non_admin = await client.post(path, json=body, headers=user)
        assert (anonymous.status_code, non_admin.status_code) == (401, 403)
