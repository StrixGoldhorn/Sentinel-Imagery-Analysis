"""HTTP serialization helpers."""

from pathlib import Path

from sentinel_analysis.domain.entities import AreaOfInterest, Scan


def scan_image_url(scan: Scan, output_root: Path) -> str:
    try:
        relative = Path(scan.image_path).resolve().relative_to(output_root.resolve())
    except ValueError as exc:
        raise ValueError("Scan image is outside the configured output directory") from exc
    return f"/media/scans/{relative.as_posix()}"



def serialize_aoi(aoi: AreaOfInterest) -> dict[str, object]:
    return {
        "id": aoi.id,
        "name": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "next_scan": aoi.next_scan.isoformat() if aoi.next_scan else None,
        "last_checked": aoi.last_checked.isoformat() if aoi.last_checked else None,
        "auto_capture_enabled": getattr(aoi, "auto_capture_enabled", False),
    }

