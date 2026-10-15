import time
from pathlib import Path

import pytest

from app.transcode import ffmpeg
from app.transcode.ladder import pick_renditions


def _value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_hls_args_encode_every_rendition_with_capped_bitrates() -> None:
    renditions = pick_renditions(1920, 1080)

    args = ffmpeg.hls_args(Path("in.mp4"), Path("out"), renditions, has_audio=True)

    assert _value(args, "-preset") == "veryfast"
    assert _value(args, "-c:v") == "libx264"
    for i, (kbps, maxrate, bufsize) in enumerate(
        [(1400, 1500, 2100), (2800, 3000, 4200), (5000, 5350, 7500)]
    ):
        assert _value(args, f"-b:v:{i}") == f"{kbps}k"
        assert _value(args, f"-maxrate:v:{i}") == f"{maxrate}k"
        assert _value(args, f"-bufsize:v:{i}") == f"{bufsize}k"
    assert "[s2]scale=1920:1080[v2]" in _value(args, "-filter_complex")
    assert _value(args, "-force_key_frames") == "expr:gte(t,n_forced*6)"
    assert (_value(args, "-c:a"), _value(args, "-b:a"), _value(args, "-ac")) == ("aac", "128k", "2")
    assert _value(args, "-hls_segment_type") == "fmp4"
    assert _value(args, "-hls_time") == "6"
    assert _value(args, "-var_stream_map") == "v:0,a:0 v:1,a:1 v:2,a:2"


def test_hls_args_for_a_silent_source_have_no_audio() -> None:
    args = ffmpeg.hls_args(Path("in.mp4"), Path("out"), pick_renditions(640, 360), has_audio=False)

    assert "0:a:0" not in args
    assert "-c:a" not in args
    assert _value(args, "-var_stream_map") == "v:0"


async def test_run_kills_ffmpeg_after_the_timeout(tmp_path: Path) -> None:
    endless = [
        "ffmpeg",
        "-nostdin",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=64x64",
        "-f",
        "null",
        "-",
    ]
    started = time.monotonic()

    with pytest.raises(ffmpeg.TranscodeError, match="timed out"):
        await ffmpeg.run(endless, tmp_path / "log", timeout_seconds=1)

    assert time.monotonic() - started < 10


async def test_run_reports_a_non_zero_exit(tmp_path: Path) -> None:
    with pytest.raises(ffmpeg.TranscodeError, match="exited with code"):
        await ffmpeg.run(["ffmpeg", "-i", str(tmp_path / "missing.mp4")], tmp_path / "log", 30)

    assert "missing.mp4" in ffmpeg.tail(tmp_path / "log")
