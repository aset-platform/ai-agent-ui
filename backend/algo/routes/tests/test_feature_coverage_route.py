"""Unit tests for GET /v1/admin/feature-coverage
(ASETPLTFRM-416 / FE-14).

Stubs the Iceberg scan with a synthetic pandas DataFrame so the
group-by + percentage math runs in pure-Python without touching
the real catalog. Auth boundary is exercised via FastAPI
``dependency_overrides``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth.dependencies import (
    get_current_user,
    superuser_only,
)
from auth.models import UserContext
from backend.algo.routes.feature_coverage import (
    _build_row_filter,
    create_feature_coverage_router,
)

SUPERUSER = UserContext(
    user_id="00000000-0000-0000-0000-000000000001",
    email="su@t.com",
    role="superuser",
)
PRO_USER = UserContext(
    user_id="00000000-0000-0000-0000-000000000002",
    email="pro@t.com",
    role="pro",
)
GENERAL_USER = UserContext(
    user_id="00000000-0000-0000-0000-000000000003",
    email="g@t.com",
    role="general",
)


def _build_app(user: UserContext) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_feature_coverage_router(),
        prefix="/v1",
    )
    app.dependency_overrides[superuser_only] = lambda: (
        user
        if user.role == "superuser"
        else (_ for _ in ()).throw(
            HTTPException(
                status_code=403,
                detail="Superuser role required",
            )
        )
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Per-test cache so cross-test bleed-through doesn't
    flip percentages.  The route's ``get_cache()`` returns a
    process-wide singleton — patch it with a fresh MagicMock
    for every test (test_route_caches_response builds its own
    inside the body).
    """
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.set.return_value = None
    with patch(
        "backend.algo.routes.feature_coverage.get_cache",
        return_value=fake_cache,
    ):
        yield fake_cache


def _fake_scan(df: pd.DataFrame) -> MagicMock:
    """Return a MagicMock that mimics
    ``catalog.load_table(...).refresh().scan(...).to_pandas()``.
    """
    cat = MagicMock()
    tbl = MagicMock()
    cat.load_table.return_value = tbl
    tbl.refresh.return_value = tbl
    scan = MagicMock()
    scan.to_pandas.return_value = df
    tbl.scan.return_value = scan
    return cat


def _seeded_df() -> pd.DataFrame:
    """Three features over two unique (ticker, ts) bar slots.

    Layout:
      bar A = (RELIANCE, 1) — has sma_20, rsi_14, vwap
      bar B = (RELIANCE, 2) — has sma_20, rsi_14
      bar C = (INFY, 1)    — has sma_20

    Unique bars = 3. Per-feature coverage:
      sma_20 = 3 / 3 = 100.0
      rsi_14 = 2 / 3 = 66.6666…
      vwap   = 1 / 3 = 33.3333…
    """
    rows = [
        ("RELIANCE", 1, "sma_20"),
        ("RELIANCE", 1, "rsi_14"),
        ("RELIANCE", 1, "vwap"),
        ("RELIANCE", 2, "sma_20"),
        ("RELIANCE", 2, "rsi_14"),
        ("INFY", 1, "sma_20"),
    ]
    return pd.DataFrame(
        rows,
        columns=["ticker", "bar_open_ts_ns", "feature_name"],
    )


def test_coverage_returns_per_feature_percent() -> None:
    """3 features, 3 unique bars — verify percentages."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(_seeded_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_unique_bars"] == 3
    assert body["rows_total"] == 6
    assert body["tickers_total"] == 2
    by_name = {row["feature_name"]: row for row in body["coverage"]}
    assert by_name["sma_20"]["coverage_pct"] == pytest.approx(100.0)
    assert by_name["rsi_14"]["coverage_pct"] == pytest.approx(
        66.6667,
        rel=1e-3,
    )
    assert by_name["vwap"]["coverage_pct"] == pytest.approx(
        33.3333,
        rel=1e-3,
    )
    assert by_name["sma_20"]["rows"] == 3
    assert by_name["sma_20"]["tickers_seen"] == 2
    assert by_name["vwap"]["tickers_seen"] == 1


def test_partition_prune_uses_year_month() -> None:
    """The pyiceberg row_filter we send to scan() must:
      - bracket bar_date with >= / <= literals,
      - pin interval_sec + feature_set_version.
    PyIceberg uses bar_date as the partition key (year_month
    is derived from it).
    """
    f = _build_row_filter(
        interval_sec=900,
        period_start=pd.Timestamp("2026-05-01").date(),
        period_end=pd.Timestamp("2026-05-13").date(),
        feature_set_version="v1.0",
    )
    s = repr(f)
    assert "bar_date" in s
    assert "2026-05-01" in s
    assert "2026-05-13" in s
    assert "interval_sec" in s
    assert "900" in s
    assert "feature_set_version" in s
    assert "v1.0" in s


def test_handles_empty_window() -> None:
    """Scan returns empty df → empty coverage list, zeros."""
    app = _build_app(SUPERUSER)
    empty_df = pd.DataFrame(
        columns=["ticker", "bar_open_ts_ns", "feature_name"],
    )
    fake_cat = _fake_scan(empty_df)
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_unique_bars"] == 0
    assert body["rows_total"] == 0
    assert body["tickers_total"] == 0
    assert body["coverage"] == []


def test_route_requires_superuser() -> None:
    """General → 403, pro → 403, superuser → 200."""
    for blocked in (GENERAL_USER, PRO_USER):
        app = _build_app(blocked)
        fake_cat = _fake_scan(_seeded_df())
        with patch(
            "stocks.create_tables._get_catalog",
            return_value=fake_cat,
        ):
            client = TestClient(app)
            r = client.get(
                "/v1/admin/feature-coverage"
                "?period_start=2026-05-01&period_end=2026-05-13",
            )
        assert r.status_code == 403, f"{blocked.role} should be 403"

    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(_seeded_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13",
        )
    assert r.status_code == 200


def test_route_caches_response() -> None:
    """Second call hits the in-memory cache, not Iceberg."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(_seeded_df())

    fake_cache_store: dict[str, Any] = {}
    fake_cache = MagicMock()
    fake_cache.get.side_effect = lambda k: fake_cache_store.get(k)

    def _set(k: str, v: str, ttl: int | None = None) -> None:
        fake_cache_store[k] = v

    fake_cache.set.side_effect = _set
    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=fake_cat,
        ),
        patch(
            "backend.algo.routes.feature_coverage.get_cache",
            return_value=fake_cache,
        ),
    ):
        client = TestClient(app)
        url = (
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13"
        )
        r1 = client.get(url)
        r2 = client.get(url)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # First call computes + sets; second call MUST short-circuit
    # at cache.get without reloading the Iceberg table.
    assert fake_cat.load_table.call_count == 1
    assert fake_cache.set.call_count == 1


def test_interval_sec_default_900() -> None:
    """Omitted interval_sec → 900 (15 min)."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(_seeded_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interval_sec"] == 900


def test_feature_set_version_filter() -> None:
    """Explicit ?feature_set_version=v2.0 pins the scan."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(_seeded_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13"
            "&feature_set_version=v2.0",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feature_set_version"] == "v2.0"


def test_response_sorted_by_coverage_desc() -> None:
    """Highest coverage features come first."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(_seeded_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13",
        )
    body = r.json()
    pcts = [row["coverage_pct"] for row in body["coverage"]]
    assert pcts == sorted(pcts, reverse=True)


def test_coverage_pct_never_exceeds_100() -> None:
    """Even on a degenerate input, pct stays bounded."""
    # Force every row to share the SAME (ticker, ts) — total
    # unique bars = 1, but one feature has 3 rows. The route
    # caps coverage at 100 % defensively.
    df = pd.DataFrame(
        [
            ("R", 1, "f1"),
            ("R", 1, "f1"),
            ("R", 1, "f1"),
            ("R", 1, "f2"),
        ],
        columns=["ticker", "bar_open_ts_ns", "feature_name"],
    )
    app = _build_app(SUPERUSER)
    fake_cat = _fake_scan(df)
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/feature-coverage"
            "?period_start=2026-05-01&period_end=2026-05-13",
        )
    body = r.json()
    for row in body["coverage"]:
        assert row["coverage_pct"] <= 100.0


def test_invalid_window_400() -> None:
    """period_end < period_start → 400."""
    app = _build_app(SUPERUSER)
    client = TestClient(app)
    r = client.get(
        "/v1/admin/feature-coverage"
        "?period_start=2026-05-13&period_end=2026-05-01",
    )
    assert r.status_code == 400
