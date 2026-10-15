import pytest

from app.transcode.ladder import pick_renditions
from app.transcode.probe import ProbeError, parse_probe


def _sizes(width: int, height: int) -> list[tuple[str, int, int]]:
    return [(r.name, r.width, r.height) for r in pick_renditions(width, height)]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ((1920, 1080), [("480p", 854, 480), ("720p", 1280, 720), ("1080p", 1920, 1080)]),
        ((3840, 2160), [("480p", 854, 480), ("720p", 1280, 720), ("1080p", 1920, 1080)]),
        ((1280, 720), [("480p", 854, 480), ("720p", 1280, 720)]),
        ((640, 360), [("360p", 640, 360)]),
        ((1080, 1920), [("480p", 480, 854), ("720p", 720, 1280), ("1080p", 1080, 1920)]),
        ((1440, 1080), [("480p", 640, 480), ("720p", 960, 720), ("1080p", 1440, 1080)]),
        ((853, 479), [("478p", 852, 478)]),
    ],
)
def test_ladder_never_upscales_and_keeps_even_sizes(
    source: tuple[int, int], expected: list[tuple[str, int, int]]
) -> None:
    sizes = _sizes(*source)

    assert sizes == expected
    assert all(w % 2 == 0 and h % 2 == 0 for _, w, h in sizes)


def test_ladder_bitrates_match_the_design() -> None:
    rates = [(r.video_kbps, r.maxrate_kbps, r.bufsize_kbps) for r in pick_renditions(1920, 1080)]

    assert rates == [(1400, 1500, 2100), (2800, 3000, 4200), (5000, 5350, 7500)]


def _probe(video: dict, *, audio: bool = True, duration: str = "12.5") -> dict:
    streams = [{"codec_type": "video", "avg_frame_rate": "30000/1001", **video}]
    if audio:
        streams.append({"codec_type": "audio"})
    return {"streams": streams, "format": {"duration": duration}}


def test_probe_reads_size_duration_fps_and_audio() -> None:
    info = parse_probe(_probe({"width": 1920, "height": 1080}, audio=False))

    assert (info.width, info.height, info.duration_seconds) == (1920, 1080, 12.5)
    assert round(info.fps, 2) == 29.97
    assert info.has_audio is False


@pytest.mark.parametrize(
    "rotation",
    [
        {"side_data_list": [{"rotation": -90}]},
        {"tags": {"rotate": "90"}},
    ],
)
def test_probe_swaps_size_for_a_rotated_phone_video(rotation: dict) -> None:
    info = parse_probe(_probe({"width": 1920, "height": 1080, **rotation}))

    assert (info.width, info.height) == (1080, 1920)


@pytest.mark.parametrize(
    "data",
    [
        {"streams": [{"codec_type": "audio"}], "format": {"duration": "3"}},
        _probe({"width": 640, "height": 360}, duration="0"),
        _probe({"width": 640, "height": 360}, duration="N/A"),
        {},
    ],
)
def test_probe_rejects_files_without_a_usable_video(data: dict) -> None:
    with pytest.raises(ProbeError):
        parse_probe(data)
