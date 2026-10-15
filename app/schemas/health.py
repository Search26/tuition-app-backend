from typing import Literal

from pydantic import BaseModel


class CheckResult(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: int
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]
