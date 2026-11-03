from __future__ import annotations

import os
import subprocess

import pytest

from video_toolkit import ffmpeg as ff

HAS_FFMPEG = ff.find_ffmpeg() is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr("video_toolkit.settings.config_path", lambda: str(tmp_path / "cfg" / "settings.json"))


def make_clip(path: str, seconds: float = 2.0, size: str = "320x240", fps: int = 25, audio: bool = True) -> str:
    """A synthetic test video (moving test pattern + 440 Hz tone)."""
    cmd = [ff.find_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
           f"testsrc=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    cmd += [path]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture
def clip(tmp_path) -> str:
    return make_clip(str(tmp_path / "clip.mp4"))


@pytest.fixture
def silent_clip(tmp_path) -> str:
    return make_clip(str(tmp_path / "silent.mp4"), audio=False)


def fake_info(path="C:/v/in.mp4", duration=10.0, w=1280, h=720, audio=True) -> ff.MediaInfo:
    return ff.MediaInfo(path=path, duration=duration, width=w, height=h, fps=30.0, video_codec="h264",
                        audio_codec="aac" if audio else "", has_video=True, has_audio=audio,
                        audio_channels=2 if audio else 0, size_bytes=10_000_000)
