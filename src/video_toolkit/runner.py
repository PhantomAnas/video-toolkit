"""Execute Jobs (sequentially, with progress, cancel and cleanup)."""

from __future__ import annotations

import os
from typing import Callable

from . import ffmpeg as ff
from .ops import Job

ProgressFn = Callable[[float], None]
LogFn = Callable[[str], None]


def run_job(job: Job, progress: ProgressFn | None = None, log: LogFn | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> str:
    """Run one job (and its pre-jobs).  Returns the output path.

    Raises ff.JobCancelled, ff.JobFailed or ff.FFmpegMissing.
    """
    log = log or (lambda _m: None)
    try:
        for pre in job.pre_jobs:
            run_job(pre, None, log, cancelled)
        out_dir = os.path.dirname(os.path.abspath(job.output)) if not os.path.isdir(job.output) else job.output
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            raise ff.JobFailed(f"cannot create the output folder {out_dir}: {exc}", job.args) from exc
        result = ff.run(job.args, job.expected_seconds, progress, cancelled, log)
        if result.returncode != 0:
            _cleanup_output(job)
            raise ff.JobFailed(_explain(result.stderr_tail, result.returncode), result.command)
        if not os.path.isdir(job.output) and (not os.path.isfile(job.output) or os.path.getsize(job.output) == 0):
            raise ff.JobFailed("FFmpeg finished but produced no output file", result.command)
        return job.output
    except ff.JobCancelled:
        _cleanup_output(job)
        raise
    finally:
        for tmp in job.temp_files:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _cleanup_output(job: Job) -> None:
    if os.path.isfile(job.output):
        try:
            os.remove(job.output)
        except OSError:
            pass


def _explain(stderr: str, rc: int) -> str:
    s = stderr.lower()
    if "no such file" in s:
        return "an input file could not be opened (moved or deleted?)"
    if "permission denied" in s:
        return "permission denied writing the output (is the file open in another program?)"
    if "unknown encoder" in s or "encoder not found" in s:
        return "this FFmpeg build lacks the needed encoder: " + stderr
    if "invalid data found" in s or "moov atom not found" in s:
        return "the input file is corrupt or not a video: " + stderr
    if "width not divisible by 2" in s or "height not divisible by 2" in s:
        return "odd frame size; pick an even width/height"
    return (stderr or f"FFmpeg exited with code {rc}")
