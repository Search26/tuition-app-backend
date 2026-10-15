import asyncio
from pathlib import Path

from app.transcode.ladder import Rendition

SEGMENT_SECONDS = 6
MAX_FPS = 60
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]


class TranscodeError(Exception):
    """ffmpeg failed or timed out. Worth one retry."""


def hls_args(
    source: Path,
    out_dir: Path,
    renditions: list[Rendition],
    *,
    has_audio: bool,
    threads: int = 0,
) -> list[str]:
    """One ffmpeg run: decode once, split, encode every rendition, write fMP4 HLS + master."""
    count = len(renditions)
    split = f"[0:v]split={count}" + "".join(f"[s{i}]" for i in range(count))
    scales = [f"[s{i}]scale={r.width}:{r.height}[v{i}]" for i, r in enumerate(renditions)]
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source)]
    args += ["-filter_complex", ";".join([split, *scales])]
    for i, r in enumerate(renditions):
        args += ["-map", f"[v{i}]"]
        args += [
            f"-b:v:{i}", f"{r.video_kbps}k",
            f"-maxrate:v:{i}", f"{r.maxrate_kbps}k",
            f"-bufsize:v:{i}", f"{r.bufsize_kbps}k",
        ]  # fmt: skip
        if has_audio:
            args += ["-map", "0:a:0"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
        # A keyframe every 6 s by time (works for variable frame rate), so segments align.
        "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})", "-sc_threshold", "0",
        "-fpsmax", str(MAX_FPS),
        "-threads", str(threads),
    ]  # fmt: skip
    if has_audio:
        args += AUDIO_ARGS
    stream_map = " ".join(f"v:{i},a:{i}" if has_audio else f"v:{i}" for i in range(count))
    args += [
        "-f", "hls",
        "-hls_time", str(SEGMENT_SECONDS),
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4",
        "-hls_flags", "independent_segments",
        "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(out_dir / "v%v" / "seg_%05d.m4s"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", stream_map,
        str(out_dir / "v%v" / "index.m3u8"),
    ]  # fmt: skip
    return args


def thumbnail_args(source: Path, out_path: Path, duration_seconds: float) -> list[str]:
    at = min(duration_seconds * 0.1, 5.0)
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-ss", f"{at:.3f}", "-i", str(source),
        "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(out_path),
    ]  # fmt: skip


def tail(log_path: Path, lines: int = 20, limit: int = 2000) -> str:
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])[-limit:]


async def run(args: list[str], log_path: Path, timeout_seconds: float) -> None:
    """Run ffmpeg with stderr appended to `log_path`; kill it when the timeout passes."""
    with log_path.open("ab") as log:
        log.write(("$ " + " ".join(args) + "\n").encode())
        log.flush()
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=log
        )
        try:
            returncode = await asyncio.wait_for(process.wait(), timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TranscodeError(f"ffmpeg timed out after {timeout_seconds:g} s") from None
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
    if returncode != 0:
        raise TranscodeError(f"ffmpeg exited with code {returncode}")
