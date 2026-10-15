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
    "MEDIA_SIGNING_SECRET": "media-secret-value-that-is-at-least-32-chars",
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


def test_short_media_signing_secret_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("MEDIA_SIGNING_SECRET", "too-short")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "media_signing_secret" in str(exc_info.value)


def test_media_signing_secret_must_differ_from_the_jwt_secret(env: pytest.MonkeyPatch) -> None:
    env.setenv("MEDIA_SIGNING_SECRET", _REQUIRED_ENV["JWT_SECRET"])

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "MEDIA_SIGNING_SECRET must differ from JWT_SECRET" in str(exc_info.value)


def test_cors_origins_are_split_and_trimmed(env: pytest.MonkeyPatch) -> None:
    env.setenv("CORS_ORIGINS", " http://localhost:3000/ , https://dev.example.com,")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origin_list == ["http://localhost:3000", "https://dev.example.com"]
