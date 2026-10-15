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
