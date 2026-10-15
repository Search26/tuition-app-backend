import asyncio
import glob
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from arq import Retry
from arq.jobs import Job
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import queue
from app.clients.redis import get_redis
from app.clients.storage import get_minio
from app.core.config import get_settings
from app.models import Lesson, LessonStatus, MediaAsset, MediaAssetStatus
from app.services import transcode_job
from app.services.media_keys import original_key, prefix_of
from tests.factories import make_course, make_lesson

pytestmark = pytest.mark.usefixtures("db", "bucket")


def _make_clip(path: Path, size: str, seconds: int, *, audio: bool) -> Path:
    args = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi"]
    args += ["-i", f"testsrc=duration={seconds}:size={size}:rate=25"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=duration={seconds}", "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    bad = root / "bad.mp4"
    bad.write_bytes(b"this is not a video" * 100)
    return {
        "audio": _make_clip(root / "audio.mp4", "640x360", 2, audio=True),
        "silent": _make_clip(root / "silent.mp4", "320x240", 2, audio=False),
        "bad": bad,
    }


def _objects(prefix: str = "") -> list[str]:
    bucket = get_settings().minio_bucket
    return [o.object_name for o in get_minio().list_objects(bucket, prefix=prefix, recursive=True)]


async def _asset(db: AsyncSession, lesson: Lesson, clip: Path) -> MediaAsset:
    asset_id = uuid.uuid4()
    key = original_key(lesson.course_id, lesson.id, asset_id, "video/mp4")
    await asyncio.to_thread(
        get_minio().fput_object, get_settings().minio_bucket, key, str(clip), "video/mp4"
    )
    asset = MediaAsset(
        id=asset_id,
        lesson_id=lesson.id,
        original_key=key,
        content_type="video/mp4",
        size_bytes=await asyncio.to_thread(os.path.getsize, clip),
        status=MediaAssetStatus.UPLOADED,
    )
    db.add(asset)
    await db.commit()
    return asset


@pytest.fixture
async def lesson(db: AsyncSession) -> Lesson:
    return await make_lesson(db, await make_course(db), 1)


async def _reload(db: AsyncSession, *rows: object) -> None:
    for row in rows:
        await db.refresh(row)


async def test_transcode_makes_hls_and_marks_the_lesson_ready(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    asset = await _asset(db, lesson, clips["audio"])

    result = await transcode_job.transcode({"job_try": 1}, str(asset.id))

    assert result == "ready"
    await _reload(db, asset, lesson)
    assert asset.status == MediaAssetStatus.READY
    assert asset.attempts == 1
    assert lesson.status == LessonStatus.READY
    assert lesson.duration_seconds == 2
    prefix = prefix_of(lesson.hls_path)
    assert prefix == asset.output_prefix
    assert prefix.startswith(f"hls/{lesson.course_id}/{lesson.id}/")
    assert lesson.thumbnail_path == prefix + "thumbnail.jpg"
    keys = set(_objects(prefix))
    assert {prefix + "master.m3u8", prefix + "v0/index.m3u8"} <= keys
    assert any(k.startswith(prefix + "v0/init") and k.endswith(".mp4") for k in keys)
    assert any(k.endswith(".m4s") for k in keys)
    assert _objects(f"logs/transcode/{asset.id}/") == [f"logs/transcode/{asset.id}/1.log"]

    bucket = get_settings().minio_bucket
    stat = get_minio().stat_object
    assert stat(bucket, prefix + "master.m3u8").content_type == "application/vnd.apple.mpegurl"
    segment = next(k for k in keys if k.endswith(".m4s"))
    assert stat(bucket, segment).content_type == "video/iso.segment"
    assert stat(bucket, prefix + "thumbnail.jpg").content_type == "image/jpeg"
    master = get_minio().get_object(bucket, prefix + "master.m3u8").read().decode()
    assert "RESOLUTION=640x360" in master and "mp4a" in master


async def test_a_silent_source_gets_video_only_variants(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    asset = await _asset(db, lesson, clips["silent"])

    assert await transcode_job.transcode({"job_try": 1}, str(asset.id)) == "ready"

    await _reload(db, lesson)
    master = get_minio().get_object(get_settings().minio_bucket, lesson.hls_path).read().decode()
    assert "RESOLUTION=320x240" in master
    assert "mp4a" not in master


async def test_an_unreadable_file_fails_without_retry(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    asset = await _asset(db, lesson, clips["bad"])

    result = await transcode_job.transcode({"job_try": 1}, str(asset.id))

    assert result == "failed"
    await _reload(db, asset, lesson)
    assert asset.status == MediaAssetStatus.FAILED
    assert (asset.error_msg or "").startswith("Not a readable video")
    assert "/tmp" not in (asset.error_msg or "")
    assert lesson.status == LessonStatus.DRAFT
    assert lesson.hls_path is None
    assert _objects("hls/") == []
    leftovers = glob.glob(os.path.join(get_settings().transcode_tmp_dir, f"transcode-{asset.id}-*"))
    assert leftovers == []


async def test_an_ffmpeg_failure_retries_once_then_fails(
    db: AsyncSession,
    lesson: Lesson,
    clips: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transcode_job.ffmpeg, "hls_args", lambda *a, **k: ["ffmpeg", "-i", "/no/such/file"]
    )
    asset = await _asset(db, lesson, clips["audio"])

    with pytest.raises(Retry):
        await transcode_job.transcode({"job_try": 1}, str(asset.id))
    await _reload(db, asset, lesson)
    assert asset.status == MediaAssetStatus.PROCESSING
    assert lesson.status == LessonStatus.PROCESSING

    result = await transcode_job.transcode({"job_try": 2}, str(asset.id))

    assert result == "failed"
    await _reload(db, asset, lesson)
    assert asset.status == MediaAssetStatus.FAILED
    assert asset.attempts == 2
    assert "exited with code" in (asset.error_msg or "")
    assert "/no/such/file" in (asset.error_msg or "")
    assert lesson.status == LessonStatus.DRAFT
    assert _objects("hls/") == []
    assert len(_objects(f"logs/transcode/{asset.id}/")) == 2


async def test_a_timeout_counts_as_a_failure(
    db: AsyncSession,
    lesson: Lesson,
    clips: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endless = ["ffmpeg", "-nostdin", "-re", "-f", "lavfi", "-i", "testsrc", "-f", "null", "-"]
    monkeypatch.setattr(transcode_job.ffmpeg, "hls_args", lambda *a, **k: endless)
    monkeypatch.setattr(get_settings(), "transcode_timeout_seconds", 1)
    asset = await _asset(db, lesson, clips["audio"])

    assert await transcode_job.transcode({"job_try": 2}, str(asset.id)) == "failed"

    await _reload(db, asset)
    assert "timed out" in (asset.error_msg or "")


async def test_a_new_video_replaces_the_live_one_only_when_ready(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    first = await _asset(db, lesson, clips["audio"])
    await transcode_job.transcode({"job_try": 1}, str(first.id))
    await _reload(db, lesson, first)
    old_prefix, old_original = first.output_prefix, first.original_key
    second = await _asset(db, lesson, clips["silent"])

    started = await transcode_job._start(second.id)  # mid-encode: the old video is still live
    await _reload(db, lesson)
    assert lesson.status == LessonStatus.READY
    assert lesson.hls_path == old_prefix + "master.m3u8"
    assert started is not None

    await db.execute(
        MediaAsset.__table__.update()
        .where(MediaAsset.id == second.id)
        .values(status=MediaAssetStatus.UPLOADED)
    )
    await db.commit()
    assert await transcode_job.transcode({"job_try": 1}, str(second.id)) == "ready"

    await _reload(db, lesson)
    assert prefix_of(lesson.hls_path) != old_prefix
    ids = set(await db.scalars(select(MediaAsset.id).where(MediaAsset.lesson_id == lesson.id)))
    assert ids == {second.id}
    assert _objects(old_original) == []
    assert _objects(started.prefix) == []  # the interrupted attempt's prefix was cleaned too
    # The replaced video stays until every signed URL to it has expired, then a job deletes it.
    assert _objects(old_prefix) != []
    job = await Job(f"delete:{old_prefix}", get_redis()).info()
    assert job is not None and job.function == "delete_prefix" and job.args == (old_prefix,)
    ttl_ms = get_settings().media_url_ttl_seconds * 1000
    assert abs(job.score - job.enqueue_time.timestamp() * 1000 - ttl_ms) < 5000
    assert await transcode_job.delete_prefix({}, old_prefix) == "deleted"
    assert _objects(old_prefix) == []


async def test_delete_prefix_never_touches_a_live_video_or_other_keys(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    asset = await _asset(db, lesson, clips["audio"])
    await transcode_job.transcode({"job_try": 1}, str(asset.id))
    await _reload(db, lesson)
    live = prefix_of(lesson.hls_path)

    assert await transcode_job.delete_prefix({}, live) == "live"
    for bad in ("hls/", "original/", f"{live}..", live.rstrip("/")):
        assert await transcode_job.delete_prefix({}, bad) == "refused"
    assert _objects(live) != []


async def test_a_replaced_video_is_deleted_at_once_if_the_queue_is_down(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _asset(db, lesson, clips["audio"])
    await transcode_job.transcode({"job_try": 1}, str(first.id))
    await _reload(db, lesson, first)
    old_prefix = first.output_prefix

    async def down(*_: object) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(queue, "enqueue_delete_prefix", down)
    second = await _asset(db, lesson, clips["silent"])
    assert await transcode_job.transcode({"job_try": 1}, str(second.id)) == "ready"

    assert _objects(old_prefix) == []


async def test_an_older_job_finishing_late_is_discarded(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    older = await _asset(db, lesson, clips["audio"])
    newer = await _asset(db, lesson, clips["silent"])
    assert await transcode_job.transcode({"job_try": 1}, str(newer.id)) == "ready"
    await _reload(db, lesson)
    live = lesson.hls_path

    assert await transcode_job.transcode({"job_try": 1}, str(older.id)) == "superseded"

    await _reload(db, lesson)
    assert lesson.hls_path == live
    assert await db.get(MediaAsset, older.id, populate_existing=True) is None
    assert _objects(older.original_key) == []
    assert {k.split("/")[3] for k in _objects("hls/")} == {live.split("/")[3]}


async def test_a_lesson_deleted_mid_job_leaves_no_files(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = await _asset(db, lesson, clips["audio"])
    real_encode = transcode_job._encode

    async def encode_then_delete(*args: object) -> object:
        info = await real_encode(*args)
        await db.delete(await db.get(Lesson, lesson.id))
        await db.commit()
        return info

    monkeypatch.setattr(transcode_job, "_encode", encode_then_delete)

    assert await transcode_job.transcode({"job_try": 1}, str(asset.id)) == "discarded"
    assert _objects("hls/") == []


async def test_a_job_for_a_missing_or_finished_asset_is_skipped(
    db: AsyncSession, lesson: Lesson, clips: dict[str, Path]
) -> None:
    asset = await _asset(db, lesson, clips["audio"])
    asset.status = MediaAssetStatus.FAILED
    await db.commit()

    assert await transcode_job.transcode({"job_try": 1}, str(asset.id)) == "skipped"
    assert await transcode_job.transcode({"job_try": 1}, str(uuid.uuid4())) == "skipped"
