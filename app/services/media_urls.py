"""Signed /media/ URLs. nginx asks the API to check every request before MinIO serves it.

    {MEDIA_BASE_URL}/v/{exp}/{sig}/hls/{course}/{lesson}/{run}/master.m3u8
    {MEDIA_BASE_URL}/f/{exp}/{sig}/hls/{course}/{lesson}/{run}/thumbnail.jpg

`v` signs one video's run folder: playlists use relative URIs, so the player asks for every
variant playlist, init file and segment under the same signed prefix and nothing is rewritten.
`f` signs one exact key. The kind, expiry and scope are all inside the HMAC.
"""

import base64
import hashlib
import hmac
import re
import time
from datetime import UTC, datetime

from app.core.config import get_settings

MEDIA_PATH_PREFIX = "/media/"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# A path segment never starts with a dot, so `.` and `..` can't appear.
_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_RUN_DIR = rf"hls/{_UUID}/{_UUID}/{_UUID}/"
_VIDEO_KEY = re.compile(rf"(?P<scope>{_RUN_DIR})(?P<rest>{_SEGMENT}(?:/{_SEGMENT})*)")
_FILE_KEY = re.compile(rf"{_RUN_DIR}{_SEGMENT}")
_REQUEST = re.compile(
    rf"{re.escape(MEDIA_PATH_PREFIX)}(?P<kind>[vf])/(?P<exp>[0-9]{{1,12}})/"
    r"(?P<sig>[A-Za-z0-9_-]{43})/(?P<key>.+)"
)


class InvalidMediaPath(Exception):
    """Missing, forged, expired or out-of-scope media path. Always a 403, never explained."""


def _sign(kind: str, exp: int, scope: str) -> str:
    secret = get_settings().media_signing_secret.get_secret_value().encode()
    digest = hmac.new(secret, f"{kind}\n{exp}\n{scope}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _url(kind: str, exp: int, scope: str, key: str) -> str:
    base = get_settings().media_base_url.rstrip("/")
    return f"{base}/{kind}/{exp}/{_sign(kind, exp, scope)}/{key}"


def video_url(hls_path: str, now: float | None = None) -> tuple[str, datetime]:
    """Signed URL for a lesson's master playlist, valid for MEDIA_URL_TTL_SECONDS."""
    match = _VIDEO_KEY.fullmatch(hls_path)
    if match is None:
        raise ValueError(f"Not a transcode output key: {hls_path}")
    exp = int(now if now is not None else time.time()) + get_settings().media_url_ttl_seconds
    return _url("v", exp, match["scope"], hls_path), datetime.fromtimestamp(exp, UTC)


def file_url(key: str, now: float | None = None) -> str:
    """Signed URL for one file. Expiry is rounded to the hour so lists return a stable URL."""
    if _FILE_KEY.fullmatch(key) is None:
        raise ValueError(f"Not a transcode output key: {key}")
    hour = int(now if now is not None else time.time()) // 3600 * 3600
    exp = hour + 3600 + get_settings().media_thumbnail_ttl_seconds
    return _url("f", exp, key, key)


def verify(request_uri: str, now: float | None = None) -> str:
    """The storage key a /media/ request may read, or InvalidMediaPath."""
    path = request_uri.split("?", 1)[0]
    match = _REQUEST.fullmatch(path)
    if match is None:
        raise InvalidMediaPath
    kind, exp, key = match["kind"], int(match["exp"]), match["key"]
    if kind == "v":
        video = _VIDEO_KEY.fullmatch(key)
        if video is None:
            raise InvalidMediaPath
        scope = video["scope"]
    else:
        if _FILE_KEY.fullmatch(key) is None:
            raise InvalidMediaPath
        scope = key
    # Compare the signature before the clock, so a forged URL can't probe expiry.
    if not hmac.compare_digest(match["sig"], _sign(kind, exp, scope)):
        raise InvalidMediaPath
    if exp < (now if now is not None else time.time()):
        raise InvalidMediaPath
    return key
