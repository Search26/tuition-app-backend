from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_health_checks
from app.schemas.health import HealthResponse
from app.services.health import Check, run_health_checks

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(
    response: Response, checks: Annotated[dict[str, Check], Depends(get_health_checks)]
) -> HealthResponse:
    result = await run_health_checks(checks)
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
