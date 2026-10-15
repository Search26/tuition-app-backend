import asyncio
import logging
import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models import RefreshToken, User
from tests.auth_helpers import PASSWORD, bearer, login, register, register_and_login

pytestmark = pytest.mark.usefixtures("db")


def _secret() -> str:
    return get_settings().jwt_secret.get_secret_value()


async def _refresh(client: AsyncClient, token: str):  # noqa: ANN202
    return await client.post("/auth/refresh", json={"refresh_token": token})


async def test_me_returns_the_logged_in_user(client: AsyncClient) -> None:
    tokens = await register_and_login(client)

    response = await client.get("/me", headers=bearer(tokens["access_token"]))

    assert response.status_code == 200
    assert response.json()["email"] == "an@example.com"


async def test_me_without_token_is_not_authenticated(client: AsyncClient) -> None:
    response = await client.get("/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_with_expired_token(client: AsyncClient, db: AsyncSession) -> None:
    await register(client)
    user = await db.scalar(select(User))
    assert user is not None
    expired = create_access_token(user.id, _secret(), ttl_seconds=-1)

    response = await client.get("/me", headers=bearer(expired))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_me_with_tampered_or_wrong_tokens(client: AsyncClient) -> None:
    tokens = await register_and_login(client)
    access = tokens["access_token"]
    tampered = access[:-2] + ("AA" if not access.endswith("AA") else "BB")

    for token in [tampered, tokens["refresh_token"], "garbage"]:
        response = await client.get("/me", headers=bearer(token))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_TOKEN"


async def test_me_for_a_deleted_user_is_invalid(client: AsyncClient) -> None:
    token = create_access_token(uuid.uuid4(), _secret(), ttl_seconds=60)

    response = await client.get("/me", headers=bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


async def test_refresh_rotates_and_marks_the_old_token_used(
    client: AsyncClient, db: AsyncSession
) -> None:
    tokens = await register_and_login(client)

    response = await _refresh(client, tokens["refresh_token"])

    assert response.status_code == 200
    new = response.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert (await client.get("/me", headers=bearer(new["access_token"]))).status_code == 200
    rows = (await db.scalars(select(RefreshToken).order_by(RefreshToken.created_at))).all()
    assert len(rows) == 2
    assert rows[0].used_at is not None
    assert rows[0].family_id == rows[1].family_id
    assert rows[1].used_at is None


async def test_refresh_chain_of_three(client: AsyncClient) -> None:
    token = (await register_and_login(client))["refresh_token"]

    for _ in range(3):
        response = await _refresh(client, token)
        assert response.status_code == 200
        token = response.json()["refresh_token"]


async def test_reusing_a_refresh_token_revokes_the_family(client: AsyncClient) -> None:
    first = (await register_and_login(client))["refresh_token"]
    second = (await _refresh(client, first)).json()["refresh_token"]

    reuse = await _refresh(client, first)

    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"
    # The attacker or the victim may hold `second`; neither can use it now.
    after = await _refresh(client, second)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


async def test_reuse_does_not_touch_other_logins(client: AsyncClient) -> None:
    await register(client)
    phone = (await login(client)).json()["refresh_token"]
    tablet = (await login(client)).json()["refresh_token"]
    await _refresh(client, phone)

    await _refresh(client, phone)  # reuse on the phone's family

    assert (await _refresh(client, tablet)).status_code == 200


async def test_concurrent_refreshes_with_one_token_let_only_one_through(
    client: AsyncClient,
) -> None:
    token = (await register_and_login(client))["refresh_token"]

    responses = await asyncio.gather(*(_refresh(client, token) for _ in range(10)))

    assert sorted(r.status_code for r in responses) == [200] + [401] * 9


async def test_expired_refresh_token_is_invalid(client: AsyncClient, db: AsyncSession) -> None:
    token = (await register_and_login(client))["refresh_token"]
    await db.execute(update(RefreshToken).values(expires_at=RefreshToken.created_at))
    await db.commit()

    response = await _refresh(client, token)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_garbage_refresh_tokens_are_invalid(client: AsyncClient) -> None:
    real = (await register_and_login(client))["refresh_token"]
    token_id, _, secret = real.partition(".")

    for token in ["no-dot", f"{uuid.uuid4()}.{secret}", f"{token_id}.wrong-secret", "x.y"]:
        response = await _refresh(client, token)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    # None of those spent the real token.
    assert (await _refresh(client, real)).status_code == 200


async def test_require_admin(app: FastAPI, client: AsyncClient, db: AsyncSession) -> None:
    @app.get("/_test/admin")
    async def admin_only(user: AdminUser) -> dict[str, str]:
        return {"ok": user.email}

    tokens = await register_and_login(client)
    as_user = await client.get("/_test/admin", headers=bearer(tokens["access_token"]))
    await db.execute(update(User).values(is_admin=True))
    await db.commit()
    as_admin = await client.get("/_test/admin", headers=bearer(tokens["access_token"]))

    assert as_user.status_code == 403
    assert as_user.json()["error"]["code"] == "FORBIDDEN"
    assert as_admin.status_code == 200


async def test_passwords_and_tokens_never_reach_the_logs(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    await register(client)
    await login(client, password="wrong-password")
    tokens = (await login(client)).json()
    refreshed = (await _refresh(client, tokens["refresh_token"])).json()
    await _refresh(client, tokens["refresh_token"])
    await client.get("/me", headers=bearer(refreshed["access_token"]))

    logged = caplog.text
    for value in [PASSWORD, "wrong-password", *tokens.values(), *refreshed.values()]:
        if isinstance(value, str) and len(value) > 10:
            assert value not in logged
