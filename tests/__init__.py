"""Test package configuration."""

import os
import tempfile
from pathlib import Path

# Keep concurrent test processes and leaked background workers from sharing the
# same temporary directory. The workspace root remains sandbox-compatible.
_RUNTIME_TMP = Path(__file__).resolve().parent / "runtime" / f"tmp_{os.getpid()}"
_RUNTIME_TMP.mkdir(parents=True, exist_ok=True)

os.environ["TMPDIR"] = str(_RUNTIME_TMP)
os.environ["TEMP"] = str(_RUNTIME_TMP)
os.environ["TMP"] = str(_RUNTIME_TMP)
tempfile.tempdir = str(_RUNTIME_TMP)
