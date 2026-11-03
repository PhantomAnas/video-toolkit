"""Frame extraction for the preview pane (PNG bytes via a pipe, no temp files)."""

from __future__ import annotations

import subprocess

from . import ffmpeg as ff


def frame_png(path: str, at: float, max_width: int = 960, timeout: float = 20.0) -> bytes | None:
    """PNG bytes of the frame at `at` seconds, scaled to at most `max_width`."""
    ffmpeg = ff.find_ffmpeg()
    if not ffmpeg:
        return None
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(0.0, at):.3f}", "-i", path,
           "-frames:v", "1", "-vf", f"scale='min({int(max_width)},iw)':-2", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, **ff._no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 and out.stdout else None
