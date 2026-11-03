"""Argument-building tests: no FFmpeg needed."""

import os

import pytest

from conftest import fake_info
from video_toolkit import ops
from video_toolkit.ffmpeg import fmt_time, parse_time


def test_output_path_avoids_collisions(tmp_path):
    src = str(tmp_path / "movie.mp4")
    open(src, "w").close()
    first = ops.output_path(src, "trim")
    assert first.endswith("movie_trim.mp4")
    open(first, "w").close()
    assert ops.output_path(src, "trim").endswith("movie_trim_2.mp4")
    assert ops.output_path(src, "x", str(tmp_path / "out"), "gif").endswith(os.path.join("out", "movie_x.gif"))


def test_trim_precise_and_fast():
    info = fake_info()
    j = ops.trim(info, 1.0, 4.0, precise=True)
    assert j.args[:4] == ["-ss", "1.000", "-i", info.path] and "-t" in j.args and "libx264" in j.args
    assert j.expected_seconds == 3.0
    j = ops.trim(info, 1.0, 4.0, precise=False)
    assert "copy" in j.args and "libx264" not in j.args


@pytest.mark.parametrize("start,end", [(5, 2), (-1, 2), (12, 14), (1, 1.01)])
def test_trim_rejects_bad_ranges(start, end):
    with pytest.raises(ops.OpError):
        ops.trim(fake_info(), start, end)


def test_trim_clamps_end_to_duration():
    j = ops.trim(fake_info(duration=10), 8, 99)
    assert j.expected_seconds == pytest.approx(2.0)


def test_trim_segments_joined_builds_concat_graph():
    info = fake_info()
    jobs = ops.trim_segments(info, [(0, 2), (5, 7)], join=True)
    assert len(jobs) == 1
    fc = jobs[0].args[jobs[0].args.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in fc and "atrim=start=5.000" in fc
    assert jobs[0].expected_seconds == 4
    separate = ops.trim_segments(info, [(0, 2), (5, 7)], join=False)
    assert len(separate) == 2
    with pytest.raises(ops.OpError):
        ops.trim_segments(info, [], join=True)


def test_join_copy_requires_matching_clips(tmp_path):
    a, b = fake_info("a.mp4"), fake_info("b.mp4", w=640)
    with pytest.raises(ops.OpError, match="Re-encode"):
        ops.join([a, b], reencode=False)
    j = ops.join([a, fake_info("c.mp4")], reencode=False, out_dir=str(tmp_path))
    assert "concat" in j.args and j.temp_files and os.path.isfile(j.temp_files[0])
    text = open(j.temp_files[0], encoding="utf-8").read()
    assert text.count("file '") == 2
    os.remove(j.temp_files[0])


def test_join_reencode_handles_mixed_audio():
    j = ops.join([fake_info("a.mp4"), fake_info("b.mp4", audio=False)], reencode=True)
    fc = j.args[j.args.index("-filter_complex") + 1]
    assert "anullsrc" in fc and "concat=n=2:v=1:a=1" in fc and "scale=1280:720" in fc
    with pytest.raises(ops.OpError):
        ops.join([fake_info()], reencode=True)


def test_concat_escape():
    assert ops._concat_escape(r"C:\a's\b.mp4") == r"C:/a'\''s/b.mp4"


def test_speed_and_atempo_chain():
    assert ops._atempo_chain(2.0) == "atempo=2.0000"
    assert ops._atempo_chain(0.25) == "atempo=0.5,atempo=0.5000"
    j = ops.speed(fake_info(), 2.0)
    assert "setpts=PTS/2.000000" in j.args[j.args.index("-filter_complex") + 1]
    assert j.expected_seconds == 5.0
    j = ops.speed(fake_info(audio=False), 0.5)
    assert "-vf" in j.args and "-an" in j.args
    for bad in (0, 1.0, 99):
        with pytest.raises(ops.OpError):
            ops.speed(fake_info(), bad)


def test_reverse():
    j = ops.reverse(fake_info())
    assert "reverse" in j.args and "areverse" in j.args
    with pytest.raises(ops.OpError):
        ops.reverse(fake_info(duration=5000))


def test_audio_tools(tmp_path):
    info = fake_info()
    music = tmp_path / "m.mp3"
    music.write_bytes(b"x")
    j = ops.add_audio(info, str(music), "replace", volume=0.5, fade_out=2)
    fc = j.args[j.args.index("-filter_complex") + 1]
    assert "volume=0.500" in fc and "afade=t=out:st=8.000" in fc and "-stream_loop" in j.args
    j = ops.add_audio(info, str(music), "mix", original_volume=0.3)
    assert "amix=inputs=2" in j.args[j.args.index("-filter_complex") + 1]
    j = ops.add_audio(fake_info(audio=False), str(music), "mix")
    assert "amix" not in j.args[j.args.index("-filter_complex") + 1]     # falls back to replace
    with pytest.raises(ops.OpError):
        ops.add_audio(info, str(tmp_path / "nope.mp3"))
    assert "-an" in ops.remove_audio(info).args
    assert ops.extract_audio(info, "mp3").output.endswith(".mp3")
    assert ops.extract_audio(info, "copy").output.endswith(".m4a")
    with pytest.raises(ops.OpError):
        ops.extract_audio(fake_info(audio=False))
    j = ops.volume(info, 2.0, fade_in=1, fade_out=1, normalize=True)
    af = j.args[j.args.index("-af") + 1]
    assert af.startswith("loudnorm") and "volume=2.000" in af and "afade=t=in" in af
    with pytest.raises(ops.OpError):
        ops.volume(info)


def test_compress_target_size_math():
    info = fake_info(duration=100)
    j = ops.compress(info, target_mb=10)
    kbps = int(j.args[j.args.index("-b:v") + 1].rstrip("k"))
    assert kbps == int(10 * 8192 / 100 - 128)
    with pytest.raises(ops.OpError):
        ops.compress(info, target_mb=0.1)
    j = ops.compress(info, quality="small", max_height=480)
    assert "-crf" in j.args and "scale=-2:'min(480,ih)'" in j.args[j.args.index("-vf") + 1]


def test_convert_rotate_crop():
    info = fake_info()
    j = ops.convert(info, "webm", height=720, fps=24)
    assert "libvpx-vp9" in j.args and "scale=-2:720,fps=24" in j.args[j.args.index("-vf") + 1]
    assert j.output.endswith(".webm")
    with pytest.raises(ops.OpError):
        ops.convert(info, "exe")
    assert "transpose=1" in ops.rotate(info, "90").args
    with pytest.raises(ops.OpError):
        ops.rotate(info, "45")
    j = ops.crop(info, 10, 10, 301, 201, start=1, end=3)
    assert "crop=300:200:10:10" in j.args and j.expected_seconds == 2
    with pytest.raises(ops.OpError):
        ops.crop(info, 1000, 0, 400, 400)
    assert ops.crop_preset(info, "9:16") == ((1280 - 404) // 2, 0, 404, 720)
    assert ops.crop_preset(info, "1:1") == (280, 0, 720, 720)


def test_loop_and_boomerang():
    info = fake_info()
    j = ops.loop(info, 3)
    assert j.args[:2] == ["-stream_loop", "2"] and j.expected_seconds == 30
    j = ops.loop(info, 2, boomerang=True)
    fc = j.args[j.args.index("-filter_complex") + 1]
    assert "reverse" in fc and "areverse" in fc and "loop=loop=1" in fc
    assert j.expected_seconds == 40
    with pytest.raises(ops.OpError):
        ops.loop(info, 1)


def test_frames_gif_sheet(tmp_path):
    info = fake_info(duration=10)
    j = ops.snapshot(info, 2.5, "jpg")
    assert j.args[:2] == ["-ss", "2.500"] and j.output.endswith(".jpg")
    with pytest.raises(ops.OpError):
        ops.snapshot(info, 99)
    j = ops.export_frames(info, 0.5, "png", out_dir=str(tmp_path))
    assert "fps=1/0.5" in j.args[j.args.index("-vf") + 1] and os.path.isdir(j.output)
    j = ops.gif(info, 1, 3, fps=12, width=321)
    assert "palettegen" in j.args[j.args.index("-filter_complex") + 1] and "scale=320" in j.args[j.args.index("-filter_complex") + 1]
    with pytest.raises(ops.OpError):
        ops.gif(fake_info(duration=100))
    j = ops.contact_sheet(info, 3, 2)
    assert "tile=3x2" in j.args[j.args.index("-vf") + 1]


def test_overlay(tmp_path):
    info = fake_info()
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    j = ops.overlay(info, str(logo), position="bottom-right", margin=10, width=200, opacity=0.5)
    fc = j.args[j.args.index("-filter_complex") + 1]
    assert "overlay=W-w-10:H-h-10" in fc and "scale=200:-2" in fc and "colorchannelmixer=aa=0.500" in fc
    assert "-loop" in j.args and ["-map", "0:a?"] == j.args[j.args.index("-map") + 2: j.args.index("-map") + 4]
    clip = tmp_path / "edit.mp4"
    clip.write_bytes(b"x")
    j = ops.overlay(info, str(clip), x=100, y=50, width=640, height=360, start=2.0, keep_overlay_audio=True)
    fc = j.args[j.args.index("-filter_complex") + 1]
    assert "overlay=100:50" in fc and "enable='gte(t,2.000)'" in fc and "1:a?" in j.args
    with pytest.raises(ops.OpError):
        ops.overlay(info, str(logo))                    # no position, no x/y


def test_time_helpers():
    assert fmt_time(75) == "1:15" and fmt_time(3661.5, ms=True) == "1:01:01.500"
    assert parse_time("90") == 90 and parse_time("1:30") == 90 and parse_time("0:01:30.5") == 90.5
    assert parse_time("1h2m3s") == 3723 and parse_time("2m") == 120
    with pytest.raises(ValueError):
        parse_time("1:2:3:4")
