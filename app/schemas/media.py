import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import MediaAssetStatus


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=1)

    @field_validator("content_type")
    @classmethod
    def _normalize(cls, value: str) -> str:
        # "Video/MP4; codecs=avc1" -> "video/mp4"; the allowlist check lives in the service.
        return value.split(";", 1)[0].strip().lower()


class UploadTicket(BaseModel):
    asset_id: uuid.UUID
    upload_url: str
    method: Literal["PUT"] = "PUT"
    # The client must send exactly these headers with the PUT.
    headers: dict[str, str]
    expires_at: datetime


class UploadComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: uuid.UUID


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lesson_id: uuid.UUID
    original_key: str
    status: MediaAssetStatus
    content_type: str
    size_bytes: int | None
    error_msg: str | None
    created_at: datetime
    updated_at: datetime
