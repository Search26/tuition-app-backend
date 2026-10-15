"""Storage key layout. Keys are built only from server-side ids, never from client input."""

import uuid

# Allowed source video types and the extension each gets in its key.
VIDEO_EXTENSIONS: dict[str, str] = {"video/mp4": "mp4", "video/quicktime": "mov"}


def course_prefix(course_id: uuid.UUID) -> str:
    return f"original/{course_id}/"


def lesson_prefix(course_id: uuid.UUID, lesson_id: uuid.UUID) -> str:
    return f"{course_prefix(course_id)}{lesson_id}/"


def original_key(
    course_id: uuid.UUID, lesson_id: uuid.UUID, asset_id: uuid.UUID, content_type: str
) -> str:
    return f"{lesson_prefix(course_id, lesson_id)}{asset_id}.{VIDEO_EXTENSIONS[content_type]}"


def hls_course_prefix(course_id: uuid.UUID) -> str:
    return f"hls/{course_id}/"


def hls_lesson_prefix(course_id: uuid.UUID, lesson_id: uuid.UUID) -> str:
    return f"{hls_course_prefix(course_id)}{lesson_id}/"


def hls_run_prefix(course_id: uuid.UUID, lesson_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """Each transcode attempt writes here, so the live video is never overwritten in place."""
    return f"{hls_lesson_prefix(course_id, lesson_id)}{run_id}/"


def prefix_of(key: str) -> str:
    """`hls/c/l/run/master.m3u8` -> `hls/c/l/run/`."""
    return key.rsplit("/", 1)[0] + "/"


def transcode_log_key(asset_id: uuid.UUID, attempt: int) -> str:
    return f"logs/transcode/{asset_id}/{attempt}.log"
