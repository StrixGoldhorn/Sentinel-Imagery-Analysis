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
    budget = getattr(aoi, "collection_budget_usd", 0.0)
    spent = getattr(aoi, "budget_spent_usd", 0.0)
    return {
        "id": aoi.id,
        "name": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "next_scan": aoi.next_scan.isoformat() if aoi.next_scan else None,
        "last_checked": aoi.last_checked.isoformat() if aoi.last_checked else None,
        "auto_capture_enabled": getattr(aoi, "auto_capture_enabled", False),
        "priority": getattr(aoi, "priority", "MEDIUM"),
        "collection_budget_usd": budget,
        "budget_spent_usd": spent,
        "budget_remaining_usd": max(0.0, budget - spent) if budget > 0 else None,
        "desired_revisit_rate_hours": getattr(aoi, "desired_revisit_rate_hours", None),
        "retention_policy_days": getattr(aoi, "retention_policy_days", None),
    }

