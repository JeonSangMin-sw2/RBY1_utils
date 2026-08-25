#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"

backend_str = str(BACKEND_DIR)
if backend_str not in sys.path:
    sys.path.insert(0, backend_str)

existing_pythonpath = os.environ.get("PYTHONPATH", "")
if backend_str not in existing_pythonpath.split(os.pathsep):
    os.environ["PYTHONPATH"] = f"{backend_str}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else backend_str

import multiprocessing

from rby1_analyzer.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
