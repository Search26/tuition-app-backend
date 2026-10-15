from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    minio_region: str = "us-east-1"
    minio_bucket: str = "tuition-media"
    # Host the uploader reaches. Presigned URLs are signed for this host, not minio_endpoint.
    minio_public_endpoint: str = "localhost:9000"
    minio_public_secure: bool = False

    health_check_timeout_seconds: float = 2.0

    jwt_secret: SecretStr = Field(min_length=32)
    access_token_ttl_seconds: int = Field(default=900, gt=0)
    refresh_token_ttl_days: int = Field(default=30, gt=0)

    login_ip_limit: int = Field(default=20, gt=0)
    login_ip_window_seconds: int = Field(default=60, gt=0)
    login_email_failure_limit: int = Field(default=5, gt=0)
    login_email_window_seconds: int = Field(default=900, gt=0)

    upload_max_bytes: int = Field(default=2 * 1024**3, gt=0)
    upload_url_ttl_seconds: int = Field(default=900, gt=0, le=7 * 24 * 3600)

    worker_max_jobs: int = Field(default=1, ge=1)
    transcode_timeout_seconds: int = Field(default=3600, gt=0)
    transcode_threads: int = Field(default=0, ge=0)  # 0 lets ffmpeg decide
    transcode_tmp_dir: str = "/tmp"
    transcode_retry_delay_seconds: int = Field(default=30, ge=0)

    # Public prefix of nginx's /media/ location; signed media URLs start with it.
    media_base_url: str = "http://localhost:8080/media"
    # Signs /media/ URLs. Must differ from the JWT secret, so a leak of one can't forge the other.
    media_signing_secret: SecretStr = Field(min_length=32)
    media_url_ttl_seconds: int = Field(default=4 * 3600, ge=300, le=7 * 24 * 3600)
    media_thumbnail_ttl_seconds: int = Field(default=24 * 3600, ge=3600, le=7 * 24 * 3600)

    # Comma-separated browser origins allowed to call the API and load /media/ (dev web pages).
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _media_secret_is_its_own(self) -> "Settings":
        if self.media_signing_secret.get_secret_value() == self.jwt_secret.get_secret_value():
            raise ValueError("MEDIA_SIGNING_SECRET must differ from JWT_SECRET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
