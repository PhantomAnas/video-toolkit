"""Render the window with a generated demo clip and save docs/screenshot.png.

    python scripts/screenshot.py

Uses a throw-away settings file.  On Windows the real platform is used (the
offscreen one has no system fonts), so the window appears for a moment.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import video_toolkit.settings as sm  # noqa: E402
from video_toolkit import ffmpeg as ff  # noqa: E402

tmp = tempfile.mkdtemp(prefix="vtk-shot-")
sm.config_path = lambda: os.path.join(tmp, "settings.json")  # type: ignore[assignment]

clip = os.path.join(tmp, "holiday_clip.mp4")
subprocess.run([ff.find_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "gradients=size=1280x720:duration=12:speed=0.02:nb_colors=4:c0=0x1d4ed8:c1=0xdb2777:c2=0xf59e0b:c3=0x0d9488",
                "-f", "lavfi", "-i", "sine=frequency=330:duration=12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-c:a", "aac", "-shortest", clip], check=True)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from video_toolkit.app.main_window import MainWindow  # noqa: E402
from video_toolkit.app.theme import STYLESHEET  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(STYLESHEET)
win = MainWindow(sm.Settings())
win.resize(1480, 920)
win.show()
win.load(clip)
win.scrub.setValue(3500)
crop = next(i for i, p in enumerate(win.panels) if p.key == "crop")
win._select_tool(crop)
win.panels[crop].presets.set_value("9:16")
win._add_job(__import__("video_toolkit.ops", fromlist=["x"]).trim(win.info, 1.0, 4.5, out_dir=tmp))
win.queue[-1]["status"] = "done"
win._paint_item(0)
win._add_job(__import__("video_toolkit.ops", fromlist=["x"]).gif(win.info, 2, 5, out_dir=tmp))
win.log_view.clear()
win._log("ffmpeg version 6.1.1 found")
win._log("opened holiday_clip.mp4")
win._log("done: holiday_clip_trim_1_0-4_5.mp4")
win._log("queued: GIF 480px 15 fps -> holiday_clip_gif.gif")


def shoot() -> None:
    out = os.path.join(ROOT, "docs", "screenshot.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    win.grab().save(out)
    print("wrote", out)
    app.quit()


QTimer.singleShot(2500, shoot)
app.exec()
