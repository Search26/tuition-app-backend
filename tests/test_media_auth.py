"""The check nginx runs before every /media/ request (the nginx half is covered in CI)."""

import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import AsyncClient

from app.clients import storage
from app.core.config import get_settings
from app.services import media_urls


@pytest.fixture
async def video(bucket: str) -> str:
    """A run folder in the test bucket with a playlist and a segment."""
    run = f"hls/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "v0").mkdir()
        (root / "master.m3u8").write_text("#EXTM3U\nv0/index.m3u8\n")
        (root / "v0" / "seg_00000.m4s").write_bytes(b"segment-bytes")
        await storage.upload_dir(run, root)
    return run


def _signed_path(run: str) -> str:
    url, _ = media_urls.video_url(run + "master.m3u8")
    return urlsplit(url).path


async def _check(client: AsyncClient, uri: str, origin: str | None = None) -> httpx.Response:
    headers = {"X-Original-URI": uri}
    if origin:
        headers["Origin"] = origin
    return await client.get("/internal/media-auth", headers=headers)


async def test_valid_path_gets_a_presigned_minio_path_that_works(
    client: AsyncClient, video: str
) -> None:
    segment = _signed_path(video).replace("master.m3u8", "v0/seg_00000.m4s")

    response = await _check(client, segment)

    assert response.status_code == 204
    media_path = response.headers["x-media-path"]
    bucket = get_settings().minio_bucket
    assert media_path.startswith(f"/{bucket}/{video}v0/seg_00000.m4s?X-Amz-")
    assert "X-Amz-Expires=60&" in media_path
    # The path is what nginx proxies to: it must work against the internal MinIO endpoint.
    async with httpx.AsyncClient(base_url=f"http://{get_settings().minio_endpoint}") as minio:
        fetched = await minio.get(media_path)
    assert fetched.status_code == 200
    assert fetched.content == b"segment-bytes"


@pytest.mark.parametrize("uri", ["", "/media/", "/media/hls/a/b/c/master.m3u8", "/elsewhere"])
async def test_missing_or_malformed_path_is_a_bare_403(client: AsyncClient, uri: str) -> None:
    response = await _check(client, uri)

    assert response.status_code == 403
    assert response.content == b""
    assert "x-media-path" not in response.headers


async def test_tampered_or_expired_token_is_403(client: AsyncClient, video: str) -> None:
    path = _signed_path(video)
    exp = path.split("/")[3]
    expired_url, _ = media_urls.video_url(video + "master.m3u8", now=0)

    assert (await _check(client, path)).status_code == 204
    assert (await _check(client, path.replace(exp, str(int(exp) + 1)))).status_code == 403
    assert (await _check(client, urlsplit(expired_url).path)).status_code == 403


async def test_cors_header_echoes_only_listed_origins(client: AsyncClient, video: str) -> None:
    path = _signed_path(video)

    listed = await _check(client, path, origin="http://localhost:3000")
    other = await _check(client, path, origin="http://evil.example")

    assert listed.headers["x-media-cors"] == "http://localhost:3000"
    assert "x-media-cors" not in other.headers
