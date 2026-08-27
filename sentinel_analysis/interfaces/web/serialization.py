"""HTTP serialization helpers."""

from pathlib import Path

from flask import url_for

from sentinel_analysis.domain.entities import AreaOfInterest, Scan


def scan_image_url(scan: Scan, output_root: Path) -> str:
    try:
        relative = Path(scan.image_path).resolve().relative_to(output_root.resolve())
    except ValueError as exc:
        raise ValueError("Scan image is outside the configured output directory") from exc
    return url_for("scans.scan_media", filename=relative.as_posix())


def serialize_aoi(aoi: AreaOfInterest) -> dict[str, object]:
    return {
        "id": aoi.id,
        "name": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "next_scan": aoi.next_scan.isoformat() if aoi.next_scan else None,
        "last_checked": aoi.last_checked.isoformat() if aoi.last_checked else None,
    }
