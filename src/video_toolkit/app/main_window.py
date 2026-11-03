"""The Video Toolkit window: tool sidebar, source + preview, tool panel, job queue."""

from __future__ import annotations

import ctypes
import os
import sys


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    for attempt in (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            attempt()
            return
        except Exception:  # noqa: BLE001
            continue


enable_dpi_awareness()

from PySide6.QtCore import QSize, Qt, QThread, QThreadPool, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QDesktopServices, QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__  # noqa: E402
from .. import ffmpeg as ff  # noqa: E402
from .. import ops  # noqa: E402
from ..ffmpeg import MediaInfo, fmt_time  # noqa: E402
from ..settings import APP_NAME, Settings  # noqa: E402
from . import icons  # noqa: E402
from .panels import PANELS, VIDEO_FILTER, Context, Panel  # noqa: E402
from .theme import STYLESHEET  # noqa: E402
from .widgets import Backdrop, FilePicker, GlassCard, RegionPreview, hint, pill, round_button  # noqa: E402
from .worker import FrameJob, FrameSignals, QueueWorker  # noqa: E402

STATUS_ICON = {"waiting": "clock", "running": "play", "done": "check", "failed": "x", "cancelled": "stop"}
STATUS_COLOR = {"waiting": "#9aa0b4", "running": "#0a84ff", "done": "#30d158", "failed": "#ff453a", "cancelled": "#ff9f0a"}


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"))
    return os.path.join(base, name)


def app_icon() -> QIcon:
    for name in ("icon.ico", "icon.png"):
        p = resource_path(name)
        if os.path.exists(p):
            return QIcon(p)
    return icons.icon("film")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.s = settings
        self.info: MediaInfo | None = None
        self.time = 0.0
        self.queue: list[dict] = []
        self._frame_id = 0
        self._frame_signals = FrameSignals()
        self._frame_signals.ready.connect(self._frame_ready)
        self._frame_signals.failed.connect(self._frame_failed)
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(120)
        self._frame_timer.timeout.connect(self._request_frame)

        self.setWindowTitle("Video Toolkit")
        self.setWindowIcon(app_icon())
        self.resize(1460, 900)
        self.setMinimumSize(1180, 720)
        self.setAcceptDrops(True)
        self.backdrop = Backdrop()
        self.setCentralWidget(self.backdrop)
        self.backdrop.set_image(self.s.extra.get("wallpaper"))
        self._build()
        self._build_worker()
        self._select_tool(0)
        self._check_ffmpeg()

    # ------------------------------------------------------------- layout
    def _build(self) -> None:
        root = QHBoxLayout(self.backdrop)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        # Sidebar
        side = GlassCard()
        side.setFixedWidth(250)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.pixmap("film", 24, "#ffffff", dpr=self.devicePixelRatioF()))
        title = QLabel("Video Toolkit")
        title.setObjectName("Title")
        head.addWidget(logo)
        head.addSpacing(4)
        head.addWidget(title)
        head.addStretch(1)
        side.body.addLayout(head)
        self.tools = QListWidget()
        self.tools.setIconSize(QSize(18, 18))
        self.tools.setSpacing(1)
        self.panels: list[Panel] = []
        for cls in PANELS:
            panel = cls()
            self.panels.append(panel)
            item = QListWidgetItem(icons.icon(cls.icon, "#e6e8f0"), cls.title)
            self.tools.addItem(item)
        side.body.addWidget(self.tools, 1)
        self.wallpaper_btn = pill("Wallpaper", "settings", tooltip="Pick a background image (Cancel resets)")
        self.wallpaper_btn.clicked.connect(self._pick_wallpaper)
        side.body.addWidget(self.wallpaper_btn)
        root.addWidget(side)

        # Center column
        center = QVBoxLayout()
        center.setSpacing(14)
        src = GlassCard("Source", "film")
        self.open_btn = pill("Open video", "folder", "Primary")
        self.open_btn.clicked.connect(self._open)
        src.head.addWidget(self.open_btn)
        self.src_name = QLabel("Drop a video anywhere in the window, or click Open video.")
        self.src_name.setObjectName("Hint")
        self.src_name.setWordWrap(True)
        src.body.addWidget(self.src_name)
        self.preview = RegionPreview()
        src.body.addWidget(self.preview, 1)
        scrub = QHBoxLayout()
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 10000)
        self.scrub.setEnabled(False)
        self.time_lbl = QLabel("0:00.000 / 0:00")
        self.time_lbl.setObjectName("Hint")
        self.time_lbl.setMinimumWidth(150)
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.step_back = round_button("rewind", "Back one second")
        self.step_fwd = round_button("play", "Forward one second")
        scrub.addWidget(self.step_back)
        scrub.addWidget(self.scrub, 1)
        scrub.addWidget(self.step_fwd)
        scrub.addWidget(self.time_lbl)
        src.body.addLayout(scrub)
        self.scrub.valueChanged.connect(self._on_scrub)
        self.step_back.clicked.connect(lambda: self._seek(self.time - 1))
        self.step_fwd.clicked.connect(lambda: self._seek(self.time + 1))
        center.addWidget(src, 3)

        tool = GlassCard("Tool", "wand")
        self.tool_title = tool.title_lbl
        self.stack = QStackedWidget()
        for p in self.panels:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(p)
            self.stack.addWidget(scroll)
        tool.body.addWidget(self.stack, 1)
        actions = QHBoxLayout()
        self.run_btn = pill("Run now", "play", "Go")
        self.queue_btn = pill("Add to queue", "plus")
        self.quality = QComboBox()
        for label, key in (("Best quality", "best"), ("High quality", "high"), ("Balanced", "balanced"), ("Small file", "small")):
            self.quality.addItem(label, key)
        self.quality.setCurrentIndex(max(0, self.quality.findData(self.s.quality)))
        actions.addWidget(self.run_btn)
        actions.addWidget(self.queue_btn)
        actions.addStretch(1)
        actions.addWidget(hint("Encode"))
        actions.addWidget(self.quality)
        tool.body.addLayout(actions)
        self.run_btn.clicked.connect(lambda: self._enqueue(run=True))
        self.queue_btn.clicked.connect(lambda: self._enqueue(run=False))
        center.addWidget(tool, 2)
        root.addLayout(center, 5)

        # Right column: output + queue
        right = QVBoxLayout()
        right.setSpacing(14)
        out = GlassCard("Output", "folder")
        self.out_pick = FilePicker("Save results to", "folder", "dir", placeholder="Next to the source file")
        self.out_pick.set_path(self.s.output_folder)
        out.body.addWidget(self.out_pick)
        self.open_out = pill("Open output folder", "open")
        self.open_out.clicked.connect(self._open_output)
        out.body.addWidget(self.open_out, 0, Qt.AlignmentFlag.AlignLeft)
        right.addWidget(out)

        q = GlassCard("Queue", "list")
        self.clear_done = round_button("trash", "Remove finished jobs")
        q.head.addWidget(self.clear_done)
        self.queue_list = QListWidget()
        self.queue_list.setIconSize(QSize(16, 16))
        q.body.addWidget(self.queue_list, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(10)
        q.body.addWidget(self.progress)
        self.stage = hint("Idle")
        q.body.addWidget(self.stage)
        qa = QHBoxLayout()
        self.start_btn = pill("Start queue", "play", "Primary")
        self.cancel_btn = pill("Cancel current", "stop", "Danger")
        self.cancel_btn.setEnabled(False)
        qa.addWidget(self.start_btn)
        qa.addWidget(self.cancel_btn)
        qa.addStretch(1)
        q.body.addLayout(qa)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setMinimumHeight(140)
        q.body.addWidget(self.log_view, 1)
        self.start_btn.clicked.connect(self._start_queue)
        self.cancel_btn.clicked.connect(self._cancel)
        self.clear_done.clicked.connect(self._clear_done)
        right.addWidget(q, 1)
        root.addLayout(right, 3)

        self.tools.currentRowChanged.connect(self._select_tool)
        self.preview.regionChanged.connect(self._region_dragged)
        for p in self.panels:
            p.changed.connect(self._panel_changed)

    def _build_worker(self) -> None:
        self.thread = QThread(self)
        self.worker = QueueWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.job_started.connect(self._job_started)
        self.worker.job_progress.connect(self._job_progress)
        self.worker.job_log.connect(lambda i, m: self._log(m))
        self.worker.job_done.connect(self._job_done)
        self.worker.job_failed.connect(self._job_failed)
        self.worker.job_cancelled.connect(self._job_cancelled)
        self.worker.all_done.connect(self._all_done)

    # ------------------------------------------------------------- source
    def _open(self) -> None:
        start = self.s.last_open_dir or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Open video", start, VIDEO_FILTER)
        if path:
            self.load(os.path.normpath(path))

    def load(self, path: str) -> None:
        if not os.path.isfile(path):
            self._warn(f"File not found:\n{path}")
            return
        if not ff.find_ffmpeg():
            self._check_ffmpeg()
            return
        info = ff.probe(path)
        if info is None or not (info.has_video or info.has_audio):
            self._warn("That file could not be read as a video or audio file.")
            return
        self.info = info
        self.time = 0.0
        self.s.remember(path)
        self.src_name.setText(f"{os.path.basename(path)}   ·   {info.describe()}")
        self.src_name.setToolTip(path)
        self.scrub.setEnabled(info.has_video and info.duration > 0)
        self.scrub.blockSignals(True)
        self.scrub.setValue(0)
        self.scrub.blockSignals(False)
        self.time_lbl.setText(f"0:00.000 / {fmt_time(info.duration)}")
        for p in self.panels:
            p.on_source(info)
            p.on_time(0.0)
        self._log(f"opened {path}")
        if info.has_video:
            self._request_frame()
        else:
            self.preview.set_message("Audio only: no picture to show")
        self._sync_region()

    def _on_scrub(self, v: int) -> None:
        if not self.info:
            return
        self.time = v / 10000 * self.info.duration
        self.time_lbl.setText(f"{fmt_time(self.time, ms=True)} / {fmt_time(self.info.duration)}")
        for p in self.panels:
            p.on_time(self.time)
        self._frame_timer.start()

    def _seek(self, t: float) -> None:
        if not self.info or not self.info.duration:
            return
        t = max(0.0, min(self.info.duration, t))
        self.scrub.setValue(int(t / self.info.duration * 10000))

    def _request_frame(self) -> None:
        if not self.info or not self.info.has_video:
            return
        self._frame_id += 1
        QThreadPool.globalInstance().start(
            FrameJob(self._frame_id, self.info.path, self.time, max(480, self.preview.width()), self._frame_signals))

    def _frame_ready(self, request_id: int, data: bytes) -> None:
        if request_id == self._frame_id and self.info:
            self.preview.set_frame(data, (self.info.width, self.info.height))

    def _frame_failed(self, request_id: int) -> None:
        if request_id == self._frame_id:
            self.preview.set_message("Could not decode a frame at this position")

    # ---------------------------------------------------------- tools
    def _current_panel(self) -> Panel:
        return self.panels[max(0, self.tools.currentRow())]

    def _select_tool(self, row: int) -> None:
        if row < 0:
            return
        self.tools.setCurrentRow(row)
        self.stack.setCurrentIndex(row)
        panel = self.panels[row]
        self.tool_title.setText(panel.title)
        self._sync_region()

    def _sync_region(self) -> None:
        panel = self._current_panel()
        use = bool(panel.uses_region) and self.info is not None and self.info.has_video
        self.preview.enable_region(use)
        if use:
            r = panel.region()
            if r:
                self.preview.set_region(*r)

    def _panel_changed(self) -> None:
        self._sync_region()

    def _region_dragged(self, x: int, y: int, w: int, h: int) -> None:
        self._current_panel().on_region(x, y, w, h)

    # ----------------------------------------------------------- queue
    def _context(self) -> Context:
        return Context(self.info, self.time, self.out_pick.path() or None, self.quality.currentData() or "high")

    def _enqueue(self, run: bool) -> None:
        panel = self._current_panel()
        if panel.needs_source and self.info is None:
            self._warn("Open a video first.")
            return
        if not ff.find_ffmpeg():
            self._check_ffmpeg()
            return
        try:
            jobs = panel.jobs(self._context())
        except ops.OpError as exc:
            self._warn(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._warn(f"Could not prepare the job: {exc}")
            return
        for job in jobs:
            self._add_job(job)
        self._save_settings()
        if run:
            self._start_queue()

    def _add_job(self, job: ops.Job) -> None:
        entry = {"job": job, "status": "waiting", "output": "", "error": ""}
        self.queue.append(entry)
        item = QListWidgetItem(job.title)
        self.queue_list.addItem(item)
        self._paint_item(len(self.queue) - 1)
        self._log(f"queued: {job.title} -> {job.output}")

    def _paint_item(self, i: int) -> None:
        entry = self.queue[i]
        item = self.queue_list.item(i)
        st = entry["status"]
        item.setIcon(icons.icon(STATUS_ICON[st], STATUS_COLOR[st]))
        suffix = {"done": "  ✓", "failed": "  ✗ " + entry["error"].splitlines()[0][:60] if entry["error"] else "  ✗",
                  "cancelled": "  (cancelled)", "running": "  …", "waiting": ""}[st]
        item.setText(entry["job"].title + suffix)
        item.setToolTip(entry["error"] or entry["output"] or entry["job"].output)

    def _start_queue(self) -> None:
        if self.thread.isRunning():
            return
        pending = [(i, e["job"]) for i, e in enumerate(self.queue) if e["status"] == "waiting"]
        if not pending:
            self.stage.setText("Nothing waiting in the queue")
            return
        self.worker.start(pending)
        self.cancel_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.thread.start()

    def _cancel(self) -> None:
        self.worker.cancel_current()
        self.stage.setText("Cancelling…")

    def _job_started(self, i: int) -> None:
        self.queue[i]["status"] = "running"
        self._paint_item(i)
        self.progress.setValue(0)
        self.stage.setText(self.queue[i]["job"].title)

    def _job_progress(self, i: int, frac: float) -> None:
        self.progress.setValue(int(frac * 1000))

    def _job_done(self, i: int, out: str) -> None:
        self.queue[i].update(status="done", output=out)
        self._paint_item(i)
        self.progress.setValue(1000)
        self._log(f"done: {out}")

    def _job_failed(self, i: int, msg: str) -> None:
        self.queue[i].update(status="failed", error=msg)
        self._paint_item(i)
        self._log(f"failed: {msg}")

    def _job_cancelled(self, i: int) -> None:
        self.queue[i]["status"] = "cancelled"
        self._paint_item(i)
        self._log("cancelled: " + self.queue[i]["job"].title)

    def _all_done(self) -> None:
        self.thread.quit()
        self.thread.wait(5000)
        self.cancel_btn.setEnabled(False)
        self.start_btn.setEnabled(True)
        self.run_btn.setEnabled(True)
        done = sum(1 for e in self.queue if e["status"] == "done")
        failed = sum(1 for e in self.queue if e["status"] == "failed")
        self.stage.setText(f"Finished: {done} done" + (f", {failed} failed" if failed else ""))
        last = next((e for e in reversed(self.queue) if e["status"] == "done"), None)
        if last and self.s.extra.get("auto_open_folder", True):
            pass
        if failed:
            errs = [e["error"] for e in self.queue if e["status"] == "failed"]
            QMessageBox.warning(self, "Video Toolkit", "Some jobs failed:\n\n" + "\n\n".join(errs[-3:]))

    def _clear_done(self) -> None:
        if self.thread.isRunning():
            return
        keep = [e for e in self.queue if e["status"] in ("waiting", "running")]
        self.queue = keep
        self.queue_list.clear()
        for i, e in enumerate(keep):
            self.queue_list.addItem(QListWidgetItem(e["job"].title))
            self._paint_item(i)

    # --------------------------------------------------------------- misc
    def _open_output(self) -> None:
        folder = self.out_pick.path()
        if not folder:
            last = next((e["output"] for e in reversed(self.queue) if e["output"]), None)
            folder = os.path.dirname(last) if last else (os.path.dirname(self.info.path) if self.info else "")
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self._warn("No output folder yet: open a video or pick a folder.")

    def _pick_wallpaper(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wallpaper image (Cancel resets)", os.path.expanduser("~"),
                                              "Images (*.png *.jpg *.jpeg *.webp)")
        self.s.extra["wallpaper"] = path or ""
        self.backdrop.set_image(path)
        self._save_settings()

    def _check_ffmpeg(self) -> None:
        v = ff.ffmpeg_version()
        if v:
            self._log(v)
        else:
            self.stage.setText("FFmpeg not found")
            self._log(f"FFmpeg was not found. Install it from {ff.FFMPEG_DOWNLOAD_URL} and restart.")
            QMessageBox.warning(self, "FFmpeg needed", str(ff.FFmpegMissing()))

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "Video Toolkit", text)

    def _log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _save_settings(self) -> None:
        self.s.output_folder = self.out_pick.path()
        self.s.quality = self.quality.currentData() or "high"
        try:
            self.s.save()
        except OSError as exc:
            self._log(f"could not save settings: {exc}")

    # drag and drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [os.path.normpath(u.toLocalFile()) for u in event.mimeData().urls() if u.toLocalFile()]
        videos = [p for p in paths if os.path.splitext(p)[1].lower() in ops.VIDEO_EXTENSIONS | ops.AUDIO_EXTENSIONS]
        if not videos:
            self._log("dropped files were not videos")
            return
        self.load(videos[0])
        join_panel = next((p for p in self.panels if p.key == "join"), None)
        if len(videos) > 1 and join_panel is not None:
            join_panel.list.add_paths(videos[1:])
            self._select_tool(self.panels.index(join_panel))
            self._log(f"{len(videos)} clips dropped: added to Join")
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.thread.isRunning():
            if QMessageBox.question(self, "Video Toolkit", "Jobs are running. Cancel them and quit?") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop_all()
            self.thread.quit()
            self.thread.wait(10000)
        self._save_settings()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())
    win = MainWindow(Settings.load())
    win.show()
    for a in argv:
        if os.path.isfile(a):
            win.load(os.path.normpath(a))
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
