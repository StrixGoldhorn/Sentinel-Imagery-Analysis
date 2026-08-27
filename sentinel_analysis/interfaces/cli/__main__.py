"""Unified command dispatcher for ``python -m sentinel_analysis``."""

import argparse
import sys
from collections.abc import Callable, Sequence

from sentinel_analysis.interfaces.cli.detect import main as detect_main
from sentinel_analysis.interfaces.cli.download import main as download_main
from sentinel_analysis.interfaces.cli.ingest import main as ingest_main
from sentinel_analysis.interfaces.cli.predict import main as predict_main


COMMANDS: dict[str, Callable[[Sequence[str] | None], int]] = {
    "detect": detect_main,
    "download": download_main,
    "ingest": ingest_main,
    "predict": predict_main,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentinel Imagery Analysis command line")
    parser.add_argument("command", choices=COMMANDS, nargs="?", help="Operation to run")
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.parse_args(arguments)
        return 0
    command = arguments[0]
    if command not in COMMANDS:
        parser.error(f"invalid choice: {command!r} (choose from {', '.join(COMMANDS)})")
    return COMMANDS[command](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
