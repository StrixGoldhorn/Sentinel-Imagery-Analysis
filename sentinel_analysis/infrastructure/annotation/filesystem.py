"""Filesystem tile discovery and annotation progress persistence."""

import json
from pathlib import Path

from sentinel_analysis.application.ports.annotation import AnnotationProgress, AnnotationTile


class FilesystemAnnotationTileSource:
    def list_tiles(self, tile_folder: Path) -> list[AnnotationTile]:
        folder = tile_folder.resolve()
        if not folder.is_dir():
            raise FileNotFoundError(f"Tile folder not found: {folder}")
        tiles = []
        for sar_path in sorted(folder.glob("*_sar.png")):
            tile_id = sar_path.stem.removesuffix("_sar")
            dem_candidate = folder / f"{tile_id}_dem.png"
            tiles.append(
                AnnotationTile(
                    tile_id,
                    sar_path,
                    dem_candidate if dem_candidate.is_file() else None,
                )
            )
        return tiles


class JSONAnnotationProgressRepository:
    def __init__(self, progress_path: Path) -> None:
        self._path = progress_path.resolve()

    def load(self) -> AnnotationProgress:
        if not self._path.is_file():
            return AnnotationProgress()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            current = int(payload.get("current_index", 0))
            total = int(payload.get("total_images", 0))
            if current < 0 or total < 0:
                raise ValueError("Progress values cannot be negative")
            return AnnotationProgress(current, total)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid annotation progress file: {self._path}") from exc

    def save(self, progress: AnnotationProgress) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {
                        "current_index": progress.current_index,
                        "total_images": progress.total_images,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        finally:
            temporary.unlink(missing_ok=True)
