"""Tests for Story 1.1 — slowapi rate limiting on auth endpoints."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from auth.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
)


@pytest.fixture()
def _rate_app():
    """Minimal FastAPI app with a rate-limited endpoint."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        rate_limit_exceeded_handler,
    )

    @app.post("/test-limited")
    @limiter.limit("2/minute")
    def limited(request: Request):
        return {"ok": True}

    @app.get("/test-unlimited")
    def unlimited():
        return {"ok": True}

    return app


@pytest.fixture()
def client(_rate_app):
    """Test client for the rate-limited app."""
    return TestClient(_rate_app)


def test_requests_within_limit_succeed(client):
    """First two requests should pass."""
    for _ in range(2):
        resp = client.post("/test-limited")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_rate_limit_returns_429(client):
    """Third request within 1 minute should be rejected."""
    for _ in range(2):
        client.post("/test-limited")
    resp = client.post("/test-limited")
    assert resp.status_code == 429
    assert "Too many requests" in resp.json()["detail"]


def test_unlimited_endpoint_unaffected(client):
    """Unlimited endpoint is not rate-limited."""
    for _ in range(10):
        resp = client.get("/test-unlimited")
        assert resp.status_code == 200
