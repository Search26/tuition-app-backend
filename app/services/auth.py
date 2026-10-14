"""Register, login and refresh-token rotation. Routes stay thin and call these."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import Settings
from app.core.errors import AppError, AuthError
from app.models import RefreshToken, User
from app.schemas.auth import TokenPair


def invalid_credentials() -> AuthError:
    return AuthError("INVALID_CREDENTIALS", "Email or password is incorrect")


def _invalid_refresh() -> AuthError:
    return AuthError("INVALID_REFRESH_TOKEN", "Refresh token is invalid or expired")


async def register(session: AsyncSession, email: str, password: str, name: str) -> User:
    user = User(email=email, password_hash=security.hash_password(password), name=name)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Also covers two registrations racing for the same email.
        await session.rollback()
        raise AppError("EMAIL_TAKEN", "Email is already registered", status_code=409) from exc
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    """Return the user if the password matches. Never says which part was wrong."""
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        security.burn_password_check(password)
        return None
    if not security.verify_password(user.password_hash, password):
        return None
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
        await session.commit()
    return user


async def issue_tokens(
    session: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
) -> TokenPair:
    """Mint an access token and a refresh token; a new family unless one is given. Commits."""
    refresh = security.new_refresh_token()
    session.add(
        RefreshToken(
            id=refresh.id,
            user_id=user_id,
            family_id=family_id or uuid.uuid4(),
            token_hash=refresh.token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    await session.commit()
    return TokenPair(
        access_token=security.create_access_token(
            user_id, settings.jwt_secret.get_secret_value(), settings.access_token_ttl_seconds
        ),
        refresh_token=refresh.token,
        expires_in=settings.access_token_ttl_seconds,
    )


async def rotate_refresh_token(session: AsyncSession, settings: Settings, token: str) -> TokenPair:
    """Spend a refresh token for a new pair. Reusing a spent token revokes its whole family."""
    parsed = security.parse_refresh_token(token)
    if parsed is None:
        raise _invalid_refresh()
    token_id, secret = parsed

    # The row lock makes two refreshes with the same token run one after the other, so the
    # second one sees `used_at` and is treated as reuse.
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.id == token_id).with_for_update()
    )
    if row is None or not security.refresh_secret_matches(secret, row.token_hash):
        raise _invalid_refresh()

    now = datetime.now(UTC)
    if row.used_at is not None or row.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise AuthError("REFRESH_TOKEN_REUSED", "Refresh token was already used; log in again")
    if row.expires_at <= now:
        raise _invalid_refresh()

    row.used_at = now
    return await issue_tokens(session, settings, row.user_id, family_id=row.family_id)
