import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models import LessonStatus
from app.schemas._text import Description, Title, reject_explicit_null
from app.services import media_urls


class LessonPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    position: int
    duration_seconds: int | None
    status: LessonStatus
    hls_path: str | None = Field(exclude=True)
    thumbnail_path: str | None = Field(exclude=True)

    # Playback goes through GET /lessons/{id}/stream; only the thumbnail is signed here.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str | None:
        if self.status != LessonStatus.READY or not self.thumbnail_path:
            return None
        return media_urls.file_url(self.thumbnail_path)


class AdminLesson(LessonPublic):
    course_id: uuid.UUID
    # Admins see the raw storage keys; the public schema hides them.
    hls_path: str | None
    thumbnail_path: str | None
    created_at: datetime
    updated_at: datetime


class LessonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description = ""
    position: int | None = Field(default=None, ge=1, le=10000)


class LessonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    position: int | None = Field(default=None, ge=1, le=10000)

    _no_null = field_validator("title", "description", "position", mode="before")(
        reject_explicit_null
    )
