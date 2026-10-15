from dataclasses import dataclass


@dataclass(frozen=True)
class Rendition:
    name: str
    width: int
    height: int
    video_kbps: int
    maxrate_kbps: int
    bufsize_kbps: int


# (short side, bitrate, maxrate, bufsize) in kbit/s.
LADDER: list[tuple[int, int, int, int]] = [
    (480, 1400, 1500, 2100),
    (720, 2800, 3000, 4200),
    (1080, 5000, 5350, 7500),
]


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def pick_renditions(width: int, height: int) -> list[Rendition]:
    """Renditions no larger than the source, matched on the short side so portrait works too."""
    short = min(width, height)
    rungs = [rung for rung in LADDER if rung[0] <= short]
    if not rungs:
        # Smaller than 480: one rendition at the source's own size, with the lowest bitrates.
        rungs = [(short - short % 2, *LADDER[0][1:])]
    renditions = []
    for target, kbps, maxrate, bufsize in rungs:
        if width >= height:
            out_w, out_h = _even(width * target / height), _even(target)
        else:
            out_w, out_h = _even(target), _even(height * target / width)
        renditions.append(Rendition(f"{target}p", out_w, out_h, kbps, maxrate, bufsize))
    return renditions
