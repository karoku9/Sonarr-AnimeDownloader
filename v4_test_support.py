"""Hermetic temp storage for V4 tests.

Runtime persistence is intentionally restricted to the repository work/ tree.
Tests create that ignored root explicitly so clean clones never depend on
pre-existing local state.
"""
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parent
WORK_ROOT=ROOT/"work"

def repo_tempdir():
    WORK_ROOT.mkdir(parents=True,exist_ok=True)
    return TemporaryDirectory(dir=WORK_ROOT)
