"""Every tool is a pure function that turns parameters into a `Job`: the exact
FFmpeg argument list, the output path, and the duration used for progress.

Nothing here runs anything, so every builder is unit-testable without FFmpeg,
and the GUI can show the command before running it.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field

from .ffmpeg import MediaInfo

VIDEO_CODEC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k"]
QUALITY_CRF = {"best": 17, "high": 20, "balanced": 23, "small": 28}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".ts", ".3gp", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class OpError(ValueError):
    """A parameter problem, phrased for the user."""


@dataclass
class Job:
    tool: str
    title: str
    args: list[str]
    output: str
    expected_seconds: float | None = None
    temp_files: list[str] = field(default_factory=list)   # removed after the run
    pre_jobs: list["Job"] = field(default_factory=list)   # run first (e.g. GIF palette)


# ------------------------------------------------------------------ helpers

def _check_input(info: MediaInfo | None, need_video: bool = True, need_audio: bool = False) -> MediaInfo:
    if info is None:
        raise OpError("pick a source video first")
    if need_video and not info.has_video:
        raise OpError("the source has no video stream")
    if need_audio and not info.has_audio:
        raise OpError("the source has no audio track")
    return info


def even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def output_path(src: str, suffix: str, out_dir: str | None = None, ext: str | None = None) -> str:
    base, src_ext = os.path.splitext(os.path.basename(src))
    folder = out_dir or os.path.dirname(src)
    ext = ext or src_ext or ".mp4"
    if not ext.startswith("."):
        ext = "." + ext
    cand = os.path.join(folder, f"{base}_{suffix}{ext}")
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(folder, f"{base}_{suffix}_{n}{ext}")
        n += 1
    return cand


def _crf(quality: str) -> list[str]:
    return ["-crf", str(QUALITY_CRF.get(quality, 20)), "-preset", "medium"]


def _encode(quality: str = "high", keep_audio: bool = True, info: MediaInfo | None = None) -> list[str]:
    args = VIDEO_CODEC + _crf(quality)
    if keep_audio and (info is None or info.has_audio):
        args += AUDIO_CODEC
    else:
        args += ["-an"]
    return args


def _tempname(ext: str) -> str:
    fd, path = tempfile.mkstemp(prefix="vtk_", suffix=ext)
    os.close(fd)
    return path


def _validate_range(start: float, end: float, duration: float) -> tuple[float, float]:
    if start < 0 or end < 0:
        raise OpError("times cannot be negative")
    if end <= start:
        raise OpError("end time must be after start time")
    if duration and start >= duration:
        raise OpError("start time is past the end of the video")
    if duration and end > duration + 0.01:
        end = duration
    if end - start < 0.05:
        raise OpError("segment is shorter than 50 ms")
    return start, end


# ------------------------------------------------------------------- trim

def trim(info: MediaInfo, start: float, end: float, precise: bool = True, quality: str = "high",
         out_dir: str | None = None) -> Job:
    """Cut [start, end].  Precise re-encodes (frame accurate); fast copies streams
    (instant, but cuts land on keyframes, so it may start a little early)."""
    _check_input(info)
    start, end = _validate_range(start, end, info.duration)
    out = output_path(info.path, f"trim_{start:.1f}-{end:.1f}".replace(".", "_"), out_dir, None if precise else None)
    if precise:
        args = ["-ss", f"{start:.3f}", "-i", info.path, "-t", f"{end - start:.3f}"] + _encode(quality, True, info)
    else:
        args = ["-ss", f"{start:.3f}", "-i", info.path, "-t", f"{end - start:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    return Job("trim", f"Trim {start:.1f}s - {end:.1f}s", args + [out], out, end - start)


def trim_segments(info: MediaInfo, segments: list[tuple[float, float]], join: bool, precise: bool = True,
                  quality: str = "high", out_dir: str | None = None) -> list[Job]:
    """Several [start, end] ranges: separate files, or one file with the kept parts joined."""
    _check_input(info)
    if not segments:
        raise OpError("add at least one segment")
    segs = [_validate_range(s, e, info.duration) for s, e in segments]
    if not join:
        return [trim(info, s, e, precise, quality, out_dir) for s, e in segs]
    # One pass with trim/concat filters: frame accurate and keeps audio in sync.
    parts, labels = [], ""
    for i, (s, e) in enumerate(segs):
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        labels += f"[v{i}]"
        if info.has_audio:
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels += f"[a{i}]"
    n = len(segs)
    if info.has_audio:
        parts.append(f"{labels}concat=n={n}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        parts.append(f"{labels}concat=n={n}:v=1:a=0[v]")
        maps = ["-map", "[v]"]
    out = output_path(info.path, "cut", out_dir)
    total = sum(e - s for s, e in segs)
    args = ["-i", info.path, "-filter_complex", ";".join(parts)] + maps + _encode(quality, True, info) + [out]
    return [Job("trim", f"Keep {n} segments ({total:.1f}s)", args, out, total)]


# ------------------------------------------------------------------- join

def _concat_escape(path: str) -> str:
    return path.replace("\\", "/").replace("'", r"'\''")


def join(infos: list[MediaInfo], reencode: bool, quality: str = "high", out_dir: str | None = None,
         target: tuple[int, int] | None = None) -> Job:
    """Concatenate clips.  Stream copy needs identical codecs/sizes; re-encode
    scales everything to `target` (default: the first clip's size) and works always."""
    if len(infos) < 2:
        raise OpError("add at least two clips to join")
    for i in infos:
        _check_input(i)
    first = infos[0]
    out = output_path(first.path, "joined", out_dir, ".mp4")
    total = sum(i.duration for i in infos)
    if not reencode:
        same = all((i.video_codec, i.width, i.height, i.has_audio) == (first.video_codec, first.width, first.height, first.has_audio) for i in infos)
        if not same:
            raise OpError("clips differ in codec, size or audio; use 'Re-encode' to join them")
        listfile = _tempname(".txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for i in infos:
                fh.write(f"file '{_concat_escape(os.path.abspath(i.path))}'\n")
        args = ["-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", "-movflags", "+faststart", out]
        return Job("join", f"Join {len(infos)} clips (copy)", args, out, total, temp_files=[listfile])
    w, h = target or (first.width, first.height)
    w, h = even(w), even(h)
    any_audio = any(i.has_audio for i in infos)
    parts, labels, inputs = [], "", []
    for n, i in enumerate(infos):
        inputs += ["-i", i.path]
        parts.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                     f"setsar=1,fps=30,format=yuv420p[v{n}]")
        labels += f"[v{n}]"
        if any_audio:
            if i.has_audio:
                parts.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo[a{n}]")
            else:
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={max(0.1, i.duration):.3f}[a{n}]")
            labels += f"[a{n}]"
    parts.append(f"{labels}concat=n={len(infos)}:v=1:a={1 if any_audio else 0}" + ("[v][a]" if any_audio else "[v]"))
    maps = ["-map", "[v]"] + (["-map", "[a]"] if any_audio else [])
    args = inputs + ["-filter_complex", ";".join(parts)] + maps + VIDEO_CODEC + _crf(quality)
    args += AUDIO_CODEC if any_audio else []
    return Job("join", f"Join {len(infos)} clips ({w}x{h})", args + [out], out, total)


# ------------------------------------------------------------------ speed

def _atempo_chain(factor: float) -> str:
    """atempo only accepts 0.5..100 per stage, so chain stages for slow-motion."""
    stages = []
    f = factor
    while f < 0.5:
        stages.append("atempo=0.5")
        f /= 0.5
    while f > 100:
        stages.append("atempo=100")
        f /= 100
    stages.append(f"atempo={f:.4f}")
    return ",".join(stages)


def speed(info: MediaInfo, factor: float, keep_pitch: bool = True, quality: str = "high",
          out_dir: str | None = None) -> Job:
    _check_input(info)
    if not 0.1 <= factor <= 16:
        raise OpError("speed must be between 0.1x and 16x")
    if abs(factor - 1.0) < 1e-6:
        raise OpError("a speed of 1x changes nothing")
    vf = f"setpts=PTS/{factor:.6f}"
    out = output_path(info.path, f"{factor:g}x", out_dir)
    if info.has_audio:
        af = _atempo_chain(factor) if keep_pitch else f"asetrate=48000*{factor:.6f},aresample=48000"
        fc = f"[0:v]{vf}[v];[0:a]{af}[a]"
        args = ["-i", info.path, "-filter_complex", fc, "-map", "[v]", "-map", "[a]"] + _encode(quality, True, info)
    else:
        args = ["-i", info.path, "-vf", vf] + _encode(quality, False, info)
    return Job("speed", f"Speed {factor:g}x", args + [out], out, info.duration / factor)


# ---------------------------------------------------------------- reverse

def reverse(info: MediaInfo, quality: str = "high", out_dir: str | None = None) -> Job:
    _check_input(info)
    if info.duration > 600:
        raise OpError("reverse loads the whole clip into memory; trim it under 10 minutes first")
    out = output_path(info.path, "reversed", out_dir)
    if info.has_audio:
        args = ["-i", info.path, "-vf", "reverse", "-af", "areverse"] + _encode(quality, True, info)
    else:
        args = ["-i", info.path, "-vf", "reverse"] + _encode(quality, False, info)
    return Job("reverse", "Reverse", args + [out], out, info.duration)


# ------------------------------------------------------------------ audio

def add_audio(info: MediaInfo, audio_path: str, mode: str = "replace", volume: float = 1.0,
              original_volume: float = 1.0, loop: bool = True, fade_out: float = 0.0,
              out_dir: str | None = None) -> Job:
    """mode: replace (drop the original track) or mix (both, with volumes)."""
    _check_input(info)
    if not os.path.isfile(audio_path):
        raise OpError("the audio file does not exist")
    if mode not in ("replace", "mix"):
        raise OpError("mode must be replace or mix")
    if mode == "mix" and not info.has_audio:
        mode = "replace"
    out = output_path(info.path, "audio", out_dir)
    inputs = ["-i", info.path] + (["-stream_loop", "-1"] if loop else []) + ["-i", audio_path]
    new = f"[1:a]volume={volume:.3f}"
    if fade_out > 0:
        new += f",afade=t=out:st={max(0.0, info.duration - fade_out):.3f}:d={fade_out:.3f}"
    if mode == "replace":
        fc = f"{new}[a]"
    else:
        fc = f"{new}[n];[0:a]volume={original_volume:.3f}[o];[o][n]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]"
    args = inputs + ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy"] + AUDIO_CODEC + ["-shortest", "-t", f"{info.duration:.3f}", out]
    return Job("add_audio", f"Add audio ({mode})", args, out, info.duration)


def remove_audio(info: MediaInfo, out_dir: str | None = None) -> Job:
    _check_input(info)
    out = output_path(info.path, "muted", out_dir)
    return Job("remove_audio", "Remove audio", ["-i", info.path, "-c:v", "copy", "-an", "-movflags", "+faststart", out], out, info.duration)


def extract_audio(info: MediaInfo, fmt: str = "mp3", out_dir: str | None = None) -> Job:
    _check_input(info, need_video=False, need_audio=True)
    codecs = {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "wav": ["-c:a", "pcm_s16le"],
              "m4a": ["-c:a", "aac", "-b:a", "192k"], "flac": ["-c:a", "flac"], "copy": ["-c:a", "copy"]}
    if fmt not in codecs:
        raise OpError("format must be mp3, wav, m4a, flac or copy")
    ext = {"copy": "." + (info.audio_codec if info.audio_codec in ("mp3", "flac", "opus") else "m4a")}.get(fmt, "." + fmt)
    out = output_path(info.path, "audio", out_dir, ext)
    return Job("extract_audio", f"Extract audio ({fmt})", ["-i", info.path, "-vn"] + codecs[fmt] + [out], out, info.duration)


def volume(info: MediaInfo, gain: float = 1.0, fade_in: float = 0.0, fade_out: float = 0.0,
           normalize: bool = False, out_dir: str | None = None) -> Job:
    _check_input(info, need_video=False, need_audio=True)
    if gain < 0 or gain > 10:
        raise OpError("volume must be between 0 and 10 (1 = unchanged)")
    filters = []
    if normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if abs(gain - 1) > 1e-6:
        filters.append(f"volume={gain:.3f}")
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, info.duration - fade_out):.3f}:d={fade_out:.3f}")
    if not filters:
        raise OpError("nothing to change: set a volume, a fade, or normalize")
    out = output_path(info.path, "volume", out_dir)
    args = ["-i", info.path, "-af", ",".join(filters)]
    args += ["-c:v", "copy"] if info.has_video else ["-vn"]
    return Job("volume", "Volume / fades", args + AUDIO_CODEC + [out], out, info.duration)


# --------------------------------------------------------------- compress

def compress(info: MediaInfo, target_mb: float | None = None, quality: str = "balanced",
             max_height: int | None = None, out_dir: str | None = None) -> Job:
    """Target size uses a bitrate budget (two-pass would be better; one pass is close enough
    and much faster).  Without a target, CRF quality presets."""
    _check_input(info)
    out = output_path(info.path, "compressed", out_dir, ".mp4")
    vf = [f"scale=-2:'min({max_height},ih)'"] if max_height else []
    args = ["-i", info.path]
    if vf:
        args += ["-vf", ",".join(vf)]
    if target_mb:
        if target_mb <= 0 or not info.duration:
            raise OpError("target size must be positive and the source needs a known duration")
        audio_kbps = 128 if info.has_audio else 0
        total_kbps = target_mb * 8192 / info.duration
        video_kbps = int(total_kbps - audio_kbps)
        if video_kbps < 50:
            raise OpError("target size is too small for this length; try a bigger size or a lower height")
        args += VIDEO_CODEC + ["-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.2)}k",
                               "-bufsize", f"{video_kbps * 2}k", "-preset", "medium"]
        args += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"] if info.has_audio else ["-an"]
        title = f"Compress to ~{target_mb:g} MB"
    else:
        args += _encode(quality, True, info)
        title = f"Compress ({quality})"
    return Job("compress", title, args + [out], out, info.duration)


# ---------------------------------------------------------------- convert

def convert(info: MediaInfo, fmt: str = "mp4", height: int | None = None, fps: float | None = None,
            quality: str = "high", out_dir: str | None = None) -> Job:
    """Change container/codec, optionally resize (keeps aspect) and change fps."""
    _check_input(info)
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("mp4", "mkv", "mov", "webm", "avi"):
        raise OpError("format must be mp4, mkv, mov, webm or avi")
    vf = []
    if height:
        if height < 16:
            raise OpError("height is too small")
        vf.append(f"scale=-2:{even(height)}")
    if fps:
        if not 1 <= fps <= 240:
            raise OpError("fps must be between 1 and 240")
        vf.append(f"fps={fps:g}")
    out = output_path(info.path, "converted", out_dir, "." + fmt)
    args = ["-i", info.path] + (["-vf", ",".join(vf)] if vf else [])
    if fmt == "webm":
        args += ["-c:v", "libvpx-vp9", "-crf", str(QUALITY_CRF.get(quality, 30) + 10), "-b:v", "0", "-row-mt", "1"]
        args += ["-c:a", "libopus", "-b:a", "128k"] if info.has_audio else ["-an"]
    else:
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + _crf(quality)
        if fmt == "mp4" or fmt == "mov":
            args += ["-movflags", "+faststart"]
        args += AUDIO_CODEC if info.has_audio else ["-an"]
    bits = [fmt.upper()] + ([f"{height}p"] if height else []) + ([f"{fps:g} fps"] if fps else [])
    return Job("convert", "Convert to " + " ".join(bits), args + [out], out, info.duration)


# ------------------------------------------------------------ rotate/flip

ROTATIONS = {"90": "transpose=1", "180": "transpose=1,transpose=1", "270": "transpose=2",
             "hflip": "hflip", "vflip": "vflip"}


def rotate(info: MediaInfo, how: str, quality: str = "high", out_dir: str | None = None) -> Job:
    _check_input(info)
    if how not in ROTATIONS:
        raise OpError("rotation must be 90, 180, 270, hflip or vflip")
    out = output_path(info.path, how if how.endswith("flip") else f"rot{how}", out_dir)
    args = ["-i", info.path, "-vf", ROTATIONS[how], "-metadata:s:v", "rotate=0"] + _encode(quality, True, info)
    label = {"90": "Rotate 90° clockwise", "180": "Rotate 180°", "270": "Rotate 90° counter-clockwise",
             "hflip": "Flip horizontally", "vflip": "Flip vertically"}[how]
    return Job("rotate", label, args + [out], out, info.duration)


# ------------------------------------------------------------------- crop

def crop(info: MediaInfo, x: int, y: int, w: int, h: int, start: float | None = None, end: float | None = None,
         quality: str = "high", out_dir: str | None = None) -> Job:
    _check_input(info)
    w, h = even(w), even(h)
    x, y = max(0, int(x)), max(0, int(y))
    if x + w > info.width or y + h > info.height:
        raise OpError(f"crop box leaves the frame ({info.width}x{info.height})")
    args = []
    dur = info.duration
    if start is not None and end is not None:
        start, end = _validate_range(start, end, info.duration)
        args += ["-ss", f"{start:.3f}"]
        dur = end - start
    args += ["-i", info.path]
    if start is not None and end is not None:
        args += ["-t", f"{dur:.3f}"]
    out = output_path(info.path, f"crop_{w}x{h}", out_dir)
    args += ["-vf", f"crop={w}:{h}:{x}:{y}"] + _encode(quality, True, info)
    return Job("crop", f"Crop {w}x{h} at {x},{y}", args + [out], out, dur)


def crop_preset(info: MediaInfo, aspect: str) -> tuple[int, int, int, int]:
    """Centered crop box for an aspect like '9:16' or '1:1'."""
    try:
        aw, ah = (int(v) for v in aspect.split(":"))
    except ValueError as exc:
        raise OpError("aspect must look like 9:16") from exc
    w, h = info.width, info.height
    if w * ah > h * aw:          # too wide: cut the sides
        nw, nh = even(h * aw / ah), even(h)
    else:
        nw, nh = even(w), even(w * ah / aw)
    return (w - nw) // 2, (h - nh) // 2, nw, nh


# ------------------------------------------------------------------- loop

def loop(info: MediaInfo, times: int = 2, boomerang: bool = False, quality: str = "high",
         out_dir: str | None = None) -> Job:
    """Repeat the clip `times` times; boomerang plays forward then backward each time."""
    _check_input(info)
    if not 1 <= times <= 50:
        raise OpError("repeat between 1 and 50 times")
    if boomerang and info.duration > 300:
        raise OpError("boomerang reverses in memory; trim the clip under 5 minutes first")
    out = output_path(info.path, "boomerang" if boomerang else f"loop{times}", out_dir)
    if boomerang:
        if info.has_audio:
            fc = (f"[0:v]split[f][r0];[r0]reverse[r];[f][r]concat=n=2:v=1:a=0[one];"
                  f"[0:a]asplit[fa][ra0];[ra0]areverse[ra];[fa][ra]concat=n=2:v=0:a=1[aone];"
                  f"[one]loop=loop={times - 1}:size=32767:start=0[v];[aone]aloop=loop={times - 1}:size=2147483647[a]")
            maps = ["-map", "[v]", "-map", "[a]"]
        else:
            fc = f"[0:v]split[f][r0];[r0]reverse[r];[f][r]concat=n=2:v=1:a=0,loop=loop={times - 1}:size=32767:start=0[v]"
            maps = ["-map", "[v]"]
        args = ["-i", info.path, "-filter_complex", fc] + maps + _encode(quality, True, info) + [out]
        return Job("loop", f"Boomerang x{times}", args, out, info.duration * 2 * times)
    if times == 1:
        raise OpError("repeating once changes nothing")
    args = ["-stream_loop", str(times - 1), "-i", info.path, "-c", "copy", "-movflags", "+faststart", out]
    return Job("loop", f"Loop x{times}", args, out, info.duration * times)


# ----------------------------------------------------------------- frames

def snapshot(info: MediaInfo, at: float, fmt: str = "png", out_dir: str | None = None) -> Job:
    _check_input(info)
    if at < 0 or (info.duration and at > info.duration):
        raise OpError("time is outside the video")
    if fmt not in ("png", "jpg"):
        raise OpError("format must be png or jpg")
    out = output_path(info.path, f"frame_{at:.2f}s".replace(".", "_"), out_dir, "." + fmt)
    args = ["-ss", f"{at:.3f}", "-i", info.path, "-frames:v", "1"] + (["-q:v", "2"] if fmt == "jpg" else []) + [out]
    return Job("snapshot", f"Save frame at {at:.2f}s", args, out, None)


def export_frames(info: MediaInfo, every_seconds: float = 1.0, fmt: str = "jpg", height: int | None = None,
                  out_dir: str | None = None) -> Job:
    """One image every N seconds (0 = every frame) into a folder."""
    _check_input(info)
    if every_seconds < 0:
        raise OpError("interval cannot be negative")
    if fmt not in ("png", "jpg"):
        raise OpError("format must be png or jpg")
    base = os.path.splitext(os.path.basename(info.path))[0]
    folder = os.path.join(out_dir or os.path.dirname(info.path), f"{base}_frames")
    os.makedirs(folder, exist_ok=True)
    vf = []
    if every_seconds > 0:
        vf.append(f"fps=1/{every_seconds:g}")
    if height:
        vf.append(f"scale=-2:{even(height)}")
    pattern = os.path.join(folder, "frame_%05d." + fmt)
    args = ["-i", info.path] + (["-vf", ",".join(vf)] if vf else []) + (["-q:v", "2"] if fmt == "jpg" else []) + [pattern]
    label = "Export every frame" if every_seconds == 0 else f"Export a frame every {every_seconds:g}s"
    return Job("frames", label, args, folder, info.duration)


# -------------------------------------------------------------------- gif

def gif(info: MediaInfo, start: float | None = None, end: float | None = None, fps: int = 15,
        width: int = 480, out_dir: str | None = None) -> Job:
    """High-quality GIF with a generated palette, in one pass."""
    _check_input(info)
    if not 1 <= fps <= 50:
        raise OpError("gif fps must be between 1 and 50")
    if width < 16:
        raise OpError("width is too small")
    args, dur = [], info.duration
    if start is not None and end is not None:
        start, end = _validate_range(start, end, info.duration)
        args += ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}"]
        dur = end - start
    if dur > 60:
        raise OpError("GIFs over a minute get huge; trim the range first")
    out = output_path(info.path, "gif", out_dir, ".gif")
    fc = (f"[0:v]fps={fps},scale={even(width)}:-2:flags=lanczos,split[a][b];"
          f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    args += ["-i", info.path, "-filter_complex", fc, "-loop", "0", out]
    return Job("gif", f"GIF {width}px {fps} fps", args, out, dur)


# ---------------------------------------------------------------- overlay

OVERLAY_POSITIONS = {
    "top-left": ("{m}", "{m}"), "top-right": ("W-w-{m}", "{m}"),
    "bottom-left": ("{m}", "H-h-{m}"), "bottom-right": ("W-w-{m}", "H-h-{m}"),
    "center": ("(W-w)/2", "(H-h)/2"),
}


def overlay(info: MediaInfo, overlay_path: str, x: int | None = None, y: int | None = None,
            width: int | None = None, height: int | None = None, position: str | None = None,
            margin: int = 24, opacity: float = 1.0, start: float = 0.0, keep_overlay_audio: bool = False,
            quality: str = "high", out_dir: str | None = None) -> Job:
    """Picture-in-picture: an image or video over the source.

    Give either a named `position` (with `margin`) or explicit x/y.  `width`/`height`
    scale the overlay (one of them keeps aspect when the other is None).  This is also
    the 'replace region' tool: set x/y/width/height to the box a clip was cropped from
    and the edited clip drops back in, with the original audio kept.
    """
    _check_input(info)
    if not os.path.isfile(overlay_path):
        raise OpError("the overlay file does not exist")
    if not 0 < opacity <= 1:
        raise OpError("opacity must be between 0 and 1")
    ext = os.path.splitext(overlay_path)[1].lower()
    is_image = ext in IMAGE_EXTENSIONS
    out = output_path(info.path, "overlay", out_dir)
    inputs = ["-i", info.path]
    if is_image:
        inputs += ["-loop", "1", "-i", overlay_path]
    else:
        inputs += ["-i", overlay_path]
    chain = "[1:v]format=rgba"
    if width or height:
        chain += f",scale={even(width) if width else -2}:{even(height) if height else -2}:flags=lanczos"
    if opacity < 1:
        chain += f",colorchannelmixer=aa={opacity:.3f}"
    chain += "[ov]"
    if position:
        if position not in OVERLAY_POSITIONS:
            raise OpError("unknown overlay position")
        px, py = (s.format(m=margin) for s in OVERLAY_POSITIONS[position])
    else:
        if x is None or y is None:
            raise OpError("give a position or x/y")
        px, py = str(max(0, int(x))), str(max(0, int(y)))
    enable = f":enable='gte(t,{start:.3f})'" if start > 0 else ""
    fc = f"{chain};[0:v][ov]overlay={px}:{py}:shortest=0:eof_action=pass{enable}[v]"
    maps = ["-map", "[v]"]
    if keep_overlay_audio and not is_image:
        maps += ["-map", "1:a?"]
    else:
        maps += ["-map", "0:a?"]
    args = inputs + ["-filter_complex", fc] + maps + VIDEO_CODEC + _crf(quality) + AUDIO_CODEC + ["-t", f"{info.duration:.3f}", out]
    return Job("overlay", "Overlay " + os.path.basename(overlay_path), args, out, info.duration)


# ---------------------------------------------------------- contact sheet

def contact_sheet(info: MediaInfo, columns: int = 4, rows: int = 4, tile_width: int = 320,
                  out_dir: str | None = None) -> Job:
    """A grid of evenly spaced frames: a quick visual index of the video."""
    _check_input(info)
    if not (1 <= columns <= 12 and 1 <= rows <= 12):
        raise OpError("columns and rows must be between 1 and 12")
    if not info.duration:
        raise OpError("the source needs a known duration")
    n = columns * rows
    interval = info.duration / n
    out = output_path(info.path, "sheet", out_dir, ".jpg")
    vf = f"fps=1/{interval:.4f},scale={even(tile_width)}:-2,tile={columns}x{rows}:padding=4:margin=4:color=0x101014"
    args = ["-i", info.path, "-vf", vf, "-frames:v", "1", "-q:v", "3", out]
    return Job("sheet", f"Contact sheet {columns}x{rows}", args, out, info.duration)


# ---------------------------------------------------------------- registry

TOOLS: dict[str, str] = {
    "trim": "Trim / cut",
    "join": "Join clips",
    "speed": "Speed",
    "reverse": "Reverse",
    "add_audio": "Add audio",
    "remove_audio": "Remove audio",
    "extract_audio": "Extract audio",
    "volume": "Volume & fades",
    "compress": "Compress",
    "convert": "Convert / resize",
    "rotate": "Rotate / flip",
    "crop": "Crop",
    "loop": "Loop / boomerang",
    "snapshot": "Save frame",
    "frames": "Export frames",
    "gif": "Make GIF",
    "overlay": "Overlay / replace region",
    "sheet": "Contact sheet",
}


def estimate_size(info: MediaInfo, target_mb: float) -> str:
    """Helper text for the compress tool."""
    if not info.duration:
        return ""
    kbps = target_mb * 8192 / info.duration
    return f"about {int(kbps)} kbps total for {info.duration:.0f}s"


def speed_duration(duration: float, factor: float) -> float:
    return duration / factor if factor else duration


def safe_time(t: float, duration: float) -> float:
    return min(max(0.0, t), duration if duration else t)


def frac(a: float, b: float) -> float:
    return a / b if b else 0.0


def ceil_even(v: float) -> int:
    return int(math.ceil(v / 2) * 2)
