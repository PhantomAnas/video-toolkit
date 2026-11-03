"""Finding FFmpeg, probing media, and running jobs with progress.

FFmpeg is never bundled: it is found on PATH, next to the executable, or via
the VIDEO_TOOLKIT_FFMPEG environment variable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
ENV_VAR = "VIDEO_TOOLKIT_FFMPEG"


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _exe(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" and not name.lower().endswith(".exe") else name


def _candidates(name: str) -> list[str]:
    out: list[str] = []
    env = os.environ.get(ENV_VAR)
    if env:
        out.append(env if name == "ffmpeg" else os.path.join(os.path.dirname(env), _exe(name)))
    base = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0] or ".")
    out.append(os.path.join(os.path.dirname(base), _exe(name)))
    found = shutil.which(name)
    if found:
        out.append(found)
    return out


def find_tool(name: str) -> str | None:
    for cand in _candidates(name):
        for p in (cand, _exe(cand)):
            if os.path.isfile(p):
                return p
    return None


def find_ffmpeg() -> str | None:
    return find_tool("ffmpeg")


def find_ffprobe() -> str | None:
    ffm = find_ffmpeg()
    if ffm:
        sibling = os.path.join(os.path.dirname(ffm), _exe("ffprobe"))
        if os.path.isfile(sibling):
            return sibling
    return find_tool("ffprobe")


def ffmpeg_version() -> str | None:
    path = find_ffmpeg()
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10, **_no_window())
        return out.stdout.splitlines()[0] if out.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


class FFmpegMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(f"FFmpeg was not found. Install it from {FFMPEG_DOWNLOAD_URL} and make sure "
                         "'ffmpeg' works in a terminal, then restart the app.")


def require_ffmpeg() -> str:
    path = find_ffmpeg()
    if not path:
        raise FFmpegMissing()
    return path


# ------------------------------------------------------------------ probing

@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    has_video: bool = False
    has_audio: bool = False
    audio_channels: int = 0
    sample_rate: int = 0
    size_bytes: int = 0
    bitrate: int = 0
    rotation: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def is_image(self) -> bool:
        return self.has_video and self.duration == 0 and self.video_codec in {"png", "mjpeg", "bmp", "webp", "gif", "tiff"}

    def describe(self) -> str:
        bits = []
        if self.has_video:
            bits.append(f"{self.width}x{self.height}")
            if self.fps:
                bits.append(f"{self.fps:g} fps")
            bits.append(self.video_codec)
        if self.has_audio:
            bits.append(f"audio {self.audio_codec} {self.audio_channels}ch")
        else:
            bits.append("no audio")
        if self.duration:
            bits.append(fmt_time(self.duration))
        if self.size_bytes:
            bits.append(fmt_bytes(self.size_bytes))
        return " · ".join(b for b in bits if b)


def _parse_fps(text: str) -> float:
    try:
        return float(Fraction(text)) if "/" in text else float(text)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str) -> MediaInfo | None:
    """Probe with ffprobe, falling back to parsing `ffmpeg -i` output."""
    if not os.path.isfile(path):
        return None
    ffprobe = find_ffprobe()
    info = MediaInfo(path=path, size_bytes=os.path.getsize(path))
    if ffprobe:
        try:
            out = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                                  "-show_streams", path], capture_output=True, text=True, timeout=30, **_no_window())
            if out.returncode == 0:
                data = json.loads(out.stdout)
                return _from_ffprobe(info, data)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return _from_ffmpeg(info)


def _from_ffprobe(info: MediaInfo, data: dict) -> MediaInfo:
    info.raw = data
    fmt = data.get("format", {})
    try:
        info.duration = float(fmt.get("duration", 0) or 0)
    except ValueError:
        info.duration = 0.0
    try:
        info.bitrate = int(fmt.get("bit_rate", 0) or 0)
    except ValueError:
        pass
    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and not info.has_video:
            info.has_video = True
            info.width = int(st.get("width", 0) or 0)
            info.height = int(st.get("height", 0) or 0)
            info.fps = _parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate") or "0")
            if info.fps <= 0 or info.fps > 1000:
                info.fps = _parse_fps(st.get("r_frame_rate") or "0")
            info.video_codec = st.get("codec_name", "")
            for sd in st.get("side_data_list", []) or []:
                if "rotation" in sd:
                    try:
                        info.rotation = int(sd["rotation"]) % 360
                    except (TypeError, ValueError):
                        pass
            tags = st.get("tags", {}) or {}
            if "rotate" in tags:
                try:
                    info.rotation = int(tags["rotate"]) % 360
                except ValueError:
                    pass
            if not info.duration:
                try:
                    info.duration = float(st.get("duration", 0) or 0)
                except ValueError:
                    pass
        elif kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = st.get("codec_name", "")
            info.audio_channels = int(st.get("channels", 0) or 0)
            info.sample_rate = int(st.get("sample_rate", 0) or 0)
    return info


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID_RE = re.compile(r"Video:\s*(\w+).*?,\s*(\d{2,5})x(\d{2,5})(?:.*?,\s*([\d.]+)\s*fps)?")
_AUD_RE = re.compile(r"Audio:\s*(\w+).*?,\s*(\d+)\s*Hz,\s*(stereo|mono|[\d.]+ channels|\d+\.\d)")


def _from_ffmpeg(info: MediaInfo) -> MediaInfo | None:
    ffm = find_ffmpeg()
    if not ffm:
        return None
    try:
        out = subprocess.run([ffm, "-hide_banner", "-i", info.path], capture_output=True, text=True,
                             timeout=30, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stderr or ""
    m = _DUR_RE.search(text)
    if m:
        info.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in text.splitlines():
        v = _VID_RE.search(line)
        if v and not info.has_video:
            info.has_video = True
            info.video_codec = v.group(1)
            info.width, info.height = int(v.group(2)), int(v.group(3))
            info.fps = float(v.group(4)) if v.group(4) else 0.0
        a = _AUD_RE.search(line)
        if a and not info.has_audio:
            info.has_audio = True
            info.audio_codec = a.group(1)
            info.sample_rate = int(a.group(2))
            ch = a.group(3)
            info.audio_channels = 2 if ch == "stereo" else 1 if ch == "mono" else int(float(ch.split()[0]))
    if not (info.has_video or info.has_audio):
        return None
    return info


# ------------------------------------------------------------------ running

@dataclass
class RunResult:
    returncode: int
    stderr_tail: str
    command: list[str]


class JobCancelled(Exception):
    pass


class JobFailed(Exception):
    def __init__(self, message: str, command: list[str] | None = None) -> None:
        super().__init__(message)
        self.command = command or []


def run(args: list[str], expected_seconds: float | None = None,
        progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        log: Callable[[str], None] | None = None) -> RunResult:
    """Run `ffmpeg <args>`; `progress` gets 0..1 when a duration is known.

    Uses `-progress pipe:1` so no stderr parsing is needed for progress.
    """
    ffmpeg = require_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats"] + list(args)
    if log:
        log("ffmpeg " + " ".join(quote(a) for a in args))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_no_window())
    stderr_lines: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    threading.Thread(target=drain, daemon=True).start()
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            if cancelled():
                proc.kill()
                proc.wait(timeout=5)
                raise JobCancelled()
            line = raw.decode("utf-8", "replace").strip()
            if progress and expected_seconds and line.startswith("out_time_us="):
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                    progress(max(0.0, min(1.0, done / expected_seconds)))
                except ValueError:
                    pass
            elif progress and line == "progress=end":
                progress(1.0)
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    tail = " | ".join(stderr_lines[-6:]) if stderr_lines else ""
    return RunResult(rc, tail, cmd)


def quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or ";" in arg or "[" in arg) and not arg.startswith('"') else arg


# ------------------------------------------------------------------ helpers

def fmt_time(seconds: float, ms: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    frac = f".{int(round((seconds - int(seconds)) * 1000)):03d}" if ms else ""
    return f"{h}:{m:02d}:{s:02d}{frac}" if h else f"{m}:{s:02d}{frac}"


def parse_time(text: str) -> float:
    """'90', '1:30', '0:01:30.5', '1h2m3s' -> seconds.  Raises ValueError."""
    t = text.strip().lower()
    if not t:
        raise ValueError("empty time")
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?", t)
    if m and any(m.groups()) and ":" not in t:
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    parts = t.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad time '{text}'")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
