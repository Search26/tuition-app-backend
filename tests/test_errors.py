from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel

from app.core.errors import AppError


class _Payload(BaseModel):
    email: str
    age: int


def _add_test_routes(app: FastAPI) -> None:
    @app.post("/_test/validate")
    async def validate(payload: _Payload) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/_test/app-error")
    async def app_error() -> None:
        raise AppError("EMAIL_TAKEN", "Email is already registered", status_code=409)

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("secret-db-password-in-message")


async def test_unknown_route_returns_not_found_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Resource not found"}}


async def test_wrong_method_returns_method_not_allowed_envelope(client: AsyncClient) -> None:
    response = await client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_invalid_body_returns_validation_error_with_field_details(
    app: FastAPI, client: AsyncClient
) -> None:
    _add_test_routes(app)

    response = await client.post("/_test/validate", json={"email": "a@b.c", "age": "old"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == ["body.age"]
    assert "integer" in error["details"][0]["message"]


async def test_malformed_json_returns_validation_error(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.post(
        "/_test/validate", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_app_error_uses_its_own_status_and_code(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.get("/_test/app-error")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "EMAIL_TAKEN", "message": "Email is already registered"}
    }


async def test_unhandled_exception_hides_internal_message(
    app: FastAPI, client: AsyncClient
) -> None:
    _add_test_routes(app)

    response = await client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}
    }
    assert "secret" not in response.text
