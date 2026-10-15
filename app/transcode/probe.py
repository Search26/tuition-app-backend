import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProbeError(Exception):
    """The file is not a video ffprobe can read. Retrying will not help."""


@dataclass(frozen=True)
class SourceInfo:
    # Displayed size: width and height are swapped for a video stored with a 90° rotation.
    width: int
    height: int
    duration_seconds: float
    fps: float
    has_audio: bool


def _rotation(stream: dict[str, Any]) -> int:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            return int(float(side_data["rotation"]))
    return int(float((stream.get("tags") or {}).get("rotate", 0)))


def _fps(rate: str | None) -> float:
    try:
        num, _, den = (rate or "0/1").partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_probe(data: dict[str, Any]) -> SourceInfo:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or not video.get("width") or not video.get("height"):
        raise ProbeError("no video stream")
    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration"))
    except (TypeError, ValueError):
        raise ProbeError("unknown duration") from None
    if duration <= 0:
        raise ProbeError("zero duration")
    width, height = int(video["width"]), int(video["height"])
    if abs(_rotation(video)) % 180 == 90:
        width, height = height, width
    return SourceInfo(
        width=width,
        height=height,
        duration_seconds=duration,
        fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


async def probe(path: Path, timeout_seconds: float = 60) -> SourceInfo:
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ProbeError("ffprobe timed out") from None
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip().splitlines()
        raise ProbeError(message[-1] if message else f"ffprobe exited {process.returncode}")
    try:
        return parse_probe(json.loads(stdout))
    except json.JSONDecodeError:
        raise ProbeError("unreadable ffprobe output") from None
