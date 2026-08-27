"""Filesystem implementation of scan persistence."""

import json
import shutil
from datetime import datetime
from pathlib import Path

from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan


class FilesystemScanRepository:
    def __init__(self, output_root: Path | str) -> None:
        self.root = Path(output_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_name(folder_name: str) -> str:
        if (
            not isinstance(folder_name, str)
            or not folder_name
            or folder_name != folder_name.strip()
            or Path(folder_name).name != folder_name
            or folder_name in {".", ".."}
        ):
            raise ValueError("Invalid scan folder name")
        return folder_name

    def _directory(self, folder_name: str) -> Path:
        name = self._validate_name(folder_name)
        directory = (self.root / name).resolve()
        if directory.parent != self.root:
            raise ValueError("Invalid scan folder path")
        return directory

    def prepare(self, folder_name: str) -> Path:
        directory = self._directory(folder_name)
        (directory / "images").mkdir(parents=True, exist_ok=False)
        return directory

    def save(self, scan: Scan) -> None:
        directory = self._directory(scan.folder_name)
        if not directory.exists():
            raise FileNotFoundError(f"Scan workspace does not exist: {scan.folder_name}")
        metadata = dict(scan.metadata)
        metadata.setdefault("acquisition_datetime", scan.acquisition.acquired_at.isoformat())
        metadata.setdefault("satellite", scan.acquisition.satellite)
        settings = metadata.setdefault("settings", {})
        if not isinstance(settings, dict):
            raise ValueError("Scan metadata settings must be an object")
        settings.setdefault("bbox", scan.bbox.as_list())
        settings.setdefault("datasource", scan.acquisition.product_type)
        if scan.acquisition.product_id is not None:
            metadata.setdefault("product_id", scan.acquisition.product_id)
        image_path = Path(scan.image_path).resolve()
        image_directory = (directory / "images").resolve()
        if image_path.parent != image_directory:
            raise ValueError("Scan image must be stored inside its workspace image directory")
        metadata["image_filename"] = Path(scan.image_path).name
        temporary = directory / "metadata.json.tmp"
        temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        temporary.replace(directory / "metadata.json")

    def get(self, folder_name: str) -> Scan | None:
        directory = self._directory(folder_name)
        metadata_path = directory / "metadata.json"
        if not directory.is_dir() or not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            bbox = BoundingBox.from_sequence(metadata["settings"]["bbox"])
            acquired = datetime.fromisoformat(str(metadata["acquisition_datetime"]).replace("Z", "+00:00"))
            acquisition = Acquisition(
                acquired,
                str(metadata.get("satellite", "Sentinel-1")),
                str(metadata.get("settings", {}).get("datasource", "sentinel-1-grd")),
                metadata.get("product_id"),
            )
            image_dir = directory / "images"
            configured_name = str(metadata.get("image_filename", ""))
            configured = image_dir / configured_name
            if configured_name and configured.name != configured_name:
                return None
            if configured.is_file():
                image_path = configured
            else:
                candidates = list(image_dir.glob("*_stitched_sar.png")) or list(image_dir.glob("*.png"))
                if not candidates:
                    return None
                image_path = candidates[0]
            return Scan(folder_name, bbox, acquisition, str(image_path), metadata)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list(self) -> list[Scan]:
        scans: list[Scan] = []
        directories = sorted(
            (path for path in self.root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for directory in directories:
            scan = self.get(directory.name)
            if scan is not None:
                scans.append(scan)
        return scans

    def update_custom_name(self, folder_name: str, custom_name: str | None) -> None:
        scan = self.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan not found: {folder_name}")
        metadata = dict(scan.metadata)
        metadata["custom_name"] = custom_name
        self.save(Scan(scan.folder_name, scan.bbox, scan.acquisition, scan.image_path, metadata))

    def delete(self, folder_name: str) -> None:
        directory = self._directory(folder_name)
        if directory.exists():
            shutil.rmtree(directory)
