"""Command-line interfaces."""

from importlib import import_module
from typing import Any

__all__ = ["AnnotateCommand", "DetectCommand", "DownloadCommand", "IngestCommand", "PredictCommand"]

_EXPORTS = {
    "AnnotateCommand": "sentinel_analysis.interfaces.cli.annotate",
    "DetectCommand": "sentinel_analysis.interfaces.cli.detect",
    "DownloadCommand": "sentinel_analysis.interfaces.cli.download",
    "IngestCommand": "sentinel_analysis.interfaces.cli.ingest",
    "PredictCommand": "sentinel_analysis.interfaces.cli.predict",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
