"""Tests for Groq tier health monitoring (ASETPLTFRM-13).

Covers ``ObservabilityCollector`` health classification,
latency tracking, per-model cascade frequency, and the
``GET /admin/tier-health`` endpoint.
"""

import time

import pytest
from observability import ObservabilityCollector

# ── ObservabilityCollector tier health tests ──────────


class TestTierHealth:
    """Tier health classification and metrics."""

    @pytest.fixture()
    def collector(self):
        """Return a fresh collector."""
        return ObservabilityCollector()

    def test_health_endpoint_returns_all_tiers(
        self,
        collector,
    ):
        """get_tier_health lists all configured tiers."""
        tiers = [
            "model-a",
            "model-b",
            "model-c",
        ]
        result = collector.get_tier_health(tiers)
        assert len(result["tiers"]) == 3
        names = [t["model"] for t in result["tiers"]]
        assert names == tiers
        assert result["summary"]["total"] == 3

    def test_tier_marked_unhealthy_on_failure(
        self,
        collector,
    ):
        """Tier shows degraded/down after failures."""
        # Record multiple cascades (failures).
        for _ in range(5):
            collector.record_cascade(
                "model-a",
                "api_error",
                provider="groq",
            )
        result = collector.get_tier_health(["model-a"])
        tier = result["tiers"][0]
        assert tier["status"] == "down"
        assert tier["failures_5m"] >= 4

    def test_tier_degraded_on_few_failures(
        self,
        collector,
    ):
        """1–3 failures within window → degraded."""
        collector.record_cascade(
            "model-a",
            "api_error",
            provider="groq",
        )
        result = collector.get_tier_health(["model-a"])
        tier = result["tiers"][0]
        assert tier["status"] == "degraded"
        assert tier["failures_5m"] == 1

    def test_tier_healthy_no_failures(
        self,
        collector,
    ):
        """No failures → healthy."""
        collector.record_request(
            "model-a",
            latency_ms=50,
        )
        result = collector.get_tier_health(["model-a"])
        tier = result["tiers"][0]
        assert tier["status"] == "healthy"
        assert tier["failures_5m"] == 0

    def test_tier_latency_tracked(self, collector):
        """Invoke records latency in metrics."""
        for ms in [100, 200, 300, 400, 500]:
            collector.record_request(
                "model-a",
                latency_ms=ms,
            )
        result = collector.get_tier_health(["model-a"])
        lat = result["tiers"][0]["latency"]
        assert lat["avg_ms"] is not None
        assert lat["avg_ms"] == 300  # mean of 100–500
        assert lat["p95_ms"] is not None
        assert lat["p95_ms"] >= 400

    def test_cascade_frequency_counted(self, collector):
        """Cascade increments counter per tier."""
        collector.record_cascade(
            "model-a",
            "budget_exhausted",
        )
        collector.record_cascade(
            "model-a",
            "api_error",
        )
        collector.record_cascade(
            "model-b",
            "api_error",
        )
        result = collector.get_tier_health(
            ["model-a", "model-b"],
        )
        tier_a = [t for t in result["tiers"] if t["model"] == "model-a"][0]
        tier_b = [t for t in result["tiers"] if t["model"] == "model-b"][0]
        assert tier_a["cascade_count"] == 2
        assert tier_b["cascade_count"] == 1

    def test_disable_enable_tier(self, collector):
        """Disabled tier shows 'disabled' status."""
        collector.disable_tier("model-a")
        assert collector.is_tier_disabled("model-a")

        result = collector.get_tier_health(["model-a"])
        assert result["tiers"][0]["status"] == "disabled"
        assert result["summary"]["disabled"] == 1

        collector.enable_tier("model-a")
        assert not collector.is_tier_disabled("model-a")

        result = collector.get_tier_health(["model-a"])
        assert result["tiers"][0]["status"] == "healthy"

    def test_summary_counts(self, collector):
        """Summary has correct healthy/degraded/down."""
        # model-a: healthy (no failures)
        collector.record_request("model-a")
        # model-b: degraded (1 failure)
        collector.record_cascade(
            "model-b",
            "api_error",
        )
        # model-c: down (5 failures)
        for _ in range(5):
            collector.record_cascade(
                "model-c",
                "api_error",
            )

        result = collector.get_tier_health(
            ["model-a", "model-b", "model-c"],
        )
        s = result["summary"]
        assert s["healthy"] == 1
        assert s["degraded"] == 1
        assert s["down"] == 1
        assert s["total"] == 3

    def test_success_count_tracked(self, collector):
        """Successes in window are counted."""
        for _ in range(3):
            collector.record_request(
                "model-a",
                latency_ms=50,
            )
        result = collector.get_tier_health(["model-a"])
        assert result["tiers"][0]["successes_5m"] == 3

    def test_no_latency_returns_none(self, collector):
        """No latency data → avg_ms and p95_ms are None."""
        collector.record_request("model-a")
        result = collector.get_tier_health(["model-a"])
        lat = result["tiers"][0]["latency"]
        assert lat["avg_ms"] is None
        assert lat["p95_ms"] is None


# ── Admin tier-health endpoint tests ─────────────────


class TestAdminTierHealthEndpoint:
    """GET /admin/tier-health returns health data."""

    @pytest.fixture()
    def client(self, monkeypatch):
        """Create a test client with tier-health endpoint."""
        monkeypatch.setenv(
            "JWT_SECRET_KEY",
            "a" * 32,
        )

        from fastapi import APIRouter, Depends, FastAPI
        from fastapi.testclient import TestClient

        from auth.endpoints import create_auth_router

        obs = ObservabilityCollector()
        obs.record_request(
            "test-model",
            latency_ms=100,
        )
        obs.record_cascade(
            "test-model",
            "api_error",
        )

        app = FastAPI()
        app.include_router(create_auth_router())

        from auth.dependencies import superuser_only

        router = APIRouter()

        async def tier_health():
            return {
                "timestamp": time.time(),
                "health": obs.get_tier_health(
                    ["test-model"],
                ),
            }

        router.add_api_route(
            "/admin/tier-health",
            tier_health,
            methods=["GET"],
            dependencies=[Depends(superuser_only)],
        )
        app.include_router(router)
        return TestClient(app)

    def test_tier_health_requires_auth(self, client):
        """GET /admin/tier-health without token → 401."""
        r = client.get("/admin/tier-health")
        assert r.status_code == 401

    def test_tier_health_endpoint_exists(self, client):
        """GET /admin/tier-health is registered."""
        r = client.get("/admin/tier-health")
        assert r.status_code in (401, 403)
