import os

import pytest

from conftest import needs_ffmpeg
from video_toolkit.cli import build_parser, main


def test_parser_covers_every_tool():
    p = build_parser()
    sub = next(a for a in p._actions if hasattr(a, "choices") and a.choices and "trim" in a.choices)
    for name in ("trim", "cut", "join", "speed", "reverse", "add-audio", "remove-audio", "extract-audio", "volume",
                 "compress", "convert", "rotate", "crop", "loop", "snapshot", "frames", "gif", "overlay", "sheet", "info"):
        assert name in sub.choices


def test_cli_without_tool_prints_help(capsys):
    assert main([]) == 2


def test_cli_bad_time(capsys):
    with pytest.raises(SystemExit):
        main(["trim", "x.mp4", "--start", "1:2:3:4", "--end", "5"])


@needs_ffmpeg
def test_cli_trim_and_info(clip, tmp_path, capsys):
    assert main(["info", clip]) == 0
    assert "320x240" in capsys.readouterr().out
    rc = main(["--out-dir", str(tmp_path / "o"), "trim", clip, "--start", "0.5", "--end", "1.5"])
    assert rc == 0
    assert [n for n in os.listdir(tmp_path / "o") if n.endswith(".mp4")]
    assert main(["trim", clip, "--start", "2", "--end", "1"]) == 2       # OpError -> 2
    assert "end time" in capsys.readouterr().err
    assert main(["trim", str(tmp_path / "missing.mp4"), "--start", "0", "--end", "1"]) == 2


@needs_ffmpeg
def test_cli_cut_ranges(clip, tmp_path):
    rc = main(["--out-dir", str(tmp_path), "cut", clip, "--range", "0-0.5", "--range", "1-1.5"])
    assert rc == 0
    assert main(["cut", clip, "--range", "bad"]) == 2
