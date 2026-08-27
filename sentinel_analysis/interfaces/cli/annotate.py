"""Command-line entry point for interactive batch annotation."""

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from sentinel_analysis.application.use_cases.annotate_tiles import BatchAnnotateTiles
from sentinel_analysis.infrastructure.annotation.filesystem import (
    FilesystemAnnotationTileSource,
    JSONAnnotationProgressRepository,
)
from sentinel_analysis.interfaces.cli.common import CLICommand
from sentinel_analysis.interfaces.desktop.annotation import OpenCVAnnotationEditor


class AnnotateCommand(CLICommand):
    def __init__(
        self,
        use_case_factory: Callable[[Path], BatchAnnotateTiles] | None = None,
    ) -> None:
        self._use_case_factory = use_case_factory

    def create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Interactively annotate SAR image tiles")
        parser.add_argument("tile_folder", help="Folder containing *_sar.png tiles")
        parser.add_argument("--start-index", "--start_idx", type=int, default=None)
        parser.add_argument("--progress", default=None, help="Progress JSON path")
        parser.add_argument("--no-lee-filter", action="store_true")
        return parser

    def _build_use_case(self, progress_path: Path) -> BatchAnnotateTiles:
        if self._use_case_factory is not None:
            return self._use_case_factory(progress_path)
        return BatchAnnotateTiles(
            FilesystemAnnotationTileSource(),
            JSONAnnotationProgressRepository(progress_path),
            OpenCVAnnotationEditor(),
        )

    def execute(self, args: argparse.Namespace, stdout: TextIO) -> int:
        tile_folder = Path(args.tile_folder).resolve()
        progress_path = Path(args.progress).resolve() if args.progress else tile_folder / "progress.json"
        summary = self._build_use_case(progress_path).execute(
            tile_folder,
            args.start_index,
            not args.no_lee_filter,
        )
        print(
            f"Processed {summary.processed_tiles} of {summary.total_tiles} tiles; "
            f"next index: {summary.next_index}.",
            file=stdout,
        )
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    return AnnotateCommand().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
