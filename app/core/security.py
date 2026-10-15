"""Password hashing, access JWTs and opaque refresh tokens. No I/O here."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Fixed on purpose: never read from config or from the token header.
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

_hasher = PasswordHasher()
# Verified against when the email is unknown, so both login failures take the same time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check, for logins with an unknown email."""
    verify_password(_DUMMY_HASH, password)


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


class InvalidAccessToken(Exception):
    pass


class ExpiredAccessToken(InvalidAccessToken):
    pass


def create_access_token(user_id: uuid.UUID, secret: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(claims, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str) -> uuid.UUID:
    """Return the user id in a valid access token, or raise."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredAccessToken from exc
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken from exc

    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessToken
    try:
        return uuid.UUID(claims["sub"])
    except (ValueError, TypeError) as exc:
        raise InvalidAccessToken from exc


@dataclass(frozen=True)
class NewRefreshToken:
    id: uuid.UUID
    token: str  # `<id>.<secret>`, returned to the client once
    token_hash: str  # what the database keeps


def hash_refresh_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_refresh_token() -> NewRefreshToken:
    token_id = uuid.uuid4()
    secret = secrets.token_urlsafe(32)
    return NewRefreshToken(
        id=token_id, token=f"{token_id}.{secret}", token_hash=hash_refresh_secret(secret)
    )


def parse_refresh_token(token: str) -> tuple[uuid.UUID, str] | None:
    """Split `<id>.<secret>`; None if it is not that shape."""
    token_id, sep, secret = token.partition(".")
    if not sep or not secret:
        return None
    try:
        return uuid.UUID(token_id), secret
    except ValueError:
        return None


def refresh_secret_matches(secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_refresh_secret(secret), stored_hash)
