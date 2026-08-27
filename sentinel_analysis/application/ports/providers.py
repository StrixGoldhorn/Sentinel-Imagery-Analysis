"""Ports for external providers and computational adapters."""

from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

from sentinel_analysis.domain.entities import (
    AISRecord,
    Acquisition,
    BoundingBox,
    ImageTile,
    ShipDetection,
)


class ImageryProvider(Protocol):
    def find_latest_acquisition(self, bbox: BoundingBox, days_ago: int = 30) -> Acquisition | None:
        ...

    def calculate_tiles(self, bbox: BoundingBox) -> list[ImageTile]:
        ...

    def download_tile(self, tile: ImageTile, acquisition: Acquisition, output_path: Path) -> None:
        ...


class ShipDetector(Protocol):
    def detect(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
    ) -> tuple[list[ShipDetection], int, int]:
        ...


class PassPredictor(Protocol):
    def predict(self, bbox: BoundingBox, api_key: str) -> list[dict[str, object]]:
        ...


class AISPlugin(Protocol):
    name: str

    def authenticate(self) -> None:
        ...

    def fetch(self, bbox: BoundingBox, time_range: tuple[datetime | None, datetime | None]) -> Iterable[AISRecord]:
        ...


class AISPluginRegistry(Protocol):
    def get_plugins(self, name: str | None = None) -> list[AISPlugin]:
        ...

