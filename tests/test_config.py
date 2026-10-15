import pytest
from pydantic import ValidationError

from app.core.config import Settings

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:db-password@db:5432/x",
    "REDIS_URL": "redis://redis:6379/0",
    "MINIO_ENDPOINT": "minio:9000",
    "MINIO_ACCESS_KEY": "access",
    "MINIO_SECRET_KEY": "super-secret-value",
    "JWT_SECRET": "jwt-secret-value-that-is-at-least-32-chars",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_missing_required_variable_fails_and_names_it(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "database_url" in str(exc_info.value)


def test_secrets_are_not_shown_in_repr(env: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "super-secret-value" not in repr(settings)
    assert "db-password" not in repr(settings)
    assert "jwt-secret-value" not in repr(settings)


def test_short_jwt_secret_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("JWT_SECRET", "too-short")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "jwt_secret" in str(exc_info.value)
