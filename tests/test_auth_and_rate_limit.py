"""Unit and integration tests for API key authentication and request rate limiting."""

from pathlib import Path
import tempfile
import unittest

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.interfaces.web.application import create_app
from sentinel_analysis.interfaces.web.security import (
    SlidingWindowRateLimiter,
    rate_limiter,
    validate_api_key,
)


class TestSecurityUnit(unittest.TestCase):
    def test_timing_safe_validation(self):
        self.assertTrue(validate_api_key(None, None))
        self.assertTrue(validate_api_key("any", None))
        self.assertFalse(validate_api_key(None, "expected"))
        self.assertFalse(validate_api_key("", "expected"))
        self.assertFalse(validate_api_key("wrong", "expected"))
        self.assertTrue(validate_api_key("correct-key-xyz", "correct-key-xyz"))

    def test_sliding_window_limiter(self):
        limiter = SlidingWindowRateLimiter()
        # Allow 3 requests in 60s
        for _ in range(3):
            allowed, remaining, retry = limiter.check("client1", limit=3, window_seconds=60)
            self.assertTrue(allowed)
            self.assertEqual(retry, 0)

        # 4th request should be blocked
        allowed, remaining, retry = limiter.check("client1", limit=3, window_seconds=60)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)
        self.assertGreater(retry, 0)

        # Different client should still be allowed
        allowed, remaining, retry = limiter.check("client2", limit=3, window_seconds=60)
        self.assertTrue(allowed)

        # After reset, client1 is allowed again
        limiter.reset("client1")
        allowed, remaining, retry = limiter.check("client1", limit=3, window_seconds=60)
        self.assertTrue(allowed)


class TestAuthAndRateLimitingIntegration(unittest.TestCase):
    def setUp(self):
        rate_limiter.reset()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(__file__).resolve().parent.parent

    def tearDown(self):
        rate_limiter.reset()
        if hasattr(self, "container") and self.container is not None:
            try:
                self.container.shutdown(timeout=0.5)
            except Exception:
                pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_app(self, api_key=None, rate_limit=120, rate_limiting_enabled=True):
        settings = Settings(
            copernicus_username="test",
            copernicus_password="pwd",
            n2yo_api_key="test_n2yo",
            project_root=self.project_root,
            output_root=f"{self.temp_dir.name}/output",
            cache_root=f"{self.temp_dir.name}/cache",
            database_path=f"{self.temp_dir.name}/test.db",
            api_key=api_key,
            rate_limit_per_minute=rate_limit,
            rate_limiting_enabled=rate_limiting_enabled,
            debug=False,
        )
        self.container = ApplicationContainer(settings)
        app = create_app(settings, self.container, start_background_workers=False)
        return app.test_client()

    def test_public_probes_bypass_auth(self):
        client = self._create_app(api_key="secret-token-xyz")
        res = client.get("/healthz")
        self.assertEqual(res.status_code, 200)

        res_ready = client.get("/readyz")
        self.assertEqual(res_ready.status_code, 200)

    def test_api_key_required_when_configured(self):
        client = self._create_app(api_key="secret-token-xyz")
        # 1. No key -> 401
        res = client.get("/api/aoi")
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertEqual(data["error"], "Unauthorized")

        # 2. Invalid key -> 401
        res_bad = client.get("/api/aoi", headers={"X-API-Key": "invalid-token"})
        self.assertEqual(res_bad.status_code, 401)

        # 3. Header X-API-Key -> 200
        res_x = client.get("/api/aoi", headers={"X-API-Key": "secret-token-xyz"})
        self.assertEqual(res_x.status_code, 200)

        # 4. Authorization Bearer -> 200
        res_bearer = client.get(
            "/api/aoi",
            headers={"Authorization": "Bearer secret-token-xyz"},
        )
        self.assertEqual(res_bearer.status_code, 200)

        # 5. Query param -> 200
        res_query = client.get("/api/aoi?api_key=secret-token-xyz")
        self.assertEqual(res_query.status_code, 200)

    def test_permissive_when_no_api_key(self):
        client = self._create_app(api_key=None)
        res = client.get("/api/aoi")
        self.assertEqual(res.status_code, 200)

    def test_rate_limiting_enforcement(self):
        client = self._create_app(rate_limit=3, rate_limiting_enabled=True)

        for _ in range(3):
            res = client.get("/api/aoi")
            self.assertEqual(res.status_code, 200)

        # 4th request from same IP should be blocked
        res_blocked = client.get("/api/aoi")
        self.assertEqual(res_blocked.status_code, 429)
        data = res_blocked.get_json()
        self.assertEqual(data["error"], "Too Many Requests")
        self.assertIn("Retry-After", res_blocked.headers)
        self.assertIn("X-RateLimit-Limit", res_blocked.headers)


if __name__ == "__main__":
    unittest.main()
