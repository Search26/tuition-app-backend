from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import AppSettings, DbSession, get_login_rate_limiter
from app.schemas.auth import LoginIn, RefreshIn, RegisterIn, TokenPair
from app.schemas.user import UserOut
from app.services import auth as auth_service
from app.services.rate_limit import LoginRateLimiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: DbSession) -> UserOut:
    user = await auth_service.register(
        session, body.email, body.password.get_secret_value(), body.name
    )
    return UserOut.model_validate(user)


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    limiter: Annotated[LoginRateLimiter, Depends(get_login_rate_limiter)],
) -> TokenPair:
    client_ip = request.client.host if request.client else "unknown"
    await limiter.check(client_ip, body.email)

    user = await auth_service.authenticate(session, body.email, body.password.get_secret_value())
    if user is None:
        await limiter.record_failure(body.email)
        raise auth_service.invalid_credentials()

    await limiter.reset(body.email)
    return await auth_service.issue_tokens(session, settings, user.id)


@router.post("/refresh")
async def refresh(body: RefreshIn, session: DbSession, settings: AppSettings) -> TokenPair:
    return await auth_service.rotate_refresh_token(
        session, settings, body.refresh_token.get_secret_value()
    )
