"""Application-owned contracts for interactive tile annotation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class AnnotationTile:
    tile_id: str
    sar_path: Path
    dem_path: Path | None = None


@dataclass(frozen=True)
class AnnotationProgress:
    current_index: int = 0
    total_images: int = 0


@runtime_checkable
class AnnotationTileSource(Protocol):
    def list_tiles(self, tile_folder: Path) -> Sequence[AnnotationTile]:
        ...


@runtime_checkable
class AnnotationProgressRepository(Protocol):
    def load(self) -> AnnotationProgress:
        ...

    def save(self, progress: AnnotationProgress) -> None:
        ...


@runtime_checkable
class AnnotationEditor(Protocol):
    def edit(self, tile: AnnotationTile, label_path: Path, use_lee_filter: bool) -> bool:
        """Edit one tile and return whether processing should continue."""

        ...
