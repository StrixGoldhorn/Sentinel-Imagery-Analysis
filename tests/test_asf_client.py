"""Unit tests for NASA Alaska Satellite Facility (ASF) DAAC SAR client."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.satellite.asf_client import ASFProduct, ASFSearchClient


class TestASFSearchClient(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(
            min_longitude=103.5,
            min_latitude=1.0,
            max_longitude=104.5,
            max_latitude=2.0,
        )

    def test_parse_asf_results(self):
        client = ASFSearchClient()
        mock_data = [
            [
                {
                    "granuleName": "S1A_IW_GRDH_1SDV_20261001T111520_20261001T111545_055912_06D8A2_A1B2",
                    "platform": "Sentinel-1A",
                    "processingLevel": "GRD_HD",
                    "beamModeType": "IW",
                    "polarization": "VV+VH",
                    "flightDirection": "DESCENDING",
                    "startTime": "2026-10-01T11:15:20.000Z",
                    "stopTime": "2026-10-01T11:15:45.000Z",
                    "orbit": 55912,
                    "pathNumber": 142,
                    "downloadUrl": "https://datapool.asf.alaska.edu/GRD_HD/SA/S1A_sample.zip",
                    "sizeMB": 941.5,
                    "centerLat": 1.5,
                    "centerLon": 104.0,
                }
            ]
        ]

        products = client.parse_results(mock_data)
        self.assertEqual(len(products), 1)

        p = products[0]
        self.assertIsInstance(p, ASFProduct)
        self.assertEqual(p.granule_name, "S1A_IW_GRDH_1SDV_20261001T111520_20261001T111545_055912_06D8A2_A1B2")
        self.assertEqual(p.platform, "Sentinel-1A")
        self.assertEqual(p.processing_level, "GRD_HD")
        self.assertEqual(p.beam_mode, "IW")
        self.assertEqual(p.polarization, "VV+VH")
        self.assertEqual(p.flight_direction, "DESCENDING")
        self.assertEqual(p.orbit, 55912)
        self.assertEqual(p.relative_orbit, 142)
        self.assertEqual(p.download_url, "https://datapool.asf.alaska.edu/GRD_HD/SA/S1A_sample.zip")
        self.assertAlmostEqual(p.size_mb, 941.5)
        self.assertAlmostEqual(p.center_lat, 1.5)
        self.assertAlmostEqual(p.center_lon, 104.0)

        # Verify to_dict serializability
        d = p.to_dict()
        self.assertEqual(d["granule_name"], p.granule_name)
        self.assertIn("T", d["start_time"])

    def test_search_formats_query_parameters(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_session.get.return_value = mock_resp

        client = ASFSearchClient(session=mock_session, earthdata_token="nasa-token")
        client.search(
            bbox=self.bbox,
            start_date=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
            end_date=datetime(2026, 10, 5, 23, 59, tzinfo=timezone.utc),
            platform="SENTINEL-1",
            processing_level="GRD_HD",
            flight_direction="ASCENDING",
        )

        mock_session.get.assert_called_once()
        args, kwargs = mock_session.get.call_args
        self.assertEqual(args[0], ASFSearchClient.API_URL)
        params = kwargs["params"]
        self.assertEqual(params["platform"], "SENTINEL-1")
        self.assertEqual(params["processingLevel"], "GRD_HD")
        self.assertEqual(params["flightDirection"], "ASCENDING")
        self.assertIn("103.5000,1.0000,104.5000,2.0000", params["bbox"])
        self.assertEqual(params["start"], "2026-10-01T00:00:00Z")

        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer nasa-token")

    def test_search_error_resilience(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = RuntimeError("Network timeout")

        client = ASFSearchClient(session=mock_session)
        results = client.search(self.bbox)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
