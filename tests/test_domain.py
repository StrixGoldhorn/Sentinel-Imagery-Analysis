"""Unit tests for domain invariants and normalization."""

import unittest
from datetime import datetime, timedelta, timezone

from sentinel_analysis.domain.entities import (
    AISRecord,
    Acquisition,
    AreaOfInterest,
    BoundingBox,
    ImageTile,
    ShipDetection,
    Vessel,
    VesselPosition,
)
from sentinel_analysis.domain.exceptions import DomainValidationError, SentinelAnalysisError


def test_bounding_box_normalizes_numbers_and_exposes_latitude_longitude_center() -> None:
    bbox = BoundingBox.from_sequence(["103", "1", "104.5", "2.5"])

    assert bbox.as_list() == [103.0, 1.0, 104.5, 2.5]
    assert bbox.center == (1.75, 103.75)


def test_bounding_box_rejects_non_finite_and_inverted_coordinates() -> None:
    try:
        BoundingBox(float("nan"), 1, 104, 2)
        assert False, "Expected DomainValidationError"
    except DomainValidationError as e:
        assert "finite" in str(e)

    try:
        BoundingBox.from_sequence([104, 1, 103, 2])
        assert False, "Expected DomainValidationError"
    except DomainValidationError as e:
        assert "Minimum longitude" in str(e)


def test_acquisition_and_aoi_times_are_normalized_to_utc() -> None:
    offset = timezone(timedelta(hours=8))
    acquisition = Acquisition(datetime(2026, 8, 27, 12, tzinfo=offset), " Sentinel-1 ", " SAR ")
    aoi = AreaOfInterest(" Singapore Strait ", BoundingBox(103, 1, 104, 2), next_scan=datetime(2026, 8, 27))

    assert acquisition.acquired_at == datetime(2026, 8, 27, 4, tzinfo=timezone.utc)
    assert acquisition.satellite == "Sentinel-1"
    assert aoi.next_scan.tzinfo == timezone.utc
    assert aoi.name == "Singapore Strait"


def test_tile_and_detection_dimensions_must_be_valid() -> None:
    bbox = BoundingBox(103, 1, 104, 2)

    try:
        ImageTile(bbox, 0, 256, 0, 0)
        assert False, "Expected DomainValidationError"
    except DomainValidationError:
        pass

    try:
        ShipDetection(0, 0, 20, 10, 1.1)
        assert False, "Expected DomainValidationError"
    except DomainValidationError:
        pass


def test_vessel_position_validates_navigation_values() -> None:
    try:
        VesselPosition("123456789", 91, 103, datetime.now(timezone.utc))
        assert False, "Expected DomainValidationError"
    except DomainValidationError as e:
        assert "Latitude" in str(e)

    try:
        VesselPosition("123456789", 1, 103, datetime.now(timezone.utc), speed=-1)
        assert False, "Expected DomainValidationError"
    except DomainValidationError as e:
        assert "Speed" in str(e)


def test_ais_record_requires_matching_mmsi() -> None:
    vessel = Vessel("1234567", "123456789")
    position = VesselPosition("987654321", 1, 103, datetime.now(timezone.utc))

    try:
        AISRecord(vessel, position)
        assert False, "Expected DomainValidationError"
    except DomainValidationError as e:
        assert "must match" in str(e)


def test_ship_detection_supports_obb_and_metrology() -> None:
    ship = ShipDetection(
        x=10,
        y=20,
        width=30,
        height=40,
        confidence=0.92,
        angle=45.0,
        length=120.5,
        beam=25.0,
        center_x=25.0,
        center_y=40.0,
        polygon_points=((10, 20), (30, 20), (30, 40), (10, 40)),
    )
    assert ship.angle == 45.0
    assert ship.length == 120.5
    assert ship.beam == 25.0
    assert len(ship.polygon_points) == 4


def test_background_task_lifecycle_and_defaults() -> None:
    from sentinel_analysis.domain.entities import BackgroundTask
    task = BackgroundTask(task_id="t1", task_type="scan")
    assert task.status == "PENDING"
    assert task.progress == 0.0
    assert task.error is None


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()


