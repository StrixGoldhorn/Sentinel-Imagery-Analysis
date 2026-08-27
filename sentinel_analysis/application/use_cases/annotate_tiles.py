"""Coordinate an interactive batch tile-annotation session."""

from dataclasses import dataclass
from pathlib import Path

from sentinel_analysis.application.ports.annotation import (
    AnnotationEditor,
    AnnotationProgress,
    AnnotationProgressRepository,
    AnnotationTileSource,
)


@dataclass(frozen=True)
class AnnotationSummary:
    total_tiles: int
    processed_tiles: int
    next_index: int
    completed: bool


class BatchAnnotateTiles:
    def __init__(
        self,
        tiles: AnnotationTileSource,
        progress: AnnotationProgressRepository,
        editor: AnnotationEditor,
    ) -> None:
        self._tiles = tiles
        self._progress = progress
        self._editor = editor

    def execute(
        self,
        tile_folder: Path,
        start_index: int | None = None,
        use_lee_filter: bool = True,
    ) -> AnnotationSummary:
        if start_index is not None and (
            isinstance(start_index, bool) or not isinstance(start_index, int) or start_index < 0
        ):
            raise ValueError("Annotation start index must be a non-negative integer")

        tiles = list(self._tiles.list_tiles(tile_folder))
        total = len(tiles)
        saved = self._progress.load()
        current = saved.current_index if start_index is None else start_index
        current = min(current, total)
        processed = 0
        label_directory = tile_folder / "labels"

        for index in range(current, total):
            tile = tiles[index]
            should_continue = self._editor.edit(
                tile,
                label_directory / f"{tile.tile_id}.txt",
                use_lee_filter,
            )
            current = index + 1
            processed += 1
            self._progress.save(AnnotationProgress(current, total))
            if not should_continue:
                return AnnotationSummary(total, processed, current, False)

        return AnnotationSummary(total, processed, current, current >= total)
