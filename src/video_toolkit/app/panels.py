"""One panel per tool.  Each panel knows how to turn its inputs into Jobs.

Panels get a `Context` (current source info, scrubber time, output dir,
quality) from the window and raise `ops.OpError` with a user-facing message
when something is missing or invalid.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import ops
from ..ffmpeg import MediaInfo, fmt_bytes, fmt_time
from .widgets import ClipList, FilePicker, LabeledSlider, Segmented, TimeEdit, hint, pill, round_button

VIDEO_FILTER = "Video (" + " ".join(f"*{e}" for e in sorted(ops.VIDEO_EXTENSIONS)) + ");;All files (*)"
AUDIO_FILTER = "Audio (" + " ".join(f"*{e}" for e in sorted(ops.AUDIO_EXTENSIONS)) + ");;All files (*)"
MEDIA_FILTER = "Image or video (" + " ".join(f"*{e}" for e in sorted(ops.IMAGE_EXTENSIONS | ops.VIDEO_EXTENSIONS)) + ")"


@dataclass
class Context:
    info: MediaInfo | None
    time: float            # scrubber position
    out_dir: str | None
    quality: str


class Panel(QWidget):
    """Base: a title, a description, a form layout, and `jobs(ctx)`."""

    key = ""
    title = ""
    icon = "film"
    blurb = ""
    uses_region = False          # show the draggable rectangle on the preview
    needs_source = True
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        if self.blurb:
            self.lay.addWidget(hint(self.blurb))
        self.build()
        self.lay.addStretch(1)

    def build(self) -> None:
        pass

    def jobs(self, ctx: Context) -> list[ops.Job]:
        raise NotImplementedError

    def on_source(self, info: MediaInfo | None) -> None:
        """Called when a new source is opened."""

    def on_time(self, t: float) -> None:
        """Called when the scrubber moves."""

    def region(self) -> tuple[int, int, int, int] | None:
        return None

    def on_region(self, x: int, y: int, w: int, h: int) -> None:
        pass

    def _row(self, *widgets, stretch_last: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        for w in widgets:
            if isinstance(w, str):
                row.addWidget(hint(w))
            else:
                row.addWidget(w)
        if not stretch_last:
            row.addStretch(1)
        self.lay.addLayout(row)
        return row


def _spin(lo: int, hi: int, val: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


def _dspin(lo: float, hi: float, val: float, suffix: str = "", decimals: int = 1, step: float = 0.5) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class RangeRow(QWidget):
    """Start / end time editors with 'set from scrubber' buttons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.start = TimeEdit()
        self.end = TimeEdit()
        self.set_in = pill("Set start", "pin", tooltip="Use the preview position as the start")
        self.set_out = pill("Set end", "pin", tooltip="Use the preview position as the end")
        lay.addWidget(hint("Start"))
        lay.addWidget(self.start)
        lay.addWidget(self.set_in)
        lay.addSpacing(10)
        lay.addWidget(hint("End"))
        lay.addWidget(self.end)
        lay.addWidget(self.set_out)
        self.length = hint("")
        lay.addSpacing(10)
        lay.addWidget(self.length)
        lay.addStretch(1)
        self._t = 0.0
        self.set_in.clicked.connect(lambda: self.start.set_seconds(self._t))
        self.set_out.clicked.connect(lambda: self.end.set_seconds(self._t))
        self.start.timeChanged.connect(self._update)
        self.end.timeChanged.connect(self._update)

    def on_source(self, info: MediaInfo | None) -> None:
        d = info.duration if info else 0.0
        self.start.set_max(d)
        self.end.set_max(d)
        self.start.set_seconds(0.0)
        self.end.set_seconds(d)
        self._update()

    def on_time(self, t: float) -> None:
        self._t = t

    def _update(self, *_: object) -> None:
        d = self.end.seconds() - self.start.seconds()
        self.length.setText(f"length {fmt_time(d, ms=True)}" if d > 0 else "end must be after start")

    def values(self) -> tuple[float, float]:
        return self.start.seconds(), self.end.seconds()


# ================================================================ panels

class TrimPanel(Panel):
    key, title, icon = "trim", "Trim / cut", "scissors"
    blurb = "Keep one range, or several ranges joined into a single clip."

    def build(self) -> None:
        self.range = RangeRow()
        self.lay.addWidget(self.range)
        self.mode = Segmented([("precise", "Precise (re-encode)"), ("fast", "Fast (copy, keyframe accurate)")])
        self._row(self.mode)
        self.multi = QCheckBox("Keep several ranges and join them")
        self._row(self.multi)
        self.segments: list[tuple[float, float]] = []
        self.seg_label = hint("No extra ranges added yet.", faint=True)
        self.add_seg = pill("Add this range to the list", "plus")
        self.clear_seg = round_button("trash", "Clear the list")
        self._row(self.add_seg, self.clear_seg, self.seg_label)
        self.add_seg.clicked.connect(self._add)
        self.clear_seg.clicked.connect(self._clear)
        self.multi.toggled.connect(lambda on: (self.add_seg.setEnabled(on), self.clear_seg.setEnabled(on)))
        self.add_seg.setEnabled(False)
        self.clear_seg.setEnabled(False)

    def _add(self) -> None:
        s, e = self.range.values()
        if e <= s:
            return
        self.segments.append((s, e))
        self.seg_label.setText("Ranges: " + ", ".join(f"{fmt_time(a)}-{fmt_time(b)}" for a, b in self.segments))

    def _clear(self) -> None:
        self.segments.clear()
        self.seg_label.setText("No extra ranges added yet.")

    def on_source(self, info):
        self.range.on_source(info)
        self._clear()

    def on_time(self, t):
        self.range.on_time(t)

    def jobs(self, ctx):
        s, e = self.range.values()
        precise = self.mode.value() == "precise"
        if self.multi.isChecked():
            segs = list(self.segments)
            if not segs or segs[-1] != (s, e):
                segs.append((s, e))
            return ops.trim_segments(ctx.info, segs, True, precise, ctx.quality, ctx.out_dir)
        return [ops.trim(ctx.info, s, e, precise, ctx.quality, ctx.out_dir)]


class JoinPanel(Panel):
    key, title, icon = "join", "Join clips", "merge"
    blurb = "Concatenate clips in order. The opened video is added first; drag to reorder."
    needs_source = False

    def build(self) -> None:
        self.list = ClipList()
        self.list.setMinimumHeight(140)
        self.lay.addWidget(self.list)
        self.add = pill("Add clips", "plus")
        self.remove = round_button("x", "Remove selected")
        self._row(self.add, self.remove)
        self.mode = Segmented([("copy", "Fast copy (same format)"), ("reencode", "Re-encode (any clips)")])
        self._row(self.mode)
        self.add.clicked.connect(self._add)
        self.remove.clicked.connect(self.list.remove_selected)

    def _add(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add clips", os.path.expanduser("~"), VIDEO_FILTER)
        self.list.add_paths([os.path.normpath(f) for f in files])

    def on_source(self, info):
        if info and info.path not in self.list.paths():
            self.list.add_paths([info.path])

    def jobs(self, ctx):
        from .. import ffmpeg as ff
        infos = []
        for p in self.list.paths():
            i = ff.probe(p)
            if i is None:
                raise ops.OpError(f"cannot read {os.path.basename(p)}")
            infos.append(i)
        return [ops.join(infos, self.mode.value() == "reencode", ctx.quality, ctx.out_dir)]


class SpeedPanel(Panel):
    key, title, icon = "speed", "Speed", "gauge"
    blurb = "Speed up or slow down. Audio keeps its pitch unless you say otherwise."

    def build(self) -> None:
        self.factor = LabeledSlider("Speed", 0.25, 4.0, 0.25, "{:.2f}x")
        self.factor.set_value(2.0)
        self.lay.addWidget(self.factor)
        self.presets = Segmented([("0.5", "0.5x"), ("0.75", "0.75x"), ("1.5", "1.5x"), ("2", "2x"), ("4", "4x")])
        self.presets.set_value("2")
        self.presets.changed.connect(lambda k: self.factor.set_value(float(k)))
        self._row(self.presets)
        self.pitch = QCheckBox("Let the pitch change with speed (chipmunk / slow-mo voice)")
        self._row(self.pitch)
        self.result = hint("")
        self.lay.addWidget(self.result)
        self.factor.valueChanged.connect(self._update)

    def _update(self, *_):
        info = getattr(self, "_info", None)
        if info:
            self.result.setText(f"Result: {fmt_time(info.duration / self.factor.value())}")

    def on_source(self, info):
        self._info = info
        self._update()

    def jobs(self, ctx):
        return [ops.speed(ctx.info, self.factor.value(), not self.pitch.isChecked(), ctx.quality, ctx.out_dir)]


class ReversePanel(Panel):
    key, title, icon = "reverse", "Reverse", "rewind"
    blurb = "Play the clip backwards, audio included. Works on clips up to 10 minutes."

    def jobs(self, ctx):
        return [ops.reverse(ctx.info, ctx.quality, ctx.out_dir)]


class AddAudioPanel(Panel):
    key, title, icon = "add_audio", "Add audio", "music"
    blurb = "Replace the soundtrack, or mix a track under the original audio. Shorter tracks loop; longer ones are cut to the video."

    def build(self) -> None:
        self.file = FilePicker("Audio file", "music", "file", AUDIO_FILTER)
        self.lay.addWidget(self.file)
        self.mode = Segmented([("replace", "Replace original"), ("mix", "Mix with original")])
        self._row(self.mode)
        self.volume = LabeledSlider("New track volume", 0.0, 2.0, 0.05, "{:.0%}")
        self.volume.set_value(1.0)
        self.orig = LabeledSlider("Original volume (mix)", 0.0, 2.0, 0.05, "{:.0%}")
        self.orig.set_value(0.3)
        r = QHBoxLayout()
        r.addWidget(self.volume, 1)
        r.addWidget(self.orig, 1)
        self.lay.addLayout(r)
        self.fade = _dspin(0, 30, 2.0, " s fade out")
        self._row("Fade out", self.fade)

    def jobs(self, ctx):
        if not self.file.path():
            raise ops.OpError("choose an audio file")
        return [ops.add_audio(ctx.info, self.file.path(), self.mode.value(), self.volume.value(),
                              self.orig.value(), True, self.fade.value(), ctx.out_dir)]


class RemoveAudioPanel(Panel):
    key, title, icon = "remove_audio", "Remove audio", "mute"
    blurb = "Strip the audio track without re-encoding the video (instant)."

    def jobs(self, ctx):
        return [ops.remove_audio(ctx.info, ctx.out_dir)]


class ExtractAudioPanel(Panel):
    key, title, icon = "extract_audio", "Extract audio", "download"
    blurb = "Save the soundtrack as its own file."

    def build(self) -> None:
        self.fmt = Segmented([("mp3", "MP3"), ("wav", "WAV"), ("m4a", "M4A"), ("flac", "FLAC"), ("copy", "Original")])
        self._row(self.fmt)

    def jobs(self, ctx):
        return [ops.extract_audio(ctx.info, self.fmt.value(), ctx.out_dir)]


class VolumePanel(Panel):
    key, title, icon = "volume", "Volume & fades", "volume"
    blurb = "Change loudness, add fade in / fade out, or normalize to a broadcast-style level."

    def build(self) -> None:
        self.gain = LabeledSlider("Volume", 0.0, 3.0, 0.05, "{:.0%}")
        self.gain.set_value(1.0)
        self.lay.addWidget(self.gain)
        self.fade_in = _dspin(0, 30, 0.0, " s")
        self.fade_out = _dspin(0, 30, 0.0, " s")
        self._row("Fade in", self.fade_in, "Fade out", self.fade_out)
        self.normalize = QCheckBox("Normalize loudness (EBU R128, -16 LUFS)")
        self._row(self.normalize)

    def jobs(self, ctx):
        return [ops.volume(ctx.info, self.gain.value(), self.fade_in.value(), self.fade_out.value(),
                           self.normalize.isChecked(), ctx.out_dir)]


class CompressPanel(Panel):
    key, title, icon = "compress", "Compress", "shrink"
    blurb = "Shrink the file: pick a quality, or aim for a target size (e.g. 25 MB for messaging apps)."

    def build(self) -> None:
        self.mode = Segmented([("quality", "By quality"), ("size", "To a target size")])
        self._row(self.mode)
        self.quality = Segmented([("high", "High"), ("balanced", "Balanced"), ("small", "Small")])
        self.quality.set_value("balanced")
        self._row(self.quality)
        self.size = _dspin(0.5, 5000, 25.0, " MB", 1, 5)
        self.size_hint = hint("")
        self._row("Target", self.size, self.size_hint)
        self.height = QComboBox()
        for label, v in (("Keep resolution", 0), ("Max 1080p", 1080), ("Max 720p", 720), ("Max 480p", 480)):
            self.height.addItem(label, v)
        self._row(self.height)
        self.size.valueChanged.connect(self._update)
        self.mode.changed.connect(self._update)

    def _update(self, *_):
        info = getattr(self, "_info", None)
        if info and self.mode.value() == "size":
            self.size_hint.setText(ops.estimate_size(info, self.size.value()))
        else:
            self.size_hint.setText("")

    def on_source(self, info):
        self._info = info
        if info and info.size_bytes:
            self.size.setValue(max(0.5, round(info.size_bytes / 1024 / 1024 / 2, 1)))
        self._update()

    def jobs(self, ctx):
        target = self.size.value() if self.mode.value() == "size" else None
        return [ops.compress(ctx.info, target, self.quality.value(), self.height.currentData() or None, ctx.out_dir)]


class ConvertPanel(Panel):
    key, title, icon = "convert", "Convert / resize", "convert"
    blurb = "Change the container and codec, resize (aspect kept) or change the frame rate."

    def build(self) -> None:
        self.fmt = Segmented([("mp4", "MP4"), ("mkv", "MKV"), ("mov", "MOV"), ("webm", "WebM"), ("avi", "AVI")])
        self._row(self.fmt)
        self.height = QComboBox()
        for label, v in (("Keep size", 0), ("2160p (4K)", 2160), ("1440p", 1440), ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)):
            self.height.addItem(label, v)
        self.fps = QComboBox()
        for label, v in (("Keep fps", 0), ("24", 24), ("25", 25), ("30", 30), ("50", 50), ("60", 60)):
            self.fps.addItem(label, v)
        self._row("Size", self.height, "Frame rate", self.fps)

    def jobs(self, ctx):
        return [ops.convert(ctx.info, self.fmt.value(), self.height.currentData() or None,
                            self.fps.currentData() or None, ctx.quality, ctx.out_dir)]


class RotatePanel(Panel):
    key, title, icon = "rotate", "Rotate / flip", "rotate"
    blurb = "Fix sideways phone footage or mirror a clip."

    def build(self) -> None:
        self.how = Segmented([("90", "90° right"), ("270", "90° left"), ("180", "180°"), ("hflip", "Mirror"), ("vflip", "Upside down")])
        self._row(self.how)

    def jobs(self, ctx):
        return [ops.rotate(ctx.info, self.how.value(), ctx.quality, ctx.out_dir)]


class CropPanel(Panel):
    key, title, icon = "crop", "Crop", "crop"
    blurb = "Drag the box on the preview, or pick an aspect preset. Optionally limit to a time range."
    uses_region = True

    def build(self) -> None:
        self.presets = Segmented([("free", "Free"), ("9:16", "9:16"), ("1:1", "1:1"), ("4:5", "4:5"), ("16:9", "16:9"), ("4:3", "4:3")])
        self._row(self.presets)
        self.x, self.y = _spin(0, 20000, 0), _spin(0, 20000, 0)
        self.w, self.h = _spin(2, 20000, 100), _spin(2, 20000, 100)
        self._row("X", self.x, "Y", self.y, "Width", self.w, "Height", self.h)
        self.use_range = QCheckBox("Only a time range")
        self._row(self.use_range)
        self.range = RangeRow()
        self.lay.addWidget(self.range)
        self.range.setVisible(False)
        self.use_range.toggled.connect(self.range.setVisible)
        self.presets.changed.connect(self._preset)
        for s in (self.x, self.y, self.w, self.h):
            s.valueChanged.connect(lambda *_: self.changed.emit())

    def _preset(self, key: str) -> None:
        info = getattr(self, "_info", None)
        if not info or key == "free":
            return
        x, y, w, h = ops.crop_preset(info, key)
        self._set(x, y, w, h)

    def _set(self, x, y, w, h) -> None:
        for s in (self.x, self.y, self.w, self.h):
            s.blockSignals(True)
        self.x.setValue(x); self.y.setValue(y); self.w.setValue(w); self.h.setValue(h)
        for s in (self.x, self.y, self.w, self.h):
            s.blockSignals(False)
        self.changed.emit()

    def on_source(self, info):
        self._info = info
        self.range.on_source(info)
        if info:
            for s in (self.x, self.w):
                s.setMaximum(max(2, info.width))
            for s in (self.y, self.h):
                s.setMaximum(max(2, info.height))
            x, y, w, h = ops.crop_preset(info, "1:1") if info.width != info.height else (0, 0, info.width, info.height)
            self._set(x, y, w, h)

    def on_time(self, t):
        self.range.on_time(t)

    def region(self):
        return self.x.value(), self.y.value(), self.w.value(), self.h.value()

    def on_region(self, x, y, w, h):
        self.presets.set_value("free")
        self._set(x, y, w, h)

    def jobs(self, ctx):
        s, e = self.range.values() if self.use_range.isChecked() else (None, None)
        return [ops.crop(ctx.info, self.x.value(), self.y.value(), self.w.value(), self.h.value(), s, e, ctx.quality, ctx.out_dir)]


class LoopPanel(Panel):
    key, title, icon = "loop", "Loop / boomerang", "repeat"
    blurb = "Repeat the clip, or play it forward then backward (boomerang)."

    def build(self) -> None:
        self.times = _spin(1, 50, 2, " times")
        self._row("Repeat", self.times)
        self.boom = QCheckBox("Boomerang (forward, then reversed)")
        self._row(self.boom)

    def jobs(self, ctx):
        return [ops.loop(ctx.info, self.times.value(), self.boom.isChecked(), ctx.quality, ctx.out_dir)]


class SnapshotPanel(Panel):
    key, title, icon = "snapshot", "Save frame", "camera"
    blurb = "Save the frame shown in the preview as an image. Scrub to the moment you want."

    def build(self) -> None:
        self.at = TimeEdit()
        self.use_scrub = pill("Use preview position", "pin")
        self.fmt = Segmented([("png", "PNG (lossless)"), ("jpg", "JPG")])
        self._row("Time", self.at, self.use_scrub, self.fmt)
        self.use_scrub.clicked.connect(lambda: self.at.set_seconds(self._t))
        self._t = 0.0

    def on_source(self, info):
        self.at.set_max(info.duration if info else 0)

    def on_time(self, t):
        self._t = t
        self.at.set_seconds(t)

    def jobs(self, ctx):
        return [ops.snapshot(ctx.info, self.at.seconds(), self.fmt.value(), ctx.out_dir)]


class FramesPanel(Panel):
    key, title, icon = "frames", "Export frames", "frames"
    blurb = "Save a frame every N seconds (or every single frame) into a folder."

    def build(self) -> None:
        self.every = _dspin(0, 3600, 1.0, " s", 2, 0.5)
        self.fmt = Segmented([("jpg", "JPG"), ("png", "PNG")])
        self.height = QComboBox()
        for label, v in (("Full size", 0), ("1080p", 1080), ("720p", 720), ("480p", 480), ("240p", 240)):
            self.height.addItem(label, v)
        self._row("Every", self.every, self.fmt, self.height)
        self.lay.addWidget(hint("0 s exports every frame (can be thousands of files).", faint=True))

    def jobs(self, ctx):
        return [ops.export_frames(ctx.info, self.every.value(), self.fmt.value(), self.height.currentData() or None, ctx.out_dir)]


class GifPanel(Panel):
    key, title, icon = "gif", "Make GIF", "gif"
    blurb = "A high-quality looping GIF from a range (keep it under a minute)."

    def build(self) -> None:
        self.range = RangeRow()
        self.lay.addWidget(self.range)
        self.width = QComboBox()
        for v in (320, 480, 640, 800):
            self.width.addItem(f"{v} px wide", v)
        self.width.setCurrentIndex(1)
        self.fps = QComboBox()
        for v in (10, 12, 15, 20, 25):
            self.fps.addItem(f"{v} fps", v)
        self.fps.setCurrentIndex(2)
        self._row(self.width, self.fps)

    def on_source(self, info):
        self.range.on_source(info)
        if info and info.duration > 10:
            self.range.end.set_seconds(min(info.duration, 5.0))

    def on_time(self, t):
        self.range.on_time(t)

    def jobs(self, ctx):
        s, e = self.range.values()
        return [ops.gif(ctx.info, s, e, self.fps.currentData(), self.width.currentData(), ctx.out_dir)]


class OverlayPanel(Panel):
    key, title, icon = "overlay", "Overlay / replace region", "layers"
    blurb = ("Place an image or video over the source (watermark, picture-in-picture). "
             "To replace a region: crop it out with the Crop tool, edit that clip elsewhere, then drop it back "
             "here with 'Custom box' at the same position; the original audio is kept.")
    uses_region = True

    def build(self) -> None:
        self.file = FilePicker("Overlay image or video", "layers", "file", MEDIA_FILTER)
        self.lay.addWidget(self.file)
        self.mode = Segmented([("corner", "Corner"), ("box", "Custom box (drag on preview)")])
        self._row(self.mode)
        self.position = QComboBox()
        for k, label in (("bottom-right", "Bottom right"), ("bottom-left", "Bottom left"), ("top-right", "Top right"), ("top-left", "Top left"), ("center", "Center")):
            self.position.addItem(label, k)
        self.scale = LabeledSlider("Overlay width", 0.05, 1.0, 0.01, "{:.0%} of frame")
        self.scale.set_value(0.25)
        self.corner_row = QWidget()
        cr = QHBoxLayout(self.corner_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(self.position)
        cr.addWidget(self.scale, 1)
        self.lay.addWidget(self.corner_row)
        self.x, self.y = _spin(0, 20000, 0), _spin(0, 20000, 0)
        self.w, self.h = _spin(2, 20000, 320), _spin(2, 20000, 180)
        self.box_row = QWidget()
        br = QHBoxLayout(self.box_row)
        br.setContentsMargins(0, 0, 0, 0)
        for label, s in (("X", self.x), ("Y", self.y), ("Width", self.w), ("Height", self.h)):
            br.addWidget(hint(label))
            br.addWidget(s)
        br.addStretch(1)
        self.lay.addWidget(self.box_row)
        self.box_row.setVisible(False)
        self.opacity = LabeledSlider("Opacity", 0.1, 1.0, 0.05, "{:.0%}")
        self.opacity.set_value(1.0)
        self.lay.addWidget(self.opacity)
        self.start = TimeEdit()
        self.keep_audio = QCheckBox("Use the overlay's audio instead of the original")
        self._row("Show from", self.start, self.keep_audio)
        self.mode.changed.connect(self._mode)
        for s in (self.x, self.y, self.w, self.h):
            s.valueChanged.connect(lambda *_: self.changed.emit())

    def _mode(self, key: str) -> None:
        self.corner_row.setVisible(key == "corner")
        self.box_row.setVisible(key == "box")
        self.changed.emit()

    @property
    def uses_region(self) -> bool:  # type: ignore[override]
        return self.mode.value() == "box"

    def on_source(self, info):
        self.start.set_max(info.duration if info else 0)
        if info:
            for s in (self.x, self.w):
                s.setMaximum(max(2, info.width))
            for s in (self.y, self.h):
                s.setMaximum(max(2, info.height))
            self.w.setValue(max(2, info.width // 3))
            self.h.setValue(max(2, info.height // 3))

    def region(self):
        return self.x.value(), self.y.value(), self.w.value(), self.h.value()

    def on_region(self, x, y, w, h):
        for s in (self.x, self.y, self.w, self.h):
            s.blockSignals(True)
        self.x.setValue(x); self.y.setValue(y); self.w.setValue(w); self.h.setValue(h)
        for s in (self.x, self.y, self.w, self.h):
            s.blockSignals(False)

    def jobs(self, ctx):
        if not self.file.path():
            raise ops.OpError("choose an overlay image or video")
        common = dict(opacity=self.opacity.value(), start=self.start.seconds(),
                      keep_overlay_audio=self.keep_audio.isChecked(), quality=ctx.quality, out_dir=ctx.out_dir)
        if self.mode.value() == "corner":
            width = int(ctx.info.width * self.scale.value())
            return [ops.overlay(ctx.info, self.file.path(), position=self.position.currentData(), width=width, **common)]
        return [ops.overlay(ctx.info, self.file.path(), x=self.x.value(), y=self.y.value(),
                            width=self.w.value(), height=self.h.value(), **common)]


class SheetPanel(Panel):
    key, title, icon = "sheet", "Contact sheet", "grid"
    blurb = "A grid of evenly spaced frames: a one-image overview of the whole video."

    def build(self) -> None:
        self.cols, self.rows = _spin(1, 12, 4), _spin(1, 12, 4)
        self.tile = QComboBox()
        for v in (240, 320, 480):
            self.tile.addItem(f"{v} px tiles", v)
        self.tile.setCurrentIndex(1)
        self._row("Columns", self.cols, "Rows", self.rows, self.tile)

    def jobs(self, ctx):
        return [ops.contact_sheet(ctx.info, self.cols.value(), self.rows.value(), self.tile.currentData(), ctx.out_dir)]


PANELS: list[type[Panel]] = [
    TrimPanel, JoinPanel, SpeedPanel, ReversePanel, AddAudioPanel, RemoveAudioPanel, ExtractAudioPanel,
    VolumePanel, CompressPanel, ConvertPanel, RotatePanel, CropPanel, LoopPanel, SnapshotPanel, FramesPanel,
    GifPanel, OverlayPanel, SheetPanel,
]


def describe_size(info: MediaInfo) -> str:
    return fmt_bytes(info.size_bytes)
