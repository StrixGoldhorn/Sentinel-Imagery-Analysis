"""Test package configuration."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# Provide an isolated OS temporary directory per test process to prevent
# concurrent runners or unclosed handles from colliding on shared temp paths.
_BASE_OS_TEMP = Path(tempfile.gettempdir())
_PROCESS_TEMP_DIR = _BASE_OS_TEMP / f"sentinel_test_{os.getpid()}"
_PROCESS_TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Set environment variables so subprocesses, third-party libraries (e.g. Matplotlib),
# and standard tempfile calls all resolve to the process-isolated OS temp directory.
os.environ["TMPDIR"] = str(_PROCESS_TEMP_DIR)
os.environ["TEMP"] = str(_PROCESS_TEMP_DIR)
os.environ["TMP"] = str(_PROCESS_TEMP_DIR)
os.environ["MPLCONFIGDIR"] = str(_PROCESS_TEMP_DIR / "matplotlib")
tempfile.tempdir = str(_PROCESS_TEMP_DIR)


def _cleanup_process_temp() -> None:
    try:
        shutil.rmtree(_PROCESS_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


atexit.register(_cleanup_process_temp)
