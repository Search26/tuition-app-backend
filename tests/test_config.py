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


def _prod(env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    env.setenv("APP_ENV", "prod")
    env.setenv("MEDIA_BASE_URL", "https://api.example.com/media")
    env.setenv("CORS_ORIGINS", "")
    return env


def test_prod_with_real_values_starts(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "dev")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.app_env == "dev"

    _prod(env)
    assert Settings(_env_file=None).app_env == "prod"  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JWT_SECRET", "change-me-jwt-secret-at-least-32-characters"),
        ("MEDIA_SIGNING_SECRET", "CHANGE-ME-media-signing-secret-32-chars-min"),
        ("MINIO_SECRET_KEY", "change-me-minio-secret"),
        ("DATABASE_URL", "postgresql+asyncpg://tuition:change-me-postgres@postgres:5432/tuition"),
    ],
)
def test_prod_refuses_placeholders_and_names_only_the_variable(
    env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _prod(env).setenv(name, value)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    message = str(exc_info.value)
    assert f"{name} still has a change-me placeholder" in message
    # No input at all in the error, so not even the other, real secrets leak.
    assert "change-me-" not in message.lower()
    assert "db-password" not in message
    assert "super-secret-value" not in message


def test_dev_accepts_placeholders(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "dev")
    env.setenv("JWT_SECRET", "change-me-jwt-secret-at-least-32-characters")

    assert Settings(_env_file=None).app_env == "dev"  # type: ignore[call-arg]


def test_prod_needs_https_media_and_no_localhost_cors(env: pytest.MonkeyPatch) -> None:
    _prod(env).setenv("MEDIA_BASE_URL", "http://api.example.com/media")
    env.setenv("CORS_ORIGINS", "https://admin.example.com, http://localhost:3000")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "MEDIA_BASE_URL must start with https://" in str(exc_info.value)
    assert "CORS_ORIGINS must not list localhost" in str(exc_info.value)
