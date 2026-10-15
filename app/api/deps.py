from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.redis import get_redis
from app.core import security
from app.core.config import Settings, get_settings
from app.core.errors import AuthError, ForbiddenError
from app.db.session import get_session
from app.models import User
from app.services.health import Check, default_checks
from app.services.rate_limit import LoginRateLimiter

_bearer = HTTPBearer(auto_error=False)


def get_health_checks() -> dict[str, Check]:
    return default_checks()


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


def get_login_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter(get_redis(), get_settings())


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    session: DbSession,
    settings: AppSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AuthError("NOT_AUTHENTICATED", "Missing bearer token")
    try:
        user_id = security.decode_access_token(
            credentials.credentials, settings.jwt_secret.get_secret_value()
        )
    except security.ExpiredAccessToken:
        raise AuthError("TOKEN_EXPIRED", "Access token has expired") from None
    except security.InvalidAccessToken:
        raise AuthError("INVALID_TOKEN", "Access token is invalid") from None

    user = await session.get(User, user_id)
    if user is None:
        raise AuthError("INVALID_TOKEN", "Access token is invalid")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ForbiddenError()
    return user


AdminUser = Annotated[User, Depends(require_admin)]
