"""Background work: the job queue runner and preview-frame fetches."""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import ffmpeg as ff
from .. import preview, runner
from ..ops import Job


class QueueWorker(QObject):
    """Runs queued jobs one after another on a QThread."""

    job_started = Signal(int)                 # queue index
    job_progress = Signal(int, float)
    job_log = Signal(int, str)
    job_done = Signal(int, str)               # index, output path
    job_failed = Signal(int, str)
    job_cancelled = Signal(int)
    all_done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()
        self._stop_all = threading.Event()
        self.jobs: list[tuple[int, Job]] = []

    def cancel_current(self) -> None:
        self._cancel.set()

    def stop_all(self) -> None:
        self._stop_all.set()
        self._cancel.set()

    def start(self, jobs: list[tuple[int, Job]]) -> None:
        self.jobs = list(jobs)
        self._cancel.clear()
        self._stop_all.clear()

    def run(self) -> None:
        for index, job in self.jobs:
            if self._stop_all.is_set():
                self.job_cancelled.emit(index)
                continue
            self._cancel.clear()
            self.job_started.emit(index)
            try:
                out = runner.run_job(job, progress=lambda f, i=index: self.job_progress.emit(i, f),
                                     log=lambda m, i=index: self.job_log.emit(i, m),
                                     cancelled=self._cancel.is_set)
            except ff.JobCancelled:
                self.job_cancelled.emit(index)
                continue
            except (ff.JobFailed, ff.FFmpegMissing) as exc:
                self.job_failed.emit(index, str(exc))
                continue
            except Exception:  # noqa: BLE001
                self.job_failed.emit(index, "unexpected error:\n" + traceback.format_exc())
                continue
            self.job_done.emit(index, out)
        self.all_done.emit()


class FrameSignals(QObject):
    ready = Signal(int, bytes)
    failed = Signal(int)


class FrameJob(QRunnable):
    def __init__(self, request_id: int, path: str, at: float, max_width: int, signals: FrameSignals) -> None:
        super().__init__()
        self.request_id, self.path, self.at, self.max_width, self.signals = request_id, path, at, max_width, signals

    def run(self) -> None:
        data = preview.frame_png(self.path, self.at, self.max_width)
        if data:
            self.signals.ready.emit(self.request_id, data)
        else:
            self.signals.failed.emit(self.request_id)
