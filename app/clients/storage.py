"""MinIO access. The SDK is blocking, so every network call runs in a worker thread."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import S3Error

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_MISSING_CODES = {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}
# Content types for transcode output, so Sprint 5 can serve the objects as stored.
_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".log": "text/plain; charset=utf-8",
}
_buckets_ready: set[str] = set()


@lru_cache
def get_minio() -> Minio:
    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_secure,
        region=settings.minio_region,
    )


@lru_cache
def get_signing_minio() -> Minio:
    """Signs URLs for the public host. With the region set, signing makes no network call."""
    settings = get_settings()
    return Minio(
        settings.minio_public_endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_public_secure,
        region=settings.minio_region,
    )


@dataclass(frozen=True)
class StoredObject:
    size: int
    content_type: str


def _ensure_bucket(bucket: str) -> None:
    client = get_minio()
    if not client.bucket_exists(bucket):
        try:
            # New buckets are private; no policy is ever set on this one.
            client.make_bucket(bucket)
        except S3Error as exc:
            if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise


async def ensure_bucket() -> None:
    bucket = get_settings().minio_bucket
    if bucket not in _buckets_ready:
        await asyncio.to_thread(_ensure_bucket, bucket)
        _buckets_ready.add(bucket)


def presign_put(key: str, ttl_seconds: int) -> str:
    return get_signing_minio().presigned_put_object(
        get_settings().minio_bucket, key, expires=timedelta(seconds=ttl_seconds)
    )


def _stat(bucket: str, key: str) -> StoredObject | None:
    try:
        info = get_minio().stat_object(bucket, key)
    except S3Error as exc:
        if exc.code in _MISSING_CODES:
            return None
        raise
    return StoredObject(size=info.size or 0, content_type=info.content_type or "")


async def stat(key: str) -> StoredObject | None:
    """Size and stored Content-Type of an object, or None when it does not exist."""
    return await asyncio.to_thread(_stat, get_settings().minio_bucket, key)


async def download(key: str, path: Path) -> None:
    await asyncio.to_thread(get_minio().fget_object, get_settings().minio_bucket, key, str(path))


def _upload(bucket: str, key: str, path: Path) -> None:
    content_type = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
    get_minio().fput_object(bucket, key, str(path), content_type=content_type)


async def upload_file(key: str, path: Path) -> None:
    await asyncio.to_thread(_upload, get_settings().minio_bucket, key, path)


def _upload_dir(bucket: str, prefix: str, root: Path) -> int:
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            _upload(bucket, prefix + path.relative_to(root).as_posix(), path)
            count += 1
    return count


async def upload_dir(prefix: str, root: Path) -> int:
    """Upload every file under `root` to `prefix` + its relative path; returns the file count."""
    return await asyncio.to_thread(_upload_dir, get_settings().minio_bucket, prefix, root)


async def delete_object(key: str) -> None:
    await asyncio.to_thread(get_minio().remove_object, get_settings().minio_bucket, key)


def _delete_prefix(bucket: str, prefix: str) -> None:
    client = get_minio()
    if not client.bucket_exists(bucket):
        return
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    _delete_keys(bucket, [obj.object_name for obj in objects if obj.object_name])


def _delete_keys(bucket: str, keys: list[str]) -> None:
    for error in get_minio().remove_objects(bucket, [DeleteObject(key) for key in keys]):
        raise RuntimeError(f"Could not delete {error.name}: {error.code}")


async def delete_keys_best_effort(keys: list[str]) -> None:
    """Remove these objects; a failure is logged, never raised (the files become orphans)."""
    if not keys:
        return
    try:
        await asyncio.to_thread(_delete_keys, get_settings().minio_bucket, keys)
    except Exception:
        logger.exception("Could not delete %d storage objects", len(keys))


async def delete_prefix_best_effort(prefix: str) -> None:
    """Remove every object under `prefix`; a failure is logged, never raised."""
    try:
        await asyncio.to_thread(_delete_prefix, get_settings().minio_bucket, prefix)
    except Exception:
        logger.exception("Could not delete storage prefix %s", prefix)
