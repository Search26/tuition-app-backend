import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import security

SECRET = "unit-test-secret-long-enough-for-hs256-and-hs512-0123456789abcdef"


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = security.hash_password("correct horse")

    assert hashed.startswith("$argon2id$")
    assert security.verify_password(hashed, "correct horse")
    assert not security.verify_password(hashed, "wrong horse")


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert not security.verify_password("not-a-hash", "anything")


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()

    token = security.create_access_token(user_id, SECRET, ttl_seconds=60)

    assert security.decode_access_token(token, SECRET) == user_id
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_expired_access_token_raises_expired() -> None:
    token = security.create_access_token(uuid.uuid4(), SECRET, ttl_seconds=-1)

    with pytest.raises(security.ExpiredAccessToken):
        security.decode_access_token(token, SECRET)


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(jwt.encode(_claims(), "another-secret-at-least-32-chars-long"), id="bad-sig"),
        pytest.param(jwt.encode(_claims(), None, algorithm="none"), id="alg-none"),
        pytest.param(jwt.encode(_claims(), SECRET, algorithm="HS512"), id="hs512"),
        pytest.param(jwt.encode(_claims(type="refresh"), SECRET), id="wrong-type"),
        pytest.param(jwt.encode(_claims(sub=None), SECRET), id="no-sub"),
        pytest.param(jwt.encode(_claims(sub="not-a-uuid"), SECRET), id="sub-not-uuid"),
        pytest.param("not.a.jwt", id="garbage"),
    ],
)
def test_bad_access_tokens_are_rejected(token: str) -> None:
    with pytest.raises(security.InvalidAccessToken):
        security.decode_access_token(token, SECRET)


def test_refresh_token_round_trip() -> None:
    new = security.new_refresh_token()

    parsed = security.parse_refresh_token(new.token)

    assert parsed is not None
    token_id, secret = parsed
    assert token_id == new.id
    assert security.refresh_secret_matches(secret, new.token_hash)
    assert not security.refresh_secret_matches(secret + "x", new.token_hash)
    assert secret not in new.token_hash


@pytest.mark.parametrize("token", ["", "no-dot", "not-a-uuid.secret", f"{uuid.uuid4()}."])
def test_malformed_refresh_tokens_do_not_parse(token: str) -> None:
    assert security.parse_refresh_token(token) is None
