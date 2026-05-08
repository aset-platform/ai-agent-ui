"""Unit tests for the Advanced Analytics endpoints (AA-12).

Tight scope — exercises the 7 endpoints exposed by
:func:`backend.advanced_analytics_routes.create_advanced_analytics_router`
without standing up the full FastAPI app:

- ``pro_or_superuser`` guard returns 403 for general role
- All 7 endpoints return 200 + ``AdvancedReportResponse``
  shape for a superuser
- Cache hit short-circuits compute (no DuckDB call)
- ``stale_tickers`` populated from real NaN inputs
- Sort + pagination query params propagate through

Heavy dependencies (``_scoped_tickers``, ``_safe_query``,
``cache``) are stubbed so the tests run without Iceberg /
Redis / Postgres.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.advanced_analytics_routes as aar
from auth.dependencies import pro_or_superuser
from auth.models import UserContext

# ---------------------------------------------------------------
# Test universe + helpers
# ---------------------------------------------------------------

REPORTS = list(aar.REPORTS)


def _ctx(role: str) -> UserContext:
    return UserContext(
        user_id="user-aa-12",
        email="aa12@test",
        role=role,
    )


def _seed_tickers() -> list[str]:
    return ["AAA.NS", "BBB.NS", "CCC.NS"]


def _ohlcv_fixture(tickers: list[str]) -> pd.DataFrame:
    """25 rows of OHLCV per ticker with a trending close +
    breakout volume on the latest day."""
    rows: list[dict] = []
    base = date(2026, 4, 7)  # 25 trading days ending 2026-05-02
    for tkr in tickers:
        for i in range(25):
            d = base + timedelta(days=i)
            close = 100.0 + i  # rising trend
            vol = 1_000_000
            if i == 24:
                vol = 5_000_000  # last-day breakout
            rows.append(
                {
                    "ticker": tkr,
                    "date": d,
                    "open": close - 1,
                    "high": close + 1,
                    "low": close - 2,
                    "close": close,
                    "volume": vol,
                }
            )
    return pd.DataFrame(rows)


def _delivery_fixture(tickers: list[str]) -> pd.DataFrame:
    """25-day delivery history with a delivery surge on
    the latest day for AAA.NS only."""
    rows: list[dict] = []
    base = date(2026, 4, 7)
    for tkr in tickers:
        for i in range(25):
            d = base + timedelta(days=i)
            dpc = 30.0
            qty = 100_000
            if tkr == "AAA.NS" and i == 24:
                dpc = 60.0
                qty = 500_000
            rows.append(
                {
                    "ticker": tkr,
                    "date": d,
                    "deliverable_qty": qty,
                    "delivery_pct": dpc,
                    "traded_qty": qty * 3,
                    "traded_value": qty * 3 * (100 + i),
                }
            )
    return pd.DataFrame(rows)


def _company_fixture(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": t,
                "company_name": f"{t} Co",
                "sector": "Tech",
                "industry": "Software",
                "week_52_high": 200.0,
                "week_52_low": 50.0,
            }
            for t in tickers
        ]
    )


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def _stub_safe_query(*, ohlcv, delivery, company):
    """Build a ``_safe_query`` stub keyed on table name."""

    def _stub(table: str, sql: str) -> pd.DataFrame:
        if table == "stocks.ohlcv":
            return ohlcv
        if table == "stocks.nse_delivery":
            # _effective_trading_date issues a MAX(date) AS d query;
            # return the expected single-column aggregate shape so the
            # date-anchor logic can proceed without KeyError.
            if "AS d" in sql:
                return pd.DataFrame({"d": [date(2026, 5, 2)]})
            return delivery
        if table == "stocks.company_info":
            return company
        # Other tables empty (fundamentals, promoter, events,
        # piotroski, technical_indicators).
        return _empty_df()

    return _stub


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Mount only the AA router on a bare FastAPI app."""
    app = FastAPI()
    router = aar.create_advanced_analytics_router()
    app.include_router(router, prefix="/v1")

    async def _scope(_user, _scope):
        return _seed_tickers()

    monkeypatch.setattr(aar, "_scoped_tickers", _scope)

    monkeypatch.setattr(
        aar,
        "_safe_query",
        _stub_safe_query(
            ohlcv=_ohlcv_fixture(_seed_tickers()),
            delivery=_delivery_fixture(_seed_tickers()),
            company=_company_fixture(_seed_tickers()),
        ),
    )

    class _NoOpCache:
        def get(self, _k):
            return None

        def set(self, _k, _v, ttl=None):
            return None

    monkeypatch.setattr(aar, "get_cache", lambda: _NoOpCache())
    return app


@pytest.fixture
def super_client(app: FastAPI) -> TestClient:
    app.dependency_overrides[pro_or_superuser] = lambda: _ctx("superuser")
    return TestClient(app)


@pytest.fixture
def general_client(app: FastAPI) -> TestClient:
    app.dependency_overrides[pro_or_superuser] = lambda: (_ for _ in ()).throw(
        # Mirror the real guard: HTTP 403 from FastAPI
        # when the role check fails.  Easiest emulation
        # is to raise inside the override.
        __import__("fastapi").HTTPException(status_code=403)
    )
    return TestClient(app)


# ---------------------------------------------------------------
# 7 happy-path endpoint cases
# ---------------------------------------------------------------


@pytest.mark.parametrize("report", REPORTS)
def test_endpoint_happy_path_returns_response_shape(
    super_client: TestClient,
    report: str,
):
    """Every endpoint returns the AdvancedReportResponse shape."""
    r = super_client.get(f"/v1/advanced-analytics/{report}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {
        "rows",
        "total",
        "page",
        "page_size",
        "stale_tickers",
    } <= body.keys()
    assert isinstance(body["rows"], list)
    assert isinstance(body["total"], int)
    assert body["page"] == 1
    assert body["page_size"] == 25
    assert isinstance(body["stale_tickers"], list)


# ---------------------------------------------------------------
# 7 auth-failure cases (parameterised for symmetry)
# ---------------------------------------------------------------


@pytest.mark.parametrize("report", REPORTS)
def test_endpoint_returns_403_for_general_role(
    general_client: TestClient,
    report: str,
):
    r = general_client.get(f"/v1/advanced-analytics/{report}")
    assert r.status_code == 403


# ---------------------------------------------------------------
# Cross-cutting behaviours
# ---------------------------------------------------------------


def test_cache_hit_short_circuits_compute(
    monkeypatch: pytest.MonkeyPatch,
    super_client: TestClient,
):
    """When ``cache.get`` returns bytes, the body is
    returned verbatim and ``_safe_query`` is never called."""

    sentinel_body = (
        '{"rows":[{"ticker":"ZZZ.NS"}],"total":1,'
        '"page":1,"page_size":25,"stale_tickers":[]}'
    )

    class _HitCache:
        def get(self, k):
            # Return a parseable date for the as-of key so
            # _effective_trading_date returns early without
            # calling _safe_query (which would register a
            # spurious DuckDB call and break this assertion).
            if k == aar._AS_OF_CACHE_KEY:
                return "2026-05-02"
            return sentinel_body

        def set(self, _k, _v, ttl=None):
            return None

    monkeypatch.setattr(aar, "get_cache", lambda: _HitCache())

    calls: list[str] = []

    def _spy(table, sql):
        calls.append(table)
        return _empty_df()

    monkeypatch.setattr(aar, "_safe_query", _spy)

    r = super_client.get("/v1/advanced-analytics/current-day-upmove")
    assert r.status_code == 200
    assert r.json() == {
        "rows": [{"ticker": "ZZZ.NS"}],
        "total": 1,
        "page": 1,
        "page_size": 25,
        "stale_tickers": [],
    }
    assert calls == [], "cache hit must skip DuckDB reads"


def test_pagination_and_sort_query_params_accepted(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/top-50-delivery-by-qty"
        "?page=1&page_size=2&sort_key=today_dv&sort_dir=asc"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["rows"]) <= 2


def test_search_filters_tickers_by_substring(
    super_client: TestClient,
):
    """`?search=AAA` keeps only tickers containing AAA
    (case-insensitive). Seed universe is AAA / BBB / CCC.NS."""
    r = super_client.get(
        "/v1/advanced-analytics/top-50-delivery-by-qty" "?search=aaa"
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert all("AAA" in row["ticker"] for row in rows), rows


def test_search_unknown_ticker_returns_empty(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/top-50-delivery-by-qty" "?search=ZZZZ"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["total"] == 0


def test_stale_tickers_populated_for_missing_inputs(
    monkeypatch: pytest.MonkeyPatch,
    super_client: TestClient,
):
    """A ticker present in the universe but absent from the
    company / delivery / fundamentals tables surfaces in
    ``stale_tickers`` with the correct reason."""

    monkeypatch.setattr(
        aar,
        "_safe_query",
        _stub_safe_query(
            ohlcv=_empty_df(),  # no closes → nan_close
            delivery=_empty_df(),
            company=_empty_df(),
        ),
    )

    r = super_client.get("/v1/advanced-analytics/top-50-delivery-by-qty")
    assert r.status_code == 200
    stale = r.json()["stale_tickers"]
    tickers = sorted(s["ticker"] for s in stale)
    assert tickers == sorted(_seed_tickers())
    reasons = {s["reason"] for s in stale}
    assert reasons.issubset(
        {
            "nan_close",
            "missing_delivery",
            "missing_quarterly",
            "missing_promoter",
        }
    )


def test_invalid_sort_dir_rejected_by_validator(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/current-day-upmove?sort_dir=ASCENDING"
    )
    assert r.status_code == 422


def test_top_50_caps_at_50_rows_post_filter(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/top-50-delivery-by-qty?page=1&page_size=200"
    )
    assert r.status_code == 200
    assert r.json()["total"] <= 50


# ---------------------------------------------------------------
# _load_indicators_latest — unit tests (AA RSI fix)
# ---------------------------------------------------------------


def _ohlcv_215d(tickers: list[str]) -> pd.DataFrame:
    """215 daily OHLCV rows per ticker — enough for SMA-200."""
    rows: list[dict] = []
    base = date(2025, 9, 1)
    for tkr in tickers:
        for i in range(215):
            d = base + timedelta(days=i)
            close = 100.0 + i * 0.1
            rows.append(
                {
                    "ticker": tkr,
                    "date": d,
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def test_load_indicators_latest_returns_rsi_sma_columns(
    monkeypatch: pytest.MonkeyPatch,
):
    """With 215 rows, all three indicator columns are non-None."""
    tickers = ["AAA.NS", "BBB.NS"]

    monkeypatch.setattr(
        aar,
        "_safe_query",
        lambda table, sql: _ohlcv_215d(tickers),
    )

    df = aar._load_indicators_latest(tickers)

    assert set(df.columns) >= {"ticker", "rsi_14", "sma_50", "sma_200"}
    assert sorted(df["ticker"].tolist()) == sorted(tickers)
    for col in ("rsi_14", "sma_50", "sma_200"):
        assert df[col].notna().all(), f"{col} should be non-NaN with 215 rows"


def test_load_indicators_latest_empty_ohlcv(
    monkeypatch: pytest.MonkeyPatch,
):
    """Empty OHLCV returns empty DataFrame without error."""
    monkeypatch.setattr(
        aar,
        "_safe_query",
        lambda table, sql: pd.DataFrame(),
    )

    df = aar._load_indicators_latest(["AAA.NS"])
    assert df.empty


def test_load_indicators_latest_empty_tickers():
    """Empty ticker list returns empty DataFrame immediately."""
    df = aar._load_indicators_latest([])
    assert df.empty


# ---------------------------------------------------------------
# Bundle filter wiring (Sprint 9 follow-on)
# ---------------------------------------------------------------


def test_endpoint_rejects_unknown_tech_filter(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=not_real"
    )
    assert r.status_code == 400
    assert "not_real" in r.json()["detail"]


def test_endpoint_rejects_unknown_fund_filter(
    super_client: TestClient,
):
    r = super_client.get("/v1/advanced-analytics/current-day-upmove?fund=foo")
    assert r.status_code == 400
    assert "foo" in r.json()["detail"]


def test_endpoint_accepts_known_tech_filter(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=golden_recent"
    )
    assert r.status_code == 200


def test_endpoint_accepts_both_bundles(
    super_client: TestClient,
):
    r = super_client.get(
        "/v1/advanced-analytics/current-day-upmove"
        "?tech=golden_recent&fund=fscore_ge_7"
    )
    assert r.status_code == 200


def test_bundle_filters_distinguish_cache_keys(
    monkeypatch: pytest.MonkeyPatch,
    super_client: TestClient,
):
    """Distinct filter combos must produce distinct inner cache keys."""
    captured: list[str] = []

    class _CapCache:
        def get(self, _k):
            return None

        def set(self, k, _v, ttl=None):
            captured.append(k)

    monkeypatch.setattr(aar, "get_cache", lambda: _CapCache())

    super_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=golden_recent"
    )
    super_client.get(
        "/v1/advanced-analytics/current-day-upmove?tech=price_gt_sma50"
    )

    assert any("ftechgolden_recent" in k for k in captured)
    assert any("ftechprice_gt_sma50" in k for k in captured)
