import uuid
from urllib.parse import urlsplit

import pytest

from app.core.config import get_settings
from app.services import media_urls
from app.services.media_urls import InvalidMediaPath

NOW = 1_800_000_000


def _run() -> str:
    return f"hls/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/"


def _path(url: str) -> str:
    return urlsplit(url).path


def _signed_video() -> tuple[str, str]:
    run = _run()
    url, _ = media_urls.video_url(run + "master.m3u8", now=NOW)
    return run, _path(url)


def test_video_url_round_trips_and_covers_every_file_in_the_run_folder() -> None:
    run, path = _signed_video()
    prefix = path.removesuffix("master.m3u8")

    assert media_urls.verify(path, now=NOW) == run + "master.m3u8"
    for child in ("v0/index.m3u8", "v2/init_0.mp4", "v1/seg_00042.m4s", "thumbnail.jpg"):
        assert media_urls.verify(prefix + child, now=NOW) == run + child


def test_video_url_expires_after_the_configured_ttl() -> None:
    url, expires_at = media_urls.video_url(_run() + "master.m3u8", now=NOW)
    ttl = get_settings().media_url_ttl_seconds

    assert ttl == 4 * 3600
    assert expires_at.timestamp() == NOW + ttl
    assert f"/media/v/{NOW + ttl}/" in url
    assert media_urls.verify(_path(url), now=NOW + ttl) is not None
    with pytest.raises(InvalidMediaPath):
        media_urls.verify(_path(url), now=NOW + ttl + 1)


def _tamper(path: str, part: int, value: str) -> str:
    parts = path.split("/")
    parts[part] = value
    return "/".join(parts)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: _tamper(p, 2, "f"),  # kind
        lambda p: _tamper(p, 3, str(int(p.split("/")[3]) + 3600)),  # later expiry
        lambda p: _tamper(p, 4, ("A" if p.split("/")[4][0] != "A" else "B") + p.split("/")[4][1:]),
        lambda p: _tamper(p, 7, str(uuid.uuid4())),  # another lesson
        lambda p: _tamper(p, 8, str(uuid.uuid4())),  # another run of the same lesson
        lambda p: p.replace("/master.m3u8", "/../master.m3u8"),
        lambda p: p.replace("/master.m3u8", "/v0/../../x"),
        lambda p: p.replace("/master.m3u8", "/%2e%2e/x"),
        lambda p: p.replace("/master.m3u8", "//master.m3u8"),
        lambda p: p.replace("/master.m3u8", "/.hidden"),
        lambda p: p.replace("/master.m3u8", "/"),
        lambda p: p.replace("/media/", "/other/"),
        lambda p: p.split("/v/")[0] + "/" + p.split("/", 5)[5],  # no token at all
    ],
    ids=[
        "kind",
        "exp",
        "sig",
        "other-lesson",
        "other-run",
        "dotdot",
        "dotdot-deep",
        "encoded-dots",
        "double-slash",
        "dotfile",
        "folder-only",
        "wrong-prefix",
        "no-token",
    ],
)
def test_any_tampered_part_is_rejected(change) -> None:  # type: ignore[no-untyped-def]
    _, path = _signed_video()

    with pytest.raises(InvalidMediaPath):
        media_urls.verify(change(path), now=NOW)


def test_query_string_is_ignored() -> None:
    run, path = _signed_video()

    assert media_urls.verify(path + "?X-Amz-Credential=x&a=b", now=NOW) == run + "master.m3u8"


def test_file_url_opens_only_that_file() -> None:
    run = _run()
    path = _path(media_urls.file_url(run + "thumbnail.jpg", now=NOW))

    assert path.startswith("/media/f/")
    assert media_urls.verify(path, now=NOW) == run + "thumbnail.jpg"
    for other in ("master.m3u8", "v0/index.m3u8"):
        with pytest.raises(InvalidMediaPath):
            media_urls.verify(path.replace("thumbnail.jpg", other), now=NOW)
    # Same scope signed as a video folder must not be accepted either.
    with pytest.raises(InvalidMediaPath):
        media_urls.verify(path.replace("/media/f/", "/media/v/"), now=NOW)


def test_file_url_is_stable_within_the_hour_and_lasts_a_day() -> None:
    key = _run() + "thumbnail.jpg"
    hour = NOW // 3600 * 3600

    first = media_urls.file_url(key, now=hour + 5)
    later = media_urls.file_url(key, now=hour + 3599)
    exp = int(_path(first).split("/")[3])

    assert first == later
    assert exp == hour + 3600 + 24 * 3600
    assert media_urls.verify(_path(first), now=hour + 24 * 3600) == key


def test_a_different_secret_invalidates_every_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _, path = _signed_video()
    monkeypatch.setattr(
        get_settings(), "media_signing_secret", type(get_settings().media_signing_secret)("x" * 40)
    )

    with pytest.raises(InvalidMediaPath):
        media_urls.verify(path, now=NOW)


@pytest.mark.parametrize("key", ["hls/c/l/master.m3u8", "original/a/b/c.mp4", "hls/../x"])
def test_only_transcode_output_keys_can_be_signed(key: str) -> None:
    with pytest.raises(ValueError):
        media_urls.video_url(key, now=NOW)
    with pytest.raises(ValueError):
        media_urls.file_url(key, now=NOW)
