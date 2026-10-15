from datetime import datetime

from pydantic import BaseModel


class StreamOut(BaseModel):
    url: str  # signed master.m3u8; every playlist and segment under it carries the same token
    expires_at: datetime
    duration_seconds: int | None
