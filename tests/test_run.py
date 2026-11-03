"""Real FFmpeg runs on a generated test clip."""

import os

import pytest

from conftest import make_clip, needs_ffmpeg
from video_toolkit import ffmpeg as ff
from video_toolkit import ops, preview, runner

pytestmark = needs_ffmpeg


def test_probe(clip, silent_clip):
    info = ff.probe(clip)
    assert info and info.has_video and info.has_audio
    assert (info.width, info.height) == (320, 240) and info.fps == pytest.approx(25, abs=0.1)
    assert info.duration == pytest.approx(2.0, abs=0.15)
    assert "320x240" in info.describe()
    assert not ff.probe(silent_clip).has_audio
    assert ff.probe(clip + ".missing") is None


def test_probe_fallback_without_ffprobe(clip, monkeypatch):
    monkeypatch.setattr(ff, "find_ffprobe", lambda: None)
    info = ff.probe(clip)
    assert info and info.width == 320 and info.has_audio and info.duration > 1.5


def _run(job):
    prog = []
    out = runner.run_job(job, progress=prog.append)
    assert prog and prog[-1] == pytest.approx(1.0)
    return out


def test_trim_precise_and_fast(clip, tmp_path):
    info = ff.probe(clip)
    out = _run(ops.trim(info, 0.5, 1.5, precise=True, out_dir=str(tmp_path)))
    assert ff.probe(out).duration == pytest.approx(1.0, abs=0.15)
    out = _run(ops.trim(info, 0.5, 1.5, precise=False, out_dir=str(tmp_path)))
    assert 0.5 < ff.probe(out).duration < 2.1


def test_trim_segments_joined(clip, tmp_path):
    info = ff.probe(clip)
    out = _run(ops.trim_segments(info, [(0, 0.5), (1.2, 1.7)], join=True, out_dir=str(tmp_path))[0])
    assert ff.probe(out).duration == pytest.approx(1.0, abs=0.15)


def test_join_copy_and_reencode(clip, tmp_path):
    other = make_clip(str(tmp_path / "other.mp4"), seconds=1.0, size="640x360", audio=False)
    a, b = ff.probe(clip), ff.probe(other)
    out = _run(ops.join([a, ff.probe(clip)], reencode=False, out_dir=str(tmp_path)))
    assert ff.probe(out).duration == pytest.approx(4.0, abs=0.3)
    out = _run(ops.join([a, b], reencode=True, out_dir=str(tmp_path)))
    info = ff.probe(out)
    assert info.duration == pytest.approx(3.0, abs=0.3) and (info.width, info.height) == (320, 240) and info.has_audio


def test_speed_reverse_loop(clip, tmp_path):
    info = ff.probe(clip)
    assert ff.probe(_run(ops.speed(info, 2.0, out_dir=str(tmp_path)))).duration == pytest.approx(1.0, abs=0.15)
    assert ff.probe(_run(ops.speed(info, 0.25, out_dir=str(tmp_path)))).duration == pytest.approx(8.0, abs=0.3)
    rev = ff.probe(_run(ops.reverse(info, out_dir=str(tmp_path))))
    assert rev.duration == pytest.approx(2.0, abs=0.15) and rev.has_audio
    assert ff.probe(_run(ops.loop(info, 2, out_dir=str(tmp_path)))).duration == pytest.approx(4.0, abs=0.3)
    boom = ff.probe(_run(ops.loop(info, 1, boomerang=True, out_dir=str(tmp_path))))
    assert boom.duration == pytest.approx(4.0, abs=0.3) and boom.has_audio


def test_audio_tools(clip, silent_clip, tmp_path):
    info = ff.probe(clip)
    mp3 = _run(ops.extract_audio(info, "mp3", out_dir=str(tmp_path)))
    a = ff.probe(mp3)
    assert a.has_audio and not a.has_video and mp3.endswith(".mp3")
    muted = ff.probe(_run(ops.remove_audio(info, out_dir=str(tmp_path))))
    assert muted.has_video and not muted.has_audio
    with_music = ff.probe(_run(ops.add_audio(ff.probe(silent_clip), mp3, "replace", out_dir=str(tmp_path))))
    assert with_music.has_audio and with_music.duration == pytest.approx(2.0, abs=0.2)
    mixed = ff.probe(_run(ops.add_audio(info, mp3, "mix", fade_out=0.5, out_dir=str(tmp_path))))
    assert mixed.has_audio
    vol = ff.probe(_run(ops.volume(info, 0.5, fade_in=0.3, fade_out=0.3, out_dir=str(tmp_path))))
    assert vol.has_audio and vol.has_video


def test_compress_convert_rotate_crop(clip, tmp_path):
    info = ff.probe(clip)
    small = _run(ops.compress(info, target_mb=0.2, out_dir=str(tmp_path)))
    assert os.path.getsize(small) < 0.4 * 1024 * 1024
    webm = ff.probe(_run(ops.convert(info, "webm", height=120, fps=10, quality="small", out_dir=str(tmp_path))))
    assert webm.height == 120 and webm.video_codec == "vp9" and webm.fps == pytest.approx(10, abs=0.1)
    rot = ff.probe(_run(ops.rotate(info, "90", out_dir=str(tmp_path))))
    assert (rot.width, rot.height) == (240, 320)
    x, y, w, h = ops.crop_preset(info, "1:1")
    sq = ff.probe(_run(ops.crop(info, x, y, w, h, start=0.2, end=1.2, out_dir=str(tmp_path))))
    assert (sq.width, sq.height) == (240, 240) and sq.duration == pytest.approx(1.0, abs=0.15)


def test_frames_gif_sheet_overlay(clip, tmp_path):
    info = ff.probe(clip)
    png = runner.run_job(ops.snapshot(info, 1.0, "png", out_dir=str(tmp_path)))
    assert png.endswith(".png") and os.path.getsize(png) > 500
    folder = runner.run_job(ops.export_frames(info, 0.5, "jpg", height=120, out_dir=str(tmp_path)))
    assert len([n for n in os.listdir(folder) if n.endswith(".jpg")]) >= 3
    g = runner.run_job(ops.gif(info, 0, 1, fps=10, width=160, out_dir=str(tmp_path)))
    assert g.endswith(".gif") and os.path.getsize(g) > 1000
    sheet = runner.run_job(ops.contact_sheet(info, 2, 2, tile_width=100, out_dir=str(tmp_path)))
    assert ff.probe(sheet).width == pytest.approx(2 * 100 + 3 * 4, abs=8)
    ov = ff.probe(runner.run_job(ops.overlay(info, png, position="top-left", width=80, opacity=0.7, out_dir=str(tmp_path))))
    assert ov.has_audio and ov.duration == pytest.approx(2.0, abs=0.2)
    pip = ff.probe(runner.run_job(ops.overlay(info, clip, x=10, y=10, width=100, start=0.5, out_dir=str(tmp_path))))
    assert (pip.width, pip.height) == (320, 240)


def test_preview_frame(clip):
    data = preview.frame_png(clip, 0.5, max_width=160)
    assert data and data[:8] == b"\x89PNG\r\n\x1a\n"
    assert preview.frame_png(clip + ".nope", 0.5) is None


def test_cancel_and_failure(clip, tmp_path):
    info = ff.probe(clip)
    job = ops.reverse(info, out_dir=str(tmp_path))
    calls = [0]

    def cancelled():
        calls[0] += 1
        return calls[0] > 2

    with pytest.raises(ff.JobCancelled):
        runner.run_job(job, cancelled=cancelled)
    assert not os.path.exists(job.output)

    bad = ops.Job("x", "bad", ["-i", str(tmp_path / "missing.mp4"), "-c", "copy", str(tmp_path / "o.mp4")], str(tmp_path / "o.mp4"))
    with pytest.raises(ff.JobFailed, match="could not be opened"):
        runner.run_job(bad)


def test_missing_ffmpeg_message(monkeypatch, tmp_path):
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    with pytest.raises(ff.FFmpegMissing, match="FFmpeg was not found"):
        runner.run_job(ops.Job("x", "x", ["-i", "a"], str(tmp_path / "o.mp4")))
