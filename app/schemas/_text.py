import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field


def _nfc(value: str) -> str:
    # iOS keyboards can send decomposed accents; store and search one form only.
    return unicodedata.normalize("NFC", value)


def _nfc_strip(value: str) -> str:
    return _nfc(value).strip()


Title = Annotated[str, AfterValidator(_nfc_strip), Field(min_length=1, max_length=200)]
Description = Annotated[str, AfterValidator(_nfc), Field(max_length=5000)]
SearchQuery = Annotated[str, AfterValidator(_nfc_strip), Field(max_length=100)]


def reject_explicit_null(value: object) -> object:
    """PATCH fields may be left out, but sending `null` for a required column is an error."""
    if value is None:
        raise ValueError("may be omitted but not null")
    return value
