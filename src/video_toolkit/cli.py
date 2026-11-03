"""Command line: `video-toolkit --cli <tool> <input> [options]`.

    video-toolkit --cli trim movie.mp4 --start 1:05 --end 1:35
    video-toolkit --cli join a.mp4 b.mp4 --reencode
    video-toolkit --cli speed clip.mp4 --factor 2
    video-toolkit --cli gif clip.mp4 --start 3 --end 6 --width 480
    video-toolkit --cli snapshot clip.mp4 --at 12.5
"""

from __future__ import annotations

import argparse
import sys

from . import ffmpeg as ff
from . import ops, runner
from .ffmpeg import parse_time


def _t(text: str) -> float:
    try:
        return parse_time(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="video-toolkit --cli", description="Everyday video jobs on top of FFmpeg.")
    p.add_argument("--out-dir", help="output folder (default: next to the input)")
    p.add_argument("--quality", choices=list(ops.QUALITY_CRF), default="high")
    p.add_argument("--check", action="store_true", help="report whether FFmpeg was found and exit")
    sub = p.add_subparsers(dest="tool")

    def tool(name: str, help_: str, inputs: int = 1):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("input", nargs="+" if inputs > 1 else None, help="input file(s)")
        return sp

    s = tool("trim", "cut a range"); s.add_argument("--start", type=_t, required=True); s.add_argument("--end", type=_t, required=True)
    s.add_argument("--fast", action="store_true", help="stream copy (keyframe accurate only)")
    s = tool("cut", "keep several ranges joined"); s.add_argument("--range", action="append", required=True, metavar="START-END", help="e.g. 0:10-0:20 (repeatable)")
    s = tool("join", "concatenate clips", inputs=2); s.add_argument("--reencode", action="store_true")
    s = tool("speed", "change speed"); s.add_argument("--factor", type=float, required=True); s.add_argument("--pitch", action="store_true", help="let pitch change with speed")
    tool("reverse", "play backwards")
    s = tool("add-audio", "replace or mix in an audio file"); s.add_argument("--audio", required=True); s.add_argument("--mix", action="store_true")
    s.add_argument("--volume", type=float, default=1.0); s.add_argument("--original-volume", type=float, default=1.0); s.add_argument("--fade-out", type=float, default=0.0)
    tool("remove-audio", "drop the audio track")
    s = tool("extract-audio", "save the audio"); s.add_argument("--format", default="mp3", choices=["mp3", "wav", "m4a", "flac", "copy"])
    s = tool("volume", "gain, fades, normalize"); s.add_argument("--gain", type=float, default=1.0); s.add_argument("--fade-in", type=float, default=0.0)
    s.add_argument("--fade-out", type=float, default=0.0); s.add_argument("--normalize", action="store_true")
    s = tool("compress", "shrink the file"); s.add_argument("--target-mb", type=float); s.add_argument("--max-height", type=int)
    s = tool("convert", "change format / size / fps"); s.add_argument("--format", default="mp4"); s.add_argument("--height", type=int); s.add_argument("--fps", type=float)
    s = tool("rotate", "rotate or flip"); s.add_argument("--how", required=True, choices=list(ops.ROTATIONS))
    s = tool("crop", "crop a box"); s.add_argument("--box", metavar="X,Y,W,H"); s.add_argument("--aspect", help="centered crop, e.g. 9:16")
    s.add_argument("--start", type=_t); s.add_argument("--end", type=_t)
    s = tool("loop", "repeat"); s.add_argument("--times", type=int, default=2); s.add_argument("--boomerang", action="store_true")
    s = tool("snapshot", "save one frame"); s.add_argument("--at", type=_t, required=True); s.add_argument("--format", default="png", choices=["png", "jpg"])
    s = tool("frames", "export frames"); s.add_argument("--every", type=float, default=1.0, help="seconds between frames, 0 = all"); s.add_argument("--format", default="jpg", choices=["png", "jpg"]); s.add_argument("--height", type=int)
    s = tool("gif", "make a GIF"); s.add_argument("--start", type=_t); s.add_argument("--end", type=_t); s.add_argument("--fps", type=int, default=15); s.add_argument("--width", type=int, default=480)
    s = tool("overlay", "picture-in-picture / replace a region"); s.add_argument("--file", required=True)
    s.add_argument("--position", choices=list(ops.OVERLAY_POSITIONS)); s.add_argument("--x", type=int); s.add_argument("--y", type=int)
    s.add_argument("--width", type=int); s.add_argument("--height", type=int); s.add_argument("--opacity", type=float, default=1.0)
    s.add_argument("--start", type=_t, default=0.0); s.add_argument("--keep-overlay-audio", action="store_true"); s.add_argument("--margin", type=int, default=24)
    s = tool("sheet", "contact sheet"); s.add_argument("--cols", type=int, default=4); s.add_argument("--rows", type=int, default=4); s.add_argument("--tile-width", type=int, default=320)
    tool("info", "print media info")
    return p


def _jobs(a: argparse.Namespace) -> list[ops.Job]:
    paths = a.input if isinstance(a.input, list) else [a.input]
    infos = []
    for p in paths:
        info = ff.probe(p)
        if info is None:
            raise ops.OpError(f"cannot read {p}")
        infos.append(info)
    info, od, q = infos[0], a.out_dir, a.quality
    t = a.tool
    if t == "info":
        for i in infos:
            print(f"{i.path}: {i.describe()}")
        return []
    if t == "trim":
        return [ops.trim(info, a.start, a.end, not a.fast, q, od)]
    if t == "cut":
        ranges = []
        for r in a.range:
            try:
                s, e = r.split("-", 1)
                ranges.append((parse_time(s), parse_time(e)))
            except ValueError as exc:
                raise ops.OpError(f"bad range '{r}': use START-END") from exc
        return ops.trim_segments(info, ranges, True, True, q, od)
    if t == "join":
        return [ops.join(infos, a.reencode, q, od)]
    if t == "speed":
        return [ops.speed(info, a.factor, not a.pitch, q, od)]
    if t == "reverse":
        return [ops.reverse(info, q, od)]
    if t == "add-audio":
        return [ops.add_audio(info, a.audio, "mix" if a.mix else "replace", a.volume, a.original_volume, True, a.fade_out, od)]
    if t == "remove-audio":
        return [ops.remove_audio(info, od)]
    if t == "extract-audio":
        return [ops.extract_audio(info, a.format, od)]
    if t == "volume":
        return [ops.volume(info, a.gain, a.fade_in, a.fade_out, a.normalize, od)]
    if t == "compress":
        return [ops.compress(info, a.target_mb, q if q != "high" else "balanced", a.max_height, od)]
    if t == "convert":
        return [ops.convert(info, a.format, a.height, a.fps, q, od)]
    if t == "rotate":
        return [ops.rotate(info, a.how, q, od)]
    if t == "crop":
        if a.aspect:
            x, y, w, h = ops.crop_preset(info, a.aspect)
        elif a.box:
            try:
                x, y, w, h = (int(v) for v in a.box.split(","))
            except ValueError as exc:
                raise ops.OpError("--box must be X,Y,W,H") from exc
        else:
            raise ops.OpError("give --box X,Y,W,H or --aspect W:H")
        return [ops.crop(info, x, y, w, h, a.start, a.end, q, od)]
    if t == "loop":
        return [ops.loop(info, a.times, a.boomerang, q, od)]
    if t == "snapshot":
        return [ops.snapshot(info, a.at, a.format, od)]
    if t == "frames":
        return [ops.export_frames(info, a.every, a.format, a.height, od)]
    if t == "gif":
        return [ops.gif(info, a.start, a.end, a.fps, a.width, od)]
    if t == "overlay":
        return [ops.overlay(info, a.file, a.x, a.y, a.width, a.height, a.position, a.margin, a.opacity, a.start, a.keep_overlay_audio, q, od)]
    if t == "sheet":
        return [ops.contact_sheet(info, a.cols, a.rows, a.tile_width, od)]
    raise ops.OpError(f"unknown tool {t}")


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if a.check:
        v = ff.ffmpeg_version()
        print(v or f"FFmpeg not found. Install it from {ff.FFMPEG_DOWNLOAD_URL}")
        return 0 if v else 1
    if not a.tool:
        p.print_help()
        return 2
    try:
        jobs = _jobs(a)
    except ops.OpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    last = [-1]

    def progress(frac: float) -> None:
        pct = int(frac * 100)
        if pct != last[0]:
            last[0] = pct
            print(f"\r  {pct:3d}%", end="", flush=True)

    for job in jobs:
        print(f"== {job.title}")
        try:
            out = runner.run_job(job, progress=progress, log=lambda m: print("   " + m))
        except ff.FFmpegMissing as exc:
            print(f"\nerror: {exc}", file=sys.stderr)
            return 1
        except ff.JobFailed as exc:
            print(f"\nerror: {exc}", file=sys.stderr)
            return 1
        except (ff.JobCancelled, KeyboardInterrupt):
            print("\ncancelled", file=sys.stderr)
            return 130
        print(f"\r  done -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
