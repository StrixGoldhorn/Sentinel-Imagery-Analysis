"""
Comprehensive Unit Tests for Contact Uncertainty Calibration,
Spatial Error Ellipses, Dimensional Margins, Association Likelihood,
and Surveillance Reason Codes.
"""

import unittest
from datetime import datetime, timezone
import numpy as np

from sentinel_analysis.domain.uncertainty import (
    SpatialUncertainty,
    DimensionUncertainty,
    calibrate_spatial_uncertainty,
    calibrate_dimension_uncertainty,
    calculate_association_likelihood,
    generate_contact_reason_codes,
    calibrate_contact_uncertainty,
    REASON_RADAR_STRONG,
    REASON_RADAR_WEAK,
    REASON_RADAR_HIGH_ASPECT,
    REASON_WAKE_CONFIRMED,
    REASON_AIS_KINEMATIC_MATCH,
    REASON_AIS_BUFFER_ASSOCIATION,
    REASON_DARK_VESSEL,
    REASON_SOLAS_OFF,
    REASON_SPEED_SPOOFED,
    REASON_COURSE_SPOOFED,
    REASON_DIMENSION_CONSISTENT,
    REASON_DIMENSION_MISMATCH,
    REASON_OPTICAL_CONFIRMED,
    REASON_OPTICAL_FALSE_ALARM,
    REASON_PERSISTENT_STRUCT,
    REASON_NO_AIS_BROADCAST,
)
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection
from sentinel_analysis.infrastructure.detection.detection_saver import (
    _serialize_detection,
    _build_geojson_feature_collection,
)
from sentinel_analysis.application.use_cases.generate_briefing import extract_scan_intelligence


class TestUncertaintyDomainDataclasses(unittest.TestCase):
    """Test pure domain dataclasses SpatialUncertainty and DimensionUncertainty."""

    def test_spatial_uncertainty_valid(self):
        sp = SpatialUncertainty(
            cep_meters=14.5,
            semi_major_axis_meters=22.0,
            semi_minor_axis_meters=12.0,
            orientation_deg=45.0,
            confidence_level=0.95,
        )
        self.assertAlmostEqual(sp.cep_meters, 14.5)
        self.assertAlmostEqual(sp.semi_major_axis_meters, 22.0)
        self.assertAlmostEqual(sp.semi_minor_axis_meters, 12.0)
        self.assertAlmostEqual(sp.orientation_deg, 45.0)
        self.assertEqual(sp.confidence_level, 0.95)

        data = sp.to_dict()
        self.assertEqual(data["cep_meters"], 14.5)
        self.assertEqual(data["semi_major_axis_meters"], 22.0)

    def test_spatial_uncertainty_invalid(self):
        with self.assertRaises(ValueError):
            SpatialUncertainty(
                cep_meters=-5.0,
                semi_major_axis_meters=10.0,
                semi_minor_axis_meters=5.0,
                orientation_deg=0.0,
                confidence_level=0.95,
            )

        with self.assertRaises(ValueError):
            SpatialUncertainty(
                cep_meters=10.0,
                semi_major_axis_meters=10.0,
                semi_minor_axis_meters=5.0,
                orientation_deg=0.0,
                confidence_level=1.5,
            )

    def test_dimension_uncertainty_valid(self):
        dim = DimensionUncertainty(
            length_uncertainty_m=8.5,
            beam_uncertainty_m=3.2,
            heading_uncertainty_deg=12.0,
            aspect_ratio_confidence=0.88,
        )
        self.assertAlmostEqual(dim.length_uncertainty_m, 8.5)
        self.assertAlmostEqual(dim.beam_uncertainty_m, 3.2)
        self.assertAlmostEqual(dim.heading_uncertainty_deg, 12.0)
        self.assertAlmostEqual(dim.aspect_ratio_confidence, 0.88)

        data = dim.to_dict()
        self.assertEqual(data["length_uncertainty_m"], 8.5)
        self.assertEqual(data["beam_uncertainty_m"], 3.2)

    def test_dimension_uncertainty_invalid(self):
        with self.assertRaises(ValueError):
            DimensionUncertainty(
                length_uncertainty_m=-1.0,
                beam_uncertainty_m=2.0,
                heading_uncertainty_deg=5.0,
                aspect_ratio_confidence=0.5,
            )

        with self.assertRaises(ValueError):
            DimensionUncertainty(
                length_uncertainty_m=5.0,
                beam_uncertainty_m=2.0,
                heading_uncertainty_deg=200.0,  # > 180 degrees
                aspect_ratio_confidence=0.5,
            )


class TestCalibrationMathFunctions(unittest.TestCase):
    """Test uncertainty calibration algorithms and error ellipse estimation."""

    def test_calibrate_spatial_uncertainty_radar_only(self):
        sp = calibrate_spatial_uncertainty(
            pixel_spacing_m=10.0,
            radar_snr_db=15.0,
            confidence=0.90,
        )
        self.assertGreater(sp.cep_meters, 0.0)
        self.assertGreaterEqual(sp.semi_major_axis_meters, sp.semi_minor_axis_meters)
        self.assertEqual(sp.confidence_level, 0.95)

    def test_calibrate_spatial_uncertainty_with_ais(self):
        radar_only = calibrate_spatial_uncertainty(
            pixel_spacing_m=10.0,
            radar_snr_db=15.0,
            confidence=0.90,
        )
        with_ais = calibrate_spatial_uncertainty(
            pixel_spacing_m=10.0,
            radar_snr_db=15.0,
            confidence=0.90,
            ais_semi_major_m=25.0,
            ais_semi_minor_m=15.0,
            ais_orientation_deg=30.0,
        )
        # Combined uncertainty should be larger than radar sensor alone
        self.assertGreater(with_ais.semi_major_axis_meters, radar_only.semi_major_axis_meters)
        self.assertGreater(with_ais.cep_meters, radar_only.cep_meters)

    def test_calibrate_dimension_uncertainty_aspect_ratio_effect(self):
        # Elongated vessel (high aspect ratio) has lower heading uncertainty
        elongated = calibrate_dimension_uncertainty(
            length_m=120.0,
            beam_m=20.0,
            pixel_spacing_m=10.0,
            confidence=0.90,
            radar_snr_db=18.0,
        )
        # Square/round vessel (low aspect ratio) has high heading uncertainty
        blunt = calibrate_dimension_uncertainty(
            length_m=25.0,
            beam_m=22.0,
            pixel_spacing_m=10.0,
            confidence=0.90,
            radar_snr_db=18.0,
        )
        self.assertLess(elongated.heading_uncertainty_deg, blunt.heading_uncertainty_deg)
        self.assertGreater(elongated.aspect_ratio_confidence, blunt.aspect_ratio_confidence)

    def test_calculate_association_likelihood_perfect_match(self):
        lh = calculate_association_likelihood(
            spatial_distance_m=0.0,
            spatial_cep_m=15.0,
            speed_discrepancy_knots=0.0,
            course_discrepancy_deg=0.0,
            length_discrepancy_m=0.0,
            beam_discrepancy_m=0.0,
        )
        self.assertGreaterEqual(lh, 0.95)
        self.assertLessEqual(lh, 1.0)

    def test_calculate_association_likelihood_decay(self):
        lh_near = calculate_association_likelihood(
            spatial_distance_m=20.0,
            spatial_cep_m=15.0,
            speed_discrepancy_knots=1.0,
            course_discrepancy_deg=5.0,
        )
        lh_far = calculate_association_likelihood(
            spatial_distance_m=150.0,
            spatial_cep_m=15.0,
            speed_discrepancy_knots=8.0,
            course_discrepancy_deg=45.0,
        )
        self.assertGreater(lh_near, lh_far)
        self.assertGreater(lh_far, 0.0)


class TestReasonCodesGeneration(unittest.TestCase):
    """Test tactical reason code assignment across various surveillance conditions."""

    def test_strong_reflector_wake_codes(self):
        codes = generate_contact_reason_codes(
            radar_snr_db=18.0,
            aspect_ratio=4.5,
            has_wake=True,
            is_correlated=False,
            is_dark=True,
        )
        self.assertIn(REASON_RADAR_STRONG, codes)
        self.assertIn(REASON_RADAR_HIGH_ASPECT, codes)
        self.assertIn(REASON_WAKE_CONFIRMED, codes)
        self.assertIn(REASON_DARK_VESSEL, codes)
        self.assertIn(REASON_NO_AIS_BROADCAST, codes)

    def test_kinematic_match_codes(self):
        codes = generate_contact_reason_codes(
            is_correlated=True,
            is_inside_box=True,
            is_speed_spoofed=False,
            is_course_spoofed=False,
            dimension_match=True,
        )
        self.assertIn(REASON_AIS_KINEMATIC_MATCH, codes)
        self.assertIn(REASON_DIMENSION_CONSISTENT, codes)

    def test_spoofed_and_solas_breach_codes(self):
        codes = generate_contact_reason_codes(
            is_correlated=True,
            is_speed_spoofed=True,
            is_course_spoofed=True,
            dimension_match=False,
            is_dark=False,
        )
        self.assertIn(REASON_SPEED_SPOOFED, codes)
        self.assertIn(REASON_COURSE_SPOOFED, codes)
        self.assertIn(REASON_DIMENSION_MISMATCH, codes)

    def test_solas_carriage_off_code(self):
        codes = generate_contact_reason_codes(
            is_dark=True,
            solas_carriage_expected=True,
        )
        self.assertIn(REASON_SOLAS_OFF, codes)

    def test_optical_and_temporal_codes(self):
        codes_opt = generate_contact_reason_codes(
            optical_status="CONFIRMED_VESSEL",
            temporal_status="PERSISTENT",
        )
        self.assertIn(REASON_OPTICAL_CONFIRMED, codes_opt)
        self.assertIn(REASON_PERSISTENT_STRUCT, codes_opt)

        codes_fa = generate_contact_reason_codes(
            optical_status="LAND_FALSE_ALARM",
        )
        self.assertIn(REASON_OPTICAL_FALSE_ALARM, codes_fa)


class TestShipDetectionEntity(unittest.TestCase):
    """Test ShipDetection entity validation with calibrated uncertainty fields."""

    def test_entity_creation_with_uncertainty(self):
        det = ShipDetection(
            x=10,
            y=20,
            width=30,
            height=40,
            confidence=0.92,
            spatial_uncertainty={
                "cep_meters": 12.4,
                "semi_major_axis_meters": 18.0,
                "semi_minor_axis_meters": 10.0,
                "orientation_deg": 35.0,
                "confidence_level": 0.95,
            },
            dimension_uncertainty={
                "length_uncertainty_m": 7.0,
                "beam_uncertainty_m": 2.5,
                "heading_uncertainty_deg": 8.0,
                "aspect_ratio_confidence": 0.90,
            },
            association_likelihood=0.94,
            reason_codes=["AIS_KINEMATIC_MATCH", "DIMENSION_CONSISTENT"],
        )
        self.assertIsNotNone(det.spatial_uncertainty)
        self.assertEqual(det.spatial_uncertainty["cep_meters"], 12.4)
        self.assertEqual(det.association_likelihood, 0.94)
        self.assertIn("AIS_KINEMATIC_MATCH", det.reason_codes)

    def test_entity_rejects_invalid_likelihood(self):
        with self.assertRaises(ValueError):
            ShipDetection(
                x=10,
                y=20,
                width=30,
                height=40,
                confidence=0.90,
                association_likelihood=1.5,
            )

        with self.assertRaises(ValueError):
            ShipDetection(
                x=10,
                y=20,
                width=30,
                height=40,
                confidence=0.90,
                association_likelihood=-0.1,
            )


class TestSerializationAndBriefingExtraction(unittest.TestCase):
    """Test serialization of uncertainty in detection_saver and extract_scan_intelligence."""

    def test_serialize_detection_with_uncertainty(self):
        det = ShipDetection(
            x=10,
            y=20,
            width=30,
            height=40,
            confidence=0.88,
            length=75.0,
            beam=14.0,
            spatial_uncertainty={
                "cep_meters": 11.2,
                "semi_major_axis_meters": 16.0,
                "semi_minor_axis_meters": 9.0,
                "orientation_deg": 40.0,
                "confidence_level": 0.95,
            },
            dimension_uncertainty={
                "length_uncertainty_m": 6.5,
                "beam_uncertainty_m": 2.2,
                "heading_uncertainty_deg": 10.0,
                "aspect_ratio_confidence": 0.85,
            },
            association_likelihood=0.89,
            reason_codes=["RADAR_STRONG_REFLECTOR", "AIS_KINEMATIC_MATCH"],
        )
        record = _serialize_detection(det, index=1)
        self.assertIn("spatial_uncertainty", record)
        self.assertIn("dimension_uncertainty", record)
        self.assertIn("association_likelihood", record)
        self.assertIn("reason_codes", record)
        self.assertIn("cep_meters", record)
        self.assertEqual(record["spatial_uncertainty"]["cep_meters"], 11.2)
        self.assertEqual(record["cep_meters"], 11.2)
        self.assertEqual(record["association_likelihood"], 0.89)
        self.assertIn("RADAR_STRONG_REFLECTOR", record["reason_codes"])

    def test_geojson_feature_properties_contain_uncertainty(self):
        det = ShipDetection(
            x=10,
            y=20,
            width=30,
            height=40,
            confidence=0.85,
            spatial_uncertainty={"cep_meters": 13.0, "confidence_level": 0.95},
            dimension_uncertainty={"length_uncertainty_m": 8.0, "beam_uncertainty_m": 3.0},
            association_likelihood=0.82,
            reason_codes=["DARK_VESSEL_SUSPECT"],
        )
        serialized = _serialize_detection(det, index=1)
        geojson = _build_geojson_feature_collection([serialized])
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(len(geojson["features"]), 1)
        props = geojson["features"][0]["properties"]
        self.assertIn("spatial_uncertainty", props)
        self.assertIn("dimension_uncertainty", props)
        self.assertIn("association_likelihood", props)
        self.assertIn("reason_codes", props)
        self.assertIn("cep_meters", props)
        self.assertEqual(props["cep_meters"], 13.0)
        self.assertEqual(props["association_likelihood"], 0.82)
        self.assertIn("DARK_VESSEL_SUSPECT", props["reason_codes"])

    def test_extract_scan_intelligence_preserves_uncertainty(self):
        det = ShipDetection(
            x=10,
            y=20,
            width=30,
            height=40,
            confidence=0.91,
            length=90.0,
            beam=18.0,
            spatial_uncertainty={"cep_meters": 15.0, "semi_major_axis_meters": 20.0},
            dimension_uncertainty={"length_uncertainty_m": 7.0, "beam_uncertainty_m": 2.5},
            association_likelihood=0.92,
            reason_codes=["RADAR_STRONG_REFLECTOR", "DIMENSION_CONSISTENT"],
        )
        serialized_det = _serialize_detection(det, index=1)
        acq = Acquisition(
            acquired_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            satellite="Sentinel-1A",
            product_type="sentinel-1-grd",
            orbit_direction="ASCENDING",
            relative_orbit=123,
            polarizations=["VV", "VH"],
        )
        scan = Scan(
            folder_name="scan-test-uncertainty",
            bbox=BoundingBox(-122.5, 37.5, -122.0, 38.0),
            acquisition=acq,
            image_path="dummy_image.png",
            metadata={"detections": [serialized_det]},
        )
        intel = extract_scan_intelligence(scan)
        self.assertEqual(intel["total_vessels"], 1)
        d_intel = intel["detections"][0]
        self.assertIn("spatial_uncertainty", d_intel)
        self.assertIn("dimension_uncertainty", d_intel)
        self.assertIn("association_likelihood", d_intel)
        self.assertIn("reason_codes", d_intel)
        self.assertIn("cep_meters", d_intel)
        self.assertEqual(d_intel["cep_meters"], 15.0)
        self.assertEqual(d_intel["association_likelihood"], 0.92)


if __name__ == "__main__":
    unittest.main()
