"""Thin entry point for PyInstaller; `python -m video_toolkit` is the same thing."""

import sys

from video_toolkit.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
