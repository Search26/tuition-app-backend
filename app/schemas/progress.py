import enum
import uuid
from datetime import UTC, datetime
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.models import Course
from app.schemas.course import CourseSummary

MAX_BATCH_ITEMS = 500


class ProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: uuid.UUID
    position_seconds: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    # Accepted but not trusted: the server decides completion from position and duration.
    completed: bool = False
    # Must carry an offset; a naive time can't be ordered against other devices.
    client_updated_at: AwareDatetime

    @field_validator("client_updated_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ProgressBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProgressIn] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)


class ProgressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lesson_id: uuid.UUID
    position_seconds: float
    completed: bool
    client_updated_at: datetime
    updated_at: datetime


class SyncStatus(enum.StrEnum):
    APPLIED = "applied"
    SKIPPED_STALE = "skipped_stale"
    REJECTED = "rejected"


class ProgressResult(BaseModel):
    lesson_id: uuid.UUID
    status: SyncStatus
    # LESSON_NOT_FOUND or NOT_ENROLLED when rejected, otherwise null.
    reason: str | None = None
    # What the server holds after the merge; null when rejected.
    progress: ProgressOut | None = None


class ProgressBatchResult(BaseModel):
    results: list[ProgressResult]


class ProgressList(BaseModel):
    items: list[ProgressOut]


class MyCourse(CourseSummary):
    enrolled_at: datetime
    completed_count: int
    percent_complete: int
    last_watched_at: datetime | None

    @classmethod
    def from_progress_row(
        cls,
        course: Course,
        enrolled_at: datetime,
        lesson_count: int,
        completed_count: int,
        last_watched_at: datetime | None,
    ) -> Self:
        summary = CourseSummary.from_row(course, lesson_count)
        percent = completed_count * 100 // lesson_count if lesson_count else 0
        return cls(
            **summary.model_dump(),
            enrolled_at=enrolled_at,
            completed_count=completed_count,
            percent_complete=percent,
            last_watched_at=last_watched_at,
        )
