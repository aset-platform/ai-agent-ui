"""Tests for the ``intraday_features_daily_compute`` scheduler job
(ASETPLTFRM-402 / FE-3).

Covers the orchestration shape, the NaN-replaceable upsert
predicate, and the structured stats roll-up. Iceberg / DuckDB / PG
boundaries are mocked.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.algo.backtest.types import BarData
from backend.algo.features import FEATURE_SET_VERSION
from backend.algo.jobs.intraday_features_daily_compute import (
    run_intraday_features_daily_compute_job,
)


def _bar(
    ticker="A.NS", day=date(2026, 5, 13), ts_ns=1_700_000_000_000_000_000
):
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=1000,
        bar_open_ts_ns=ts_ns,
    )


@pytest.fixture
def fake_session():
    """Stand-in for ``disposable_pg_session()`` — itself an async
    context manager (no factory layer)."""
    sess = AsyncMock()

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=sess)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=session_cm)
    return factory, sess


def _patch_session_and_universe(factory, universe):
    """Common context-manager stack: patch ``disposable_pg_session``
    plus the universe resolver to return ``universe``."""
    return (
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=list(universe)),
        ),
    )


async def test_happy_path_writes_features_with_version_stamp(
    fake_session,
):
    """End-to-end happy path: 2 tickers, panel returns 3 features
    per bar, write path stamps version + tz-naive ``written_at``,
    stats payload is well-formed."""
    factory, _ = fake_session
    bars_a = [_bar("A.NS", ts_ns=1_700_000_000_000_000_000)]
    bars_b = [_bar("B.NS", ts_ns=1_700_000_900_000_000_000)]
    panel = {
        "A.NS": {
            1_700_000_000_000_000_000: {
                "today_ltp": Decimal("100.5"),
                "today_vol": Decimal("1000"),
                "rsi": Decimal("55"),
            },
        },
        "B.NS": {
            1_700_000_900_000_000_000: {
                "today_ltp": Decimal("100.5"),
                "today_vol": Decimal("1000"),
                "rsi": Decimal("60"),
            },
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS", "B.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            side_effect=lambda ticker, **_: (
                bars_a if ticker == "A.NS" else bars_b
            ),
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    assert result["status"] == "ok"
    assert result["universe_size"] == 2
    assert result["tickers_processed"] == 2
    assert result["tickers_failed"] == 0
    assert result["rows_written"] == 6  # 2 tickers × 3 features
    assert result["feature_set_version"] == FEATURE_SET_VERSION
    assert result["window"] == ["2026-05-13", "2026-05-13"]
    assert result["interval_sec"] == 900

    # Exactly one append call with the expected schema.
    assert mock_tbl.append.call_count == 1
    arrow_tbl = mock_tbl.append.call_args.args[0]
    schema_names = arrow_tbl.schema.names
    for col in (
        "ticker",
        "bar_open_ts_ns",
        "bar_date",
        "year_month",
        "interval_sec",
        "feature_name",
        "feature_value",
        "feature_set_version",
        "written_at",
    ):
        assert col in schema_names
    # feature_set_version stamped on every row.
    fsv_col = arrow_tbl.column("feature_set_version").to_pylist()
    assert all(v == FEATURE_SET_VERSION for v in fsv_col)
    # written_at must be tz-naive (Iceberg TimestampType requirement).
    written = arrow_tbl.column("written_at").to_pylist()
    assert all(isinstance(w, datetime) and w.tzinfo is None for w in written)


async def test_rerun_scoped_pre_delete_uses_in_ticker(fake_session):
    """NaN-replaceable upsert: pre-delete predicate must scope by
    ``In("ticker", batch)`` (NOT EqualTo on the whole table) so
    re-running the same window overwrites cleanly without wiping
    other tickers."""
    from pyiceberg.expressions import And

    factory, _ = fake_session
    bars = [_bar("A.NS")]
    panel = {
        "A.NS": {
            bars[0].bar_open_ts_ns: {"today_ltp": Decimal("100.5")},
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    mock_tbl.delete.assert_called_once()
    pred = mock_tbl.delete.call_args.args[0]
    assert isinstance(pred, And)
    # Walk the And-tree and collect every scoped-ref name seen.
    # PyIceberg collapses ``In(name, [single])`` to ``EqualTo`` so
    # we accept either node type — what matters is that ``ticker``
    # AND ``bar_date`` BOTH appear as scoped predicates (the
    # NaN-replaceable upsert contract — per-DAY granularity, not
    # per-month, otherwise the daily keeper [yesterday, today]
    # window silently wipes prior-day features from the same
    # month on every run).
    seen_refs: set[str] = set()

    def _walk(p):
        if isinstance(p, And):
            _walk(p.left)
            _walk(p.right)
            return
        term = getattr(p, "term", None)
        if term is not None:
            name = getattr(term, "name", None)
            if name:
                seen_refs.add(name)

    _walk(pred)
    assert "ticker" in seen_refs, f"got {seen_refs}"
    assert "bar_date" in seen_refs, f"got {seen_refs}"
    assert (
        "year_month" not in seen_refs
    ), (
        "Regression guard: pre-delete must NOT scope on "
        "year_month — that wipes prior-day features in the "
        "current month on every daily keeper run "
        "(force re-run + day-N-of-month bug). Use bar_date."
    )


async def test_per_ticker_fetch_failure_does_not_abort_batch(
    fake_session,
):
    """One bad ticker (bar fetch raises) is recorded as a failure
    but doesn't strand the rest of the batch."""
    factory, _ = fake_session
    good_bars = [_bar("OK.NS")]
    panel = {
        "OK.NS": {
            good_bars[0].bar_open_ts_ns: {"today_ltp": Decimal("100.5")},
        },
    }

    def _loader(ticker, **_):
        if ticker == "BAD.NS":
            raise RuntimeError("simulated read failure")
        return good_bars

    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["OK.NS", "BAD.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            side_effect=_loader,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    assert result["status"] == "ok"
    assert result["tickers_processed"] == 1
    assert result["tickers_failed"] == 1
    assert any(t == "BAD.NS" for t, _ in result["failures"])
    # The good ticker's write still happened.
    assert mock_tbl.append.call_count == 1
    assert result["rows_written"] == 1


async def test_invalid_interval_sec_returns_structured_error(
    fake_session,
):
    """``interval_sec`` outside ``(900, 300, 60)`` must be rejected
    before any read/compute fires."""
    factory, _ = fake_session
    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
        ) as mock_loader,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
        ) as mock_compute,
    ):
        result = await run_intraday_features_daily_compute_job(
            {"interval_sec": 120},
        )
    assert result["status"] == "error"
    assert "120" in result["error"]
    mock_loader.assert_not_called()
    mock_compute.assert_not_called()


async def test_empty_universe_returns_skipped(fake_session):
    """Empty universe → graceful early exit, no compute / write."""
    factory, _ = fake_session
    sess_patch, univ_patch = _patch_session_and_universe(factory, [])
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
        ) as mock_loader,
    ):
        result = await run_intraday_features_daily_compute_job(None)
    assert result["status"] == "skipped_empty_universe"
    assert result["universe_size"] == 0
    mock_loader.assert_not_called()


async def test_payload_tickers_override_bypasses_universe_resolver(
    fake_session,
):
    """An explicit ``payload.tickers`` list MUST NOT touch the
    universe resolver — the on-demand backfill path relies on this
    so it works without a PG session."""
    factory, _ = fake_session
    bars = [_bar("X.NS")]
    panel = {
        "X.NS": {
            bars[0].bar_open_ts_ns: {"today_ltp": Decimal("99")},
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    with (
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=["should-not-be-used"]),
        ) as resolver_mock,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {
                "tickers": ["X.NS"],
                "period_start": "2026-05-13",
                "period_end": "2026-05-13",
            },
        )
    assert result["status"] == "ok"
    assert result["universe_size"] == 1
    resolver_mock.assert_not_awaited()


async def test_nan_feature_values_filtered_before_write(
    fake_session,
):
    """NaN / inf feature values MUST be filtered before the Arrow
    table is constructed — they'd silently poison downstream
    readers that treat NaN as 'feature missing'."""
    import math

    factory, _ = fake_session
    bars = [_bar("A.NS")]
    panel = {
        "A.NS": {
            bars[0].bar_open_ts_ns: {
                "today_ltp": Decimal("100"),
                "rsi": float("nan"),
                "atr_14": math.inf,
                "vwap": Decimal("99.5"),
            },
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    assert result["status"] == "ok"
    # Only today_ltp + vwap survive (rsi NaN, atr_14 inf filtered).
    assert result["rows_written"] == 2
    arrow_tbl = mock_tbl.append.call_args.args[0]
    feat_names = arrow_tbl.column("feature_name").to_pylist()
    assert set(feat_names) == {"today_ltp", "vwap"}


async def test_daily_compute_passes_fe8_kwargs_into_engine(
    fake_session,
):
    """FE-8: the daily compute MUST load index bars +
    ticker→sector map and pass them into
    ``compute_intraday_features_for_universe`` as kwargs. Mock the
    loaders and assert the kwargs reach the engine."""
    factory, _ = fake_session
    bars = [_bar("A.NS")]
    panel = {
        "A.NS": {
            bars[0].bar_open_ts_ns: {"today_ltp": Decimal("100.5")},
        },
    }
    fake_index_bars = {"NIFTY 50": [_bar("NIFTY 50")]}
    fake_sector_map = {"A.NS": "NIFTY IT"}
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "load_index_intraday_bars_window",
            return_value=fake_index_bars,
        ) as mock_index_loader,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "build_ticker_to_sector_index_map",
            new=AsyncMock(return_value=fake_sector_map),
        ) as mock_sector,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ) as mock_compute,
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    assert result["status"] == "ok"
    # Index loader called once with the full universe.
    mock_index_loader.assert_called_once()
    idx_kw = mock_index_loader.call_args.kwargs
    assert "NIFTY 50" in idx_kw["symbols"]
    assert idx_kw["interval_sec"] == 900
    # Sector map called once with the resolved universe.
    mock_sector.assert_awaited_once_with(["A.NS"])
    # Compute called with the FE-8 kwargs forwarded.
    mock_compute.assert_called_once()
    compute_kwargs = mock_compute.call_args.kwargs
    assert compute_kwargs["index_bars_by_symbol"] == fake_index_bars
    assert compute_kwargs["ticker_to_sector_index"] == fake_sector_map


async def test_daily_compute_proceeds_when_index_bars_empty(
    fake_session,
):
    """When ``load_index_intraday_bars_window`` returns empty (e.g.
    FE-6 hasn't backfilled the window yet), the compute MUST still
    run — non-FE-8 features emit and the FE-8 features are absent.
    """
    factory, _ = fake_session
    bars = [_bar("A.NS")]
    panel_without_fe8 = {
        "A.NS": {
            bars[0].bar_open_ts_ns: {
                "today_ltp": Decimal("100.5"),
                "today_vol": Decimal("1000"),
                "rsi": Decimal("55"),
            },
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "load_index_intraday_bars_window",
            return_value={},  # empty
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "build_ticker_to_sector_index_map",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel_without_fe8,
        ) as mock_compute,
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    assert result["status"] == "ok"
    # Non-FE-8 features still wrote.
    assert result["rows_written"] == 3
    # The engine was called with index_bars_by_symbol=None — the
    # cohort path is short-circuited cleanly rather than receiving
    # an empty dict that would re-emit zeros.
    compute_kwargs = mock_compute.call_args.kwargs
    assert compute_kwargs["index_bars_by_symbol"] is None


async def test_daily_compute_passes_fe9_regime_by_date_into_engine(
    fake_session,
):
    """FE-9: the daily compute MUST call ``_load_regime_for_window``
    and forward its output to the engine as
    ``regime_by_date``."""
    from backend.algo.regime.repo import RegimeRow

    factory, _ = fake_session
    bars = [_bar("A.NS")]
    panel = {
        "A.NS": {
            bars[0].bar_open_ts_ns: {"today_ltp": Decimal("100.5")},
        },
    }
    fake_rh = [
        RegimeRow(
            bar_date=date(2026, 5, 13),
            regime_label="BULL",
            stress_prob=0.1,
            rule_inputs={},
        ),
    ]
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "load_index_intraday_bars_window",
            return_value={},
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "build_ticker_to_sector_index_map",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "get_regime_history",
            return_value=fake_rh,
        ) as mock_rh,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ) as mock_compute,
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    assert result["status"] == "ok"
    mock_rh.assert_called_once()
    compute_kwargs = mock_compute.call_args.kwargs
    # The engine was called with a populated regime_by_date dict.
    rbd = compute_kwargs["regime_by_date"]
    assert rbd is not None
    assert date(2026, 5, 13) in rbd
    entry = rbd[date(2026, 5, 13)]
    assert entry["regime_label"] == "BULL"
    # float stress_prob from repo → Decimal in the engine input.
    assert entry["stress_prob"] == Decimal(str(0.1))


async def test_daily_compute_handles_regime_history_failure(
    fake_session,
):
    """FE-9: if ``get_regime_history`` raises, the compute MUST
    still proceed — empty dict passed (engine then drops FE-9
    regime features but rest of the panel still emits)."""
    factory, _ = fake_session
    bars = [_bar("A.NS")]
    panel = {
        "A.NS": {
            bars[0].bar_open_ts_ns: {"today_ltp": Decimal("100.5")},
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "_load_intraday_bars_for_ticker",
            return_value=bars,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "load_index_intraday_bars_window",
            return_value={},
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "build_ticker_to_sector_index_map",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "get_regime_history",
            side_effect=RuntimeError("simulated regime read fail"),
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "compute_intraday_features_for_universe",
            return_value=panel,
        ) as mock_compute,
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_intraday_features_daily_compute_job(
            {"period_start": "2026-05-13", "period_end": "2026-05-13"},
        )

    # Job completes successfully and rows are still written.
    assert result["status"] == "ok"
    assert result["rows_written"] == 1
    # Engine sees regime_by_date=None (empty dict short-circuited).
    compute_kwargs = mock_compute.call_args.kwargs
    assert compute_kwargs["regime_by_date"] is None


def test_register_job_wired_in_executor():
    """``intraday_features_daily_compute`` must be registered in
    ``JOB_EXECUTORS`` so the pipeline executor can chain it."""
    from backend.jobs.executor import JOB_EXECUTORS

    assert "intraday_features_daily_compute" in JOB_EXECUTORS


def test_register_job_wrapper_is_pipeline_compatible():
    """Sync wrapper with the standard pipeline-step signature.
    Async-only handler would silent-succeed by returning a
    coroutine the executor never awaits."""
    import inspect

    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["intraday_features_daily_compute"]
    assert not inspect.iscoroutinefunction(fn)
    params = inspect.signature(fn).parameters
    for required in (
        "scope",
        "run_id",
        "repo",
        "cancel_event",
        "force",
    ):
        assert required in params


def test_register_job_wrapper_runs_async_job():
    """The sync wrapper must drive the async job to a return
    value, not hand back an unawaited coroutine."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["intraday_features_daily_compute"]

    async def _fake_async_job(payload):
        return {
            "status": "ok",
            "payload_seen": payload,
            "rows_written": 0,
        }

    with patch(
        "backend.algo.jobs.intraday_features_daily_compute."
        "run_intraday_features_daily_compute_job",
        new=_fake_async_job,
    ):
        result = fn(
            scope=None,
            run_id="abc",
            repo=None,
            cancel_event=None,
            force=False,
            payload={"interval_sec": 900},
        )
    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["payload_seen"] == {"interval_sec": 900}
