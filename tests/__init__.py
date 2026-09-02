"""Test package configuration."""

import os
import tempfile
from pathlib import Path

# Ensure all temporary directories created by tests reside within the workspace
_RUNTIME_TMP = Path(__file__).resolve().parent / "runtime" / "tmp"
_RUNTIME_TMP.mkdir(parents=True, exist_ok=True)

os.environ["TMPDIR"] = str(_RUNTIME_TMP)
os.environ["TEMP"] = str(_RUNTIME_TMP)
os.environ["TMP"] = str(_RUNTIME_TMP)
tempfile.tempdir = str(_RUNTIME_TMP)
