"""HTTP serialization helpers."""

from pathlib import Path

from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import AreaOfInterest, Scan


def scan_image_url(scan: Scan, container: ApplicationContainer) -> str:
    relative = Path(scan.image_path).resolve().relative_to(container.settings.output_root)
    return "/static/output/" + relative.as_posix()


def serialize_aoi(aoi: AreaOfInterest) -> dict[str, object]:
    return {
        "id": aoi.id,
        "name": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "next_scan": aoi.next_scan.isoformat() if aoi.next_scan else None,
        "last_checked": aoi.last_checked.isoformat() if aoi.last_checked else None,
    }

