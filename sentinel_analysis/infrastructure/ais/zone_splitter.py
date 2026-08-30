"""Spatial zone splitting and record deduplication utilities for AIS scraping."""

import math
from sentinel_analysis.domain.entities import AISRecord, BoundingBox

DEFAULT_ZONE_SIZE_NM = 10.0


def split_into_zones(
    bbox: BoundingBox,
    zone_size_nm: float = DEFAULT_ZONE_SIZE_NM,
) -> list[BoundingBox]:
    """Partition an area of interest (BoundingBox) into multiple grid zones.

    Certain AIS data sources (e.g. VesselFinder, AISFriends, aprs.fi) limit the number of
    returned vessels or truncate results when queried over large spatial bounding boxes.
    Partitioning into smaller sub-zones (e.g. 10 nautical miles) ensures comprehensive vessel
    coverage across large AOIs.
    """
    return bbox.split_into_zones(zone_size_nm=zone_size_nm)


def deduplicate_ais_records(records: list[AISRecord]) -> list[AISRecord]:
    """Deduplicate AIS records across overlapping or adjacent zones, keeping the latest timestamp."""
    seen: dict[str, AISRecord] = {}
    for record in records:
        mmsi = str(record.vessel.mmsi).strip()
        if not mmsi:
            continue
        if mmsi not in seen:
            seen[mmsi] = record
        else:
            existing = seen[mmsi]
            if record.position.timestamp >= existing.position.timestamp:
                seen[mmsi] = record
    return list(seen.values())
