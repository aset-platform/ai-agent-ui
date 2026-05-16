"""Unit tests for GET /v1/admin/universe-snapshot
(ASETPLTFRM-423).

Mirrors the test pattern of
``test_feature_coverage_route.py``: stub the Iceberg scan with a
synthetic pandas DataFrame so aggregation runs in pure-Python
without touching the real catalog. Auth boundary exercised via
FastAPI ``dependency_overrides``.
"""

from __future__ import annotations

from datetime import date
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
from backend.algo.routes.universe_snapshot import (
    create_universe_snapshot_router,
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
        create_universe_snapshot_router(),
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
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.set.return_value = None
    with patch(
        "backend.algo.routes.universe_snapshot.get_cache",
        return_value=fake_cache,
    ):
        yield fake_cache


def _fake_catalog_for(df: pd.DataFrame) -> MagicMock:
    """Returns a catalog mock whose ``load_table(...).refresh().
    scan(...).to_pandas()`` chain yields ``df``.
    """
    cat = MagicMock()
    tbl = MagicMock()
    cat.load_table.return_value = tbl
    tbl.refresh.return_value = tbl
    scan = MagicMock()
    scan.to_pandas.return_value = df
    tbl.scan.return_value = scan
    return cat


def _two_rebalance_df() -> pd.DataFrame:
    """Synthetic snapshot covering two rebalance dates.

    2026-05-01: 3 tickers (2 top-200), 2 sectors
    2026-05-15: 3 tickers (2 top-200, different cohort), 2 sectors
    """
    rows = [
        # rebalance_date, ticker, adtv, mcap, sector, top200, bucket, t100mcap
        (date(2026, 5, 1), "AAA", 1e8, 5e10, "Technology", True, "A", True),
        (date(2026, 5, 1), "BBB", 5e7, 4e10, "Technology", True, "B", False),
        (date(2026, 5, 1), "CCC", 1e7, 1e10, "Financial Services", False, "C", False),
        (date(2026, 5, 15), "AAA", 1.2e8, 6e10, "Technology", True, "A", True),
        (date(2026, 5, 15), "DDD", 9e7, 5e10, "Healthcare", True, "B", False),
        (date(2026, 5, 15), "BBB", 4e7, 4e10, "Technology", False, "B", False),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "rebalance_date",
            "ticker",
            "adtv_inr_60d",
            "market_cap_inr",
            "sector",
            "included_in_top_200",
            "liquidity_bucket",
            "is_top100_mcap",
        ],
    )


def test_list_rebalances_returns_distinct_dates_desc() -> None:
    app = _build_app(SUPERUSER)
    fake_cat = _fake_catalog_for(_two_rebalance_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get("/v1/admin/universe-snapshot/rebalances")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rebalances"] == ["2026-05-15", "2026-05-01"]


def test_snapshot_defaults_to_latest_rebalance() -> None:
    """No ``rebalance_date`` query → returns the newest rebalance."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_catalog_for(_two_rebalance_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get("/v1/admin/universe-snapshot")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rebalance_date"] == "2026-05-15"
    # On the latest rebalance the DataFrame has 3 rows (filtered by
    # the EqualTo expression — but our stub returns the full df, so
    # we just verify the response shape + summary math works).
    assert body["total_rows"] >= 1
    assert isinstance(body["sectors"], list)
    assert isinstance(body["buckets"], list)


def test_snapshot_summary_aggregates() -> None:
    """Sector + bucket aggregates and avg_adtv reflect the data."""
    app = _build_app(SUPERUSER)
    # Stub the scan to only return the requested rebalance — the
    # real Iceberg row_filter does this, but our MagicMock chain
    # returns the same DataFrame regardless of filter args. Fake
    # that by handing back ONLY the 2026-05-15 slice.
    df_latest = _two_rebalance_df()
    df_latest = df_latest[
        df_latest["rebalance_date"] == date(2026, 5, 15)
    ]
    fake_cat = _fake_catalog_for(df_latest)
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/universe-snapshot?rebalance_date=2026-05-15",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_rows"] == 3
    assert body["top200_count"] == 2
    # Sector aggregates sum to the row count
    sector_counts = {s["sector"]: s["count"] for s in body["sectors"]}
    assert sector_counts.get("Technology") == 2
    assert sector_counts.get("Healthcare") == 1
    # Bucket aggregates
    bucket_counts = {b["bucket"]: b["count"] for b in body["buckets"]}
    assert bucket_counts.get("A") == 1
    assert bucket_counts.get("B") == 2
    # Each row carries ticker + bucket + is_top100_mcap
    tickers = {r["ticker"] for r in body["rows"]}
    assert tickers == {"AAA", "DDD", "BBB"}


def test_snapshot_empty_iceberg_returns_zeros() -> None:
    """Iceberg scan returns empty df → 200 with zero totals."""
    app = _build_app(SUPERUSER)
    empty_df = pd.DataFrame(
        columns=[
            "rebalance_date",
            "ticker",
            "adtv_inr_60d",
            "market_cap_inr",
            "sector",
            "included_in_top_200",
            "liquidity_bucket",
            "is_top100_mcap",
        ],
    )
    fake_cat = _fake_catalog_for(empty_df)
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get("/v1/admin/universe-snapshot")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_rows"] == 0
    assert body["top200_count"] == 0
    assert body["rows"] == []
    assert body["sectors"] == []
    assert body["buckets"] == []


def test_rebalances_empty_iceberg_returns_empty_list() -> None:
    app = _build_app(SUPERUSER)
    empty_df = pd.DataFrame(columns=["rebalance_date"])
    fake_cat = _fake_catalog_for(empty_df)
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get("/v1/admin/universe-snapshot/rebalances")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rebalances"] == []


def test_route_requires_superuser() -> None:
    for blocked in (GENERAL_USER, PRO_USER):
        app = _build_app(blocked)
        fake_cat = _fake_catalog_for(_two_rebalance_df())
        with patch(
            "stocks.create_tables._get_catalog",
            return_value=fake_cat,
        ):
            client = TestClient(app)
            r1 = client.get("/v1/admin/universe-snapshot")
            r2 = client.get(
                "/v1/admin/universe-snapshot/rebalances",
            )
            r3 = client.get(
                "/v1/admin/universe-snapshot/diff"
                "?from=2026-05-01&to=2026-05-15",
            )
        for r in (r1, r2, r3):
            assert r.status_code == 403, (
                f"{blocked.role} should be 403 — got {r.status_code}"
            )


def test_route_caches_snapshot_response() -> None:
    """Second call hits the in-memory cache, not Iceberg."""
    app = _build_app(SUPERUSER)
    fake_cat = _fake_catalog_for(_two_rebalance_df())
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
            "backend.algo.routes.universe_snapshot.get_cache",
            return_value=fake_cache,
        ),
    ):
        client = TestClient(app)
        url = "/v1/admin/universe-snapshot?rebalance_date=2026-05-15"
        r1 = client.get(url)
        r2 = client.get(url)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # First call: load_table once for the EqualTo scan. Second call
    # short-circuits via cache.get → no further load_table calls.
    assert fake_cat.load_table.call_count == 1
    assert fake_cache.set.call_count == 1


def test_diff_computes_entries_and_exits() -> None:
    """Diff between 2026-05-01 and 2026-05-15.

    Top-200 on 5/1: {AAA, BBB}
    Top-200 on 5/15: {AAA, DDD}
    → entries = {DDD}; exits = {BBB}
    """
    app = _build_app(SUPERUSER)
    fake_cat = _fake_catalog_for(_two_rebalance_df())
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_cat,
    ):
        client = TestClient(app)
        r = client.get(
            "/v1/admin/universe-snapshot/diff"
            "?from=2026-05-01&to=2026-05-15",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    entries = {e["ticker"] for e in body["entries"]}
    exits = {e["ticker"] for e in body["exits"]}
    assert entries == {"DDD"}
    assert exits == {"BBB"}


def test_diff_rejects_same_date() -> None:
    app = _build_app(SUPERUSER)
    client = TestClient(app)
    r = client.get(
        "/v1/admin/universe-snapshot/diff"
        "?from=2026-05-15&to=2026-05-15",
    )
    assert r.status_code == 400
