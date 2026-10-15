from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field, SecretStr


def _normalize_email(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


def _strip(value: str) -> str:
    return value.strip()


# Lower-cased so `A@x.com` and `a@x.com` are the same account.
NormalizedEmail = Annotated[EmailStr, BeforeValidator(_normalize_email)]


class RegisterIn(BaseModel):
    email: NormalizedEmail
    password: SecretStr = Field(min_length=8, max_length=128)
    name: Annotated[str, AfterValidator(_strip), Field(min_length=1, max_length=100)]


class LoginIn(BaseModel):
    # No format or length rules beyond a sanity cap: a bad email or password gets the same
    # 401 as a wrong one, never a 422 that hints at what was wrong.
    email: Annotated[str, BeforeValidator(_normalize_email), Field(max_length=320)]
    password: SecretStr = Field(max_length=1024)


class RefreshIn(BaseModel):
    refresh_token: SecretStr = Field(max_length=512)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
