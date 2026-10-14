from httpx import AsyncClient, Response

PASSWORD = "correct-horse-battery"


async def register(
    client: AsyncClient, email: str = "an@example.com", password: str = PASSWORD
) -> Response:
    return await client.post(
        "/auth/register", json={"email": email, "password": password, "name": "An Nguyen"}
    )


async def login(
    client: AsyncClient, email: str = "an@example.com", password: str = PASSWORD
) -> Response:
    return await client.post("/auth/login", json={"email": email, "password": password})


async def register_and_login(client: AsyncClient, email: str = "an@example.com") -> dict[str, str]:
    await register(client, email)
    response = await login(client, email)
    assert response.status_code == 200
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
