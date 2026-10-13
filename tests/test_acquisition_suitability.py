"""Unit tests for satellite acquisition suitability scoring and provider comparison catalog."""

import unittest

from sentinel_analysis.domain.acquisition_suitability import (
    MetoceanCondition,
    ModalityType,
    OpportunityTier,
    SuitabilityAssessment,
    calculate_cloud_score,
    calculate_coverage_score,
    calculate_incidence_angle_score,
    calculate_orbit_direction_score,
    calculate_resolution_score,
    calculate_wave_score,
    calculate_wind_score,
    score_acquisition_opportunity,
)
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.provider_catalog import (
    ProviderCatalog,
    ProviderProfile,
)


class TestAcquisitionSuitabilityDomain(unittest.TestCase):
    """Test individual physical factor calculations and composite scoring."""

    def test_metocean_condition(self) -> None:
        cond = MetoceanCondition(
            wind_speed_mps=10.0,
            wind_direction_deg=180.0,
            wave_height_m=2.0,
            cloud_cover_pct=30.0,
        )
        self.assertAlmostEqual(cond.wind_speed_knots, 19.4384, delta=0.01)
        d = cond.to_dict()
        self.assertEqual(d["wind_speed_mps"], 10.0)
        self.assertEqual(d["wave_height_m"], 2.0)
        self.assertEqual(d["cloud_cover_pct"], 30.0)

    def test_resolution_score(self) -> None:
        # Sub-meter spotlight (Umbra, Capella)
        self.assertEqual(calculate_resolution_score(0.5, ModalityType.SAR), 100.0)
        self.assertEqual(calculate_resolution_score(1.0, ModalityType.SAR), 95.0)

        # Standard 10m (Sentinel-1 IW, Sentinel-2)
        score_10m = calculate_resolution_score(10.0, ModalityType.SAR)
        self.assertAlmostEqual(score_10m, 80.0, delta=0.1)

        # Coarse 25m (Sentinel-1 EW)
        score_25m = calculate_resolution_score(25.0, ModalityType.SAR)
        self.assertAlmostEqual(score_25m, 60.0, delta=0.1)

    def test_coverage_score(self) -> None:
        self.assertEqual(calculate_coverage_score(100.0), 100.0)
        self.assertEqual(calculate_coverage_score(95.0), 100.0)
        self.assertTrue(80.0 < calculate_coverage_score(75.0) < 90.0)
        self.assertTrue(60.0 < calculate_coverage_score(50.0) < 70.0)
        self.assertTrue(calculate_coverage_score(10.0) < 30.0)

    def test_incidence_angle_score_sar(self) -> None:
        # Optimal maritime range (31-41 deg)
        self.assertEqual(calculate_incidence_angle_score(35.0, ModalityType.SAR), 100.0)
        self.assertEqual(calculate_incidence_angle_score(32.0, ModalityType.SAR), 100.0)

        # Steep incidence (high clutter floor)
        steep_score = calculate_incidence_angle_score(22.0, ModalityType.SAR)
        self.assertTrue(steep_score < 70.0)

        # Shallow incidence (> 45 deg)
        shallow_score = calculate_incidence_angle_score(50.0, ModalityType.SAR)
        self.assertTrue(shallow_score < 70.0)

    def test_wind_score_sar(self) -> None:
        # Ideal 5.0 m/s
        self.assertEqual(calculate_wind_score(5.0, ModalityType.SAR), 100.0)

        # Dead calm (< 2.0 m/s) with slick ambiguity
        calm_score = calculate_wind_score(1.0, ModalityType.SAR)
        self.assertTrue(calm_score < 75.0)

        # High winds (15 m/s) with heavy whitecaps
        high_wind_score = calculate_wind_score(15.0, ModalityType.SAR)
        self.assertTrue(high_wind_score < 60.0)

    def test_wave_score(self) -> None:
        # Calm sea (Hs <= 1.2 m)
        self.assertEqual(calculate_wave_score(1.0, ModalityType.SAR), 100.0)

        # Rough sea (Hs = 3.0 m)
        rough_score = calculate_wave_score(3.0, ModalityType.SAR)
        self.assertTrue(65.0 < rough_score < 80.0)

        # Heavy storm swell (Hs = 6.0 m)
        storm_score = calculate_wave_score(6.0, ModalityType.SAR)
        self.assertTrue(storm_score <= 25.0)

    def test_cloud_score_optical_vs_sar(self) -> None:
        # SAR is immune to clouds (100.0)
        self.assertEqual(calculate_cloud_score(90.0, ModalityType.SAR), 100.0)
        self.assertEqual(calculate_cloud_score(100.0, ModalityType.SAR, is_daylight=False), 100.0)

        # Optical is highly cloud sensitive
        clear_opt = calculate_cloud_score(5.0, ModalityType.OPTICAL, is_daylight=True)
        self.assertTrue(clear_opt > 90.0)

        cloudy_opt = calculate_cloud_score(75.0, ModalityType.OPTICAL, is_daylight=True)
        self.assertTrue(cloudy_opt < 30.0)

        # Optical at night is 0.0
        night_opt = calculate_cloud_score(0.0, ModalityType.OPTICAL, is_daylight=False)
        self.assertEqual(night_opt, 0.0)

    def test_score_acquisition_opportunity_composite(self) -> None:
        metocean = MetoceanCondition(
            wind_speed_mps=6.0,
            wave_height_m=1.1,
            cloud_cover_pct=80.0,  # 80% clouds!
        )

        # 1. Sentinel-1 SAR under 80% clouds: Should score OPTIMAL or EXCELLENT
        s1_assessment = score_acquisition_opportunity(
            resolution_m=10.0,
            aoi_coverage_pct=100.0,
            incidence_angle_deg=36.0,
            orbit_direction="DESCENDING",
            modality=ModalityType.SAR,
            metocean=metocean,
            is_daylight=True,
        )
        self.assertIsInstance(s1_assessment, SuitabilityAssessment)
        self.assertTrue(s1_assessment.composite_score >= 85.0)
        self.assertIn(s1_assessment.grade, ("OPTIMAL", "EXCELLENT"))
        self.assertTrue(any("cloud penetration" in s.lower() for s in s1_assessment.strengths))

        # 2. Sentinel-2 Optical under 80% clouds: Should score POOR or MARGINAL
        s2_assessment = score_acquisition_opportunity(
            resolution_m=10.0,
            aoi_coverage_pct=100.0,
            incidence_angle_deg=10.0,
            orbit_direction="DESCENDING",
            modality=ModalityType.OPTICAL,
            metocean=metocean,
            is_daylight=True,
        )
        self.assertTrue(s2_assessment.composite_score < 55.0)
        self.assertIn(s2_assessment.grade, ("MARGINAL", "POOR"))
        self.assertTrue(any("cloud cover" in l.lower() for l in s2_assessment.limitations))


class TestProviderCatalog(unittest.TestCase):
    """Test multi-provider catalog and side-by-side comparative analysis."""

    def test_provider_listing(self) -> None:
        providers = ProviderCatalog.list_all()
        self.assertGreaterEqual(len(providers), 6)

        provider_ids = {p.provider_id for p in providers}
        expected_ids = {"sentinel-1", "sentinel-2", "umbra", "capella", "iceye", "planetscope"}
        self.assertTrue(expected_ids.issubset(provider_ids))

        s1 = ProviderCatalog.get_provider("sentinel-1")
        self.assertIsNotNone(s1)
        self.assertEqual(s1.name, "Sentinel-1 C-SAR")
        self.assertFalse(s1.is_commercial)
        self.assertEqual(s1.cost_tier, "FREE_AND_OPEN")

        umbra = ProviderCatalog.get_provider("umbra")
        self.assertIsNotNone(umbra)
        self.assertEqual(umbra.modality, ModalityType.SAR)
        self.assertTrue(umbra.is_commercial)
        self.assertEqual(umbra.high_resolution_m, 0.25)

    def test_compare_providers(self) -> None:
        # Overcast and high swell conditions
        metocean = MetoceanCondition(
            wind_speed_mps=7.0,
            wave_height_m=1.5,
            cloud_cover_pct=70.0,
        )
        aoi = BoundingBox(103.5, 1.1, 104.2, 1.6)
        comparisons = ProviderCatalog.compare_providers(
            metocean=metocean,
            aoi_bbox=aoi,
            is_daylight=True,
        )

        self.assertEqual(len(comparisons), 6)
        # First entry should have higher score than last
        self.assertGreaterEqual(
            comparisons[0].estimated_suitability_score,
            comparisons[-1].estimated_suitability_score,
        )

        # Under 70% clouds, SAR providers (Sentinel-1, Umbra) should outscore optical
        s1_entry = next(c for c in comparisons if c.provider.provider_id == "sentinel-1")
        s2_entry = next(c for c in comparisons if c.provider.provider_id == "sentinel-2")
        self.assertGreater(s1_entry.estimated_suitability_score, s2_entry.estimated_suitability_score)

        # Verify Umbra has high dark vessel detection fidelity
        umbra_entry = next(c for c in comparisons if c.provider.provider_id == "umbra")
        self.assertIn("High", umbra_entry.dark_vessel_detection_fidelity)


if __name__ == "__main__":
    unittest.main()
