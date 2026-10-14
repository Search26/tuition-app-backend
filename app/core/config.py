from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings read from the environment and `.env`. Secrets have no defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"

    database_url: SecretStr
    redis_url: SecretStr

    minio_endpoint: str
    minio_access_key: SecretStr
    minio_secret_key: SecretStr
    minio_secure: bool = False

    health_check_timeout_seconds: float = 2.0

    jwt_secret: SecretStr = Field(min_length=32)
    access_token_ttl_seconds: int = Field(default=900, gt=0)
    refresh_token_ttl_days: int = Field(default=30, gt=0)

    login_ip_limit: int = Field(default=20, gt=0)
    login_ip_window_seconds: int = Field(default=60, gt=0)
    login_email_failure_limit: int = Field(default=5, gt=0)
    login_email_window_seconds: int = Field(default=900, gt=0)

    # Prefix for lesson `hls_url`. Sprint 5 replaces it with signed stream URLs.
    media_base_url: str = "http://localhost:8080/media"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
