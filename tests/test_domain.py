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


class DomainEntityTests(unittest.TestCase):
    def test_bounding_box_normalizes_numbers_and_exposes_latitude_longitude_center(self) -> None:
        bbox = BoundingBox.from_sequence(["103", "1", "104.5", "2.5"])

        self.assertEqual(bbox.as_list(), [103.0, 1.0, 104.5, 2.5])
        self.assertEqual(bbox.center, (1.75, 103.75))

    def test_bounding_box_rejects_non_finite_and_inverted_coordinates(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "finite"):
            BoundingBox(float("nan"), 1, 104, 2)
        with self.assertRaisesRegex(DomainValidationError, "Minimum longitude"):
            BoundingBox.from_sequence([104, 1, 103, 2])

    def test_acquisition_and_aoi_times_are_normalized_to_utc(self) -> None:
        offset = timezone(timedelta(hours=8))
        acquisition = Acquisition(datetime(2026, 8, 27, 12, tzinfo=offset), " Sentinel-1 ", " SAR ")
        aoi = AreaOfInterest(" Singapore Strait ", BoundingBox(103, 1, 104, 2), next_scan=datetime(2026, 8, 27))

        self.assertEqual(acquisition.acquired_at, datetime(2026, 8, 27, 4, tzinfo=timezone.utc))
        self.assertEqual(acquisition.satellite, "Sentinel-1")
        self.assertEqual(aoi.next_scan.tzinfo, timezone.utc)
        self.assertEqual(aoi.name, "Singapore Strait")

    def test_tile_and_detection_dimensions_must_be_valid(self) -> None:
        bbox = BoundingBox(103, 1, 104, 2)

        with self.assertRaises(DomainValidationError):
            ImageTile(bbox, 0, 256, 0, 0)
        with self.assertRaises(DomainValidationError):
            ShipDetection(0, 0, 20, 10, 1.1)

    def test_vessel_position_validates_navigation_values(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "Latitude"):
            VesselPosition("123456789", 91, 103, datetime.now(timezone.utc))
        with self.assertRaisesRegex(DomainValidationError, "Speed"):
            VesselPosition("123456789", 1, 103, datetime.now(timezone.utc), speed=-1)

    def test_ais_record_requires_matching_mmsi(self) -> None:
        vessel = Vessel("1234567", "123456789")
        position = VesselPosition("987654321", 1, 103, datetime.now(timezone.utc))

        with self.assertRaisesRegex(DomainValidationError, "must match"):
            AISRecord(vessel, position)

    def test_domain_validation_error_supports_both_error_contracts(self) -> None:
        error = DomainValidationError("invalid")

        self.assertIsInstance(error, ValueError)
        self.assertIsInstance(error, SentinelAnalysisError)


if __name__ == "__main__":
    unittest.main()
