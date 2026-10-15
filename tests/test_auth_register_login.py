import asyncio

import pytest
from argon2 import PasswordHasher
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import password_needs_rehash, verify_password
from app.models import User
from tests.auth_helpers import PASSWORD, login, register

pytestmark = pytest.mark.usefixtures("db")


async def test_register_returns_user_without_password_hash(client: AsyncClient) -> None:
    response = await register(client, " An@Example.COM ")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "an@example.com"
    assert body["name"] == "An Nguyen"
    assert body["is_admin"] is False
    assert set(body) == {"id", "email", "name", "is_admin", "created_at"}
    assert PASSWORD not in response.text


async def test_register_stores_an_argon2_hash(client: AsyncClient, db: AsyncSession) -> None:
    await register(client)

    user = await db.scalar(select(User))

    assert user is not None
    assert user.password_hash.startswith("$argon2id$")


async def test_register_duplicate_email_any_case_is_409(client: AsyncClient) -> None:
    await register(client, "an@example.com")

    response = await register(client, "AN@example.com")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


async def test_concurrent_registers_with_same_email_one_wins(client: AsyncClient) -> None:
    responses = await asyncio.gather(*(register(client) for _ in range(3)))

    assert sorted(r.status_code for r in responses) == [201, 409, 409]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"email": "an@example.com", "password": "short", "name": "An"}, "body.password"),
        ({"email": "not-an-email", "password": PASSWORD, "name": "An"}, "body.email"),
        ({"email": "an@example.com", "password": PASSWORD, "name": "   "}, "body.name"),
    ],
)
async def test_register_invalid_input_is_422(
    client: AsyncClient, payload: dict[str, str], field: str
) -> None:
    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == [field]
    assert "short" not in response.text  # the submitted password is never echoed


async def test_login_returns_token_pair(client: AsyncClient) -> None:
    await register(client)

    response = await login(client, "AN@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"] and body["refresh_token"]


async def test_wrong_password_and_unknown_email_look_identical(client: AsyncClient) -> None:
    await register(client)

    wrong_password = await login(client, password="wrong-password")
    unknown_email = await login(client, email="nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong_password.headers["www-authenticate"] == "Bearer"


async def test_login_upgrades_an_outdated_hash(client: AsyncClient, db: AsyncSession) -> None:
    old_hash = PasswordHasher(time_cost=1, memory_cost=8192).hash(PASSWORD)
    db.add(User(email="old@example.com", password_hash=old_hash, name="Old"))
    await db.commit()

    response = await login(client, "old@example.com")

    assert response.status_code == 200
    user = await db.scalar(select(User).where(User.email == "old@example.com"))
    await db.refresh(user)
    assert user is not None
    assert user.password_hash != old_hash
    assert verify_password(user.password_hash, PASSWORD)
    assert not password_needs_rehash(user.password_hash)
