"""Lesson playback for enrolled students."""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.stream import StreamOut
from app.services import streams

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.get("/{lesson_id}/stream")
async def get_stream(lesson_id: uuid.UUID, user: CurrentUser, session: DbSession) -> StreamOut:
    return await streams.stream(session, user, lesson_id)
