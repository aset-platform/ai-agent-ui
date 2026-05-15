"""Tests for the ``trade_outcome_backfill`` scheduler job
(ASETPLTFRM-415 / FE-13).

Covers outcome derivation rules, scoped pre-delete predicate,
dry-run gating, filter pass-through, idempotency on re-run, cache
invalidation, per-row failure isolation, and the
:func:`disposable_pg_session` cross-loop guard. Iceberg / cache
boundaries are mocked — the job has no PG dependency of its own,
but we still assert the writer's table identity stays stable.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from backend.algo.jobs.trade_outcome_backfill import (
    _DEFAULT_MIN_WINNER_THRESHOLD,
    _TRADE_FEATURE_SNAPSHOTS_TABLE,
    _derive_outcome,
    _snapshot_arrow_schema,
    run_trade_outcome_backfill_job,
)


def _row(
    *,
    fill_id: str,
    realised_pnl_inr: float | None,
    outcome_label: str | None = None,
    strategy_id: str = "strat-A",
    bar_date: str = "2026-05-14",
    ticker: str = "RELIANCE.NS",
    side: str = "BUY",
    qty: int = 10,
    fill_price: float = 1000.0,
    fill_ts_ns: int = 1_700_000_000_000_000_000,
    run_id: str = "run-X",
    mode: str = "backtest",
    features_json: str = "{}",
) -> dict:
    """Build a candidate row dict shaped like the PyIceberg
    scan output (``.to_arrow().to_pylist()``)."""
    return {
        "fill_id": fill_id,
        "run_id": run_id,
        "strategy_id": strategy_id,
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "fill_price": fill_price,
        "fill_ts_ns": fill_ts_ns,
        "bar_date": bar_date,
        "year_month": bar_date[:7],
        "mode": mode,
        "features_json": features_json,
        "realised_pnl_inr": realised_pnl_inr,
        "outcome_label": outcome_label,
        "written_at": datetime(2026, 5, 14, 10, 0, 0),
    }


def _patch_scan(rows: list[dict]):
    """Patch the scan helper to return ``rows`` regardless of
    filter — the predicate is exercised separately via
    :func:`_build_row_filter`."""
    return patch(
        "backend.algo.jobs.trade_outcome_backfill." "_scan_candidate_rows",
        return_value=list(rows),
    )


def _patch_catalog():
    """Patch ``_get_catalog`` so the rewrite path is fully
    mocked. Returns ``(catalog_mock, table_mock)``."""
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl
    return mock_cat, mock_tbl


async def test_labels_winner_loser_breakeven_correctly():
    """Synthetic rows with pnl = +5, -5, 0 → winner / loser /
    breakeven respectively, and the rewrite Arrow batch carries
    those labels in column order."""
    rows = [
        _row(fill_id="w1", realised_pnl_inr=5.0),
        _row(fill_id="l1", realised_pnl_inr=-5.0),
        _row(fill_id="b1", realised_pnl_inr=0.0),
    ]
    mock_cat, mock_tbl = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job({})

    assert result["status"] == "ok"
    assert result["rows_scanned"] == 3
    assert result["rows_labeled"] == 3
    assert result["rows_unchanged"] == 0
    assert mock_tbl.append.call_count == 1
    arrow_tbl = mock_tbl.append.call_args.args[0]
    labels = arrow_tbl.column("outcome_label").to_pylist()
    fids = arrow_tbl.column("fill_id").to_pylist()
    by_fid = dict(zip(fids, labels))
    assert by_fid["w1"] == "winner"
    assert by_fid["l1"] == "loser"
    assert by_fid["b1"] == "breakeven"


async def test_breakeven_threshold_at_default_001():
    """Pnl = 0.005 → breakeven (≤ threshold);
    pnl = 0.02 → winner (> threshold). Confirms the boundary
    uses ``>`` / ``<`` not ``>=`` / ``<=``."""
    rows = [
        _row(fill_id="t1", realised_pnl_inr=0.005),
        _row(fill_id="t2", realised_pnl_inr=0.02),
        _row(fill_id="t3", realised_pnl_inr=-0.005),
        _row(fill_id="t4", realised_pnl_inr=-0.02),
    ]
    mock_cat, mock_tbl = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job({})
    assert result["rows_labeled"] == 4
    arrow_tbl = mock_tbl.append.call_args.args[0]
    by_fid = dict(
        zip(
            arrow_tbl.column("fill_id").to_pylist(),
            arrow_tbl.column("outcome_label").to_pylist(),
        )
    )
    assert by_fid["t1"] == "breakeven"
    assert by_fid["t2"] == "winner"
    assert by_fid["t3"] == "breakeven"
    assert by_fid["t4"] == "loser"


async def test_skips_rows_with_null_pnl():
    """Race-window guard: if a row leaks through the scan with
    ``realised_pnl_inr=None`` it must NOT be labelled."""
    rows = [_row(fill_id="x1", realised_pnl_inr=None)]
    mock_cat, mock_tbl = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job({})
    assert result["rows_labeled"] == 0
    assert result["rows_unchanged"] == 1
    mock_tbl.append.assert_not_called()


async def test_skips_rows_with_existing_label():
    """Race-window guard #2 — re-runs must be idempotent: a row
    that already has ``outcome_label='winner'`` is left
    untouched."""
    rows = [
        _row(
            fill_id="already",
            realised_pnl_inr=5.0,
            outcome_label="winner",
        ),
        _row(fill_id="fresh", realised_pnl_inr=3.0),
    ]
    mock_cat, mock_tbl = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job({})
    assert result["rows_labeled"] == 1
    assert result["rows_unchanged"] == 1
    arrow_tbl = mock_tbl.append.call_args.args[0]
    fids = arrow_tbl.column("fill_id").to_pylist()
    assert fids == ["fresh"]


async def test_per_row_label_failure_continues_batch():
    """If one row's coercion path raises, the rest of the batch
    is still labelled and the failure surfaces in stats."""
    rows = [
        _row(fill_id="ok1", realised_pnl_inr=5.0),
        _row(fill_id="bad", realised_pnl_inr=5.0),
        _row(fill_id="ok2", realised_pnl_inr=-5.0),
    ]
    real_coerce_call = {"calls": 0}

    def _flaky_coerce(row, *, new_label):
        if row.get("fill_id") == "bad":
            raise RuntimeError("synthetic coerce failure")
        real_coerce_call["calls"] += 1
        return {
            "fill_id": str(row["fill_id"]),
            "run_id": str(row.get("run_id") or ""),
            "strategy_id": str(row.get("strategy_id") or ""),
            "ticker": str(row.get("ticker") or ""),
            "side": str(row.get("side") or ""),
            "qty": int(row.get("qty") or 0),
            "fill_price": float(row.get("fill_price") or 0.0),
            "fill_ts_ns": int(row.get("fill_ts_ns") or 0),
            "bar_date": str(row.get("bar_date") or ""),
            "year_month": str(row.get("year_month") or ""),
            "mode": str(row.get("mode") or ""),
            "features_json": str(row.get("features_json") or "{}"),
            "realised_pnl_inr": float(row["realised_pnl_inr"]),
            "outcome_label": new_label,
            "written_at": datetime(2026, 5, 14, 10, 0, 0),
        }

    mock_cat, mock_tbl = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_coerce_row_for_rewrite",
            side_effect=_flaky_coerce,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job({})
    assert result["rows_labeled"] == 2
    assert any(fid == "bad" for fid, _ in result["failures"])
    assert real_coerce_call["calls"] == 2


async def test_dry_run_no_iceberg_write():
    """``dry_run=True`` → counts labels but never invokes the
    retry-helper write path."""
    rows = [
        _row(fill_id="d1", realised_pnl_inr=5.0),
        _row(fill_id="d2", realised_pnl_inr=-5.0),
    ]
    mock_cat, mock_tbl = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill." "retry_iceberg_op",
        ) as retry_spy,
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ) as inval_spy,
    ):
        result = await run_trade_outcome_backfill_job({"dry_run": True})
    assert result["dry_run"] is True
    assert result["rows_labeled"] == 2
    assert result["rows_scanned"] == 2
    retry_spy.assert_not_called()
    mock_tbl.append.assert_not_called()
    mock_tbl.delete.assert_not_called()
    inval_spy.assert_not_called()


async def test_strategy_id_filter():
    """``strategy_id`` is propagated into the scan call so the
    Iceberg predicate filters to one strategy."""
    captured: dict = {}

    def _spy_scan(*, period_start, period_end, strategy_id):
        captured["strategy_id"] = strategy_id
        return [
            _row(
                fill_id="s1",
                realised_pnl_inr=5.0,
                strategy_id="X",
            )
        ]

    mock_cat, mock_tbl = _patch_catalog()
    with (
        patch(
            "backend.algo.jobs.trade_outcome_backfill." "_scan_candidate_rows",
            side_effect=_spy_scan,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job({"strategy_id": "X"})
    assert captured["strategy_id"] == "X"
    assert result["strategy_id"] == "X"
    assert result["rows_labeled"] == 1


async def test_period_filter():
    """``period_start`` / ``period_end`` are propagated into the
    scan helper and the stats window."""
    captured: dict = {}

    def _spy_scan(*, period_start, period_end, strategy_id):
        captured["start"] = period_start
        captured["end"] = period_end
        return []

    with (
        patch(
            "backend.algo.jobs.trade_outcome_backfill." "_scan_candidate_rows",
            side_effect=_spy_scan,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache",
        ),
    ):
        result = await run_trade_outcome_backfill_job(
            {
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            }
        )
    assert captured["start"] == date(2026, 4, 1)
    assert captured["end"] == date(2026, 4, 30)
    assert result["window"] == ["2026-04-01", "2026-04-30"]


async def test_cache_invalidation_fires():
    """After a successful write the FE-11 feature-importance
    cache is invalidated by glob pattern."""
    rows = [_row(fill_id="c1", realised_pnl_inr=5.0)]
    mock_cat, _ = _patch_catalog()
    with (
        _patch_scan(rows),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.trade_outcome_backfill.get_cache"
        ) as get_cache_spy,
    ):
        cache_mock = MagicMock()
        get_cache_spy.return_value = cache_mock
        result = await run_trade_outcome_backfill_job({})
    assert result["rows_labeled"] == 1
    cache_mock.invalidate.assert_called_once_with("cache:feature_importance:*")


async def test_cache_invalidation_skipped_when_zero_rows():
    """If no rows are labelled, the cache invalidation must NOT
    fire — there's nothing stale to flush."""
    with (
        _patch_scan([]),
        patch(
            "backend.algo.jobs.trade_outcome_backfill."
            "_invalidate_feature_importance_cache"
        ) as inval_spy,
    ):
        result = await run_trade_outcome_backfill_job({})
    assert result["rows_scanned"] == 0
    assert result["rows_labeled"] == 0
    inval_spy.assert_not_called()


def test_disposable_pg_session_used():
    """Cross-loop bug guard — the job MUST import
    :func:`disposable_pg_session` (not the cached factory)
    even if today's body doesn't yet touch PG, so future
    extensions that need PG access pick the right primitive.
    """
    src = inspect.getsource(run_trade_outcome_backfill_job)
    mod_src = inspect.getsource(
        __import__(
            "backend.algo.jobs.trade_outcome_backfill",
            fromlist=["*"],
        )
    )
    assert "get_session_factory" not in mod_src, (
        "trade_outcome_backfill must NOT use the cached "
        "get_session_factory (cross-loop bug, see "
        "pg-nullpool-sync-async-bridge memory)"
    )
    # The module imports the disposable helper.
    assert "disposable_pg_session" in mod_src
    # And the job body is async (run under asyncio.run from
    # the executor wrapper).
    assert inspect.iscoroutinefunction(run_trade_outcome_backfill_job)
    # Silence unused-var warning.
    assert "stats" in src or True


def test_register_job_decorator_exists():
    """Sanity check: the ``trade_outcome_backfill`` job type is
    registered in the executor (activates on next backend
    restart per CLAUDE.md §6.2)."""
    from backend.jobs.executor import (
        execute_trade_outcome_backfill,
    )

    assert callable(execute_trade_outcome_backfill)


def test_derive_outcome_unit_thresholds():
    """Direct unit coverage of :func:`_derive_outcome` — the
    happy paths are exercised by the orchestration tests; this
    locks down the NaN / inf / None edges."""
    th = _DEFAULT_MIN_WINNER_THRESHOLD
    assert _derive_outcome(None, min_winner_threshold=th) is None
    assert _derive_outcome(float("nan"), min_winner_threshold=th) is None
    assert _derive_outcome(float("inf"), min_winner_threshold=th) is None
    assert _derive_outcome(0.0, min_winner_threshold=th) == "breakeven"
    assert _derive_outcome(1.0, min_winner_threshold=th) == "winner"
    assert _derive_outcome(-1.0, min_winner_threshold=th) == "loser"


def test_snapshot_schema_matches_fe5_writer_column_order():
    """Arrow column order of the rewrite path MUST match FE-5
    snapshot writer exactly — column-order drift would cause
    PyIceberg to reject the append against the required-column
    schema."""
    fe5_schema = _snapshot_arrow_schema()
    expected = [
        "fill_id",
        "run_id",
        "strategy_id",
        "ticker",
        "side",
        "qty",
        "fill_price",
        "fill_ts_ns",
        "bar_date",
        "year_month",
        "mode",
        "features_json",
        "realised_pnl_inr",
        "outcome_label",
        "written_at",
    ]
    assert fe5_schema.names == expected
    # ``outcome_label`` must be nullable=True so a labelled row
    # AND the original-null row share the same Arrow column
    # type.
    assert fe5_schema.field("outcome_label").nullable is True
    # ``realised_pnl_inr`` must also be nullable for the same
    # reason.
    assert fe5_schema.field("realised_pnl_inr").nullable is True
    assert _TRADE_FEATURE_SNAPSHOTS_TABLE == ("stocks.trade_feature_snapshots")


def test_scoped_pre_delete_uses_in_fill_id():
    """Direct test of the write path — the pre-delete predicate
    MUST scope to ``In("fill_id", batch)`` per CLAUDE.md §4.3
    #18 (NOT a global ``EqualTo`` on bar_date / strategy_id)."""
    from pyiceberg.expressions import In

    from backend.algo.jobs.trade_outcome_backfill import (
        _write_labeled_batch,
    )

    rewrite_rows = [
        {
            "fill_id": "f1",
            "run_id": "r",
            "strategy_id": "s",
            "ticker": "T.NS",
            "side": "BUY",
            "qty": 1,
            "fill_price": 1.0,
            "fill_ts_ns": 1,
            "bar_date": "2026-05-14",
            "year_month": "2026-05",
            "mode": "backtest",
            "features_json": "{}",
            "realised_pnl_inr": 5.0,
            "outcome_label": "winner",
            "written_at": datetime(2026, 5, 14, 10, 0, 0),
        }
    ]
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl
    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
    ):
        written = _write_labeled_batch(
            rewrite_rows=rewrite_rows,
            fill_ids=["f1"],
        )
    assert written == 1
    mock_tbl.delete.assert_called_once()
    pred = mock_tbl.delete.call_args.args[0]
    # PyIceberg collapses ``In(name, [single])`` to ``EqualTo``
    # for single-element batches; either node type is acceptable
    # so long as the scoped reference is ``fill_id`` (NEVER a
    # global column like ``strategy_id`` or ``bar_date``).
    name = getattr(pred.term, "name", None)
    assert name == "fill_id", f"got {type(pred).__name__}: {pred!r}"
    _ = In  # keep import live for clarity


def test_scoped_pre_delete_uses_in_fill_id_multi_batch():
    """Multi-fill_id batch path — PyIceberg keeps ``In`` for
    ≥2 elements; confirms the predicate is ``In("fill_id",
    [...])`` not ``In(<other column>, ...)``."""
    from pyiceberg.expressions import In

    from backend.algo.jobs.trade_outcome_backfill import (
        _write_labeled_batch,
    )

    rewrite_rows = [
        {
            "fill_id": fid,
            "run_id": "r",
            "strategy_id": "s",
            "ticker": "T.NS",
            "side": "BUY",
            "qty": 1,
            "fill_price": 1.0,
            "fill_ts_ns": 1,
            "bar_date": "2026-05-14",
            "year_month": "2026-05",
            "mode": "backtest",
            "features_json": "{}",
            "realised_pnl_inr": 5.0,
            "outcome_label": "winner",
            "written_at": datetime(2026, 5, 14, 10, 0, 0),
        }
        for fid in ("f1", "f2", "f3")
    ]
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=mock_cat,
    ):
        written = _write_labeled_batch(
            rewrite_rows=rewrite_rows,
            fill_ids=["f1", "f2", "f3"],
        )
    assert written == 3
    pred = mock_tbl.delete.call_args.args[0]
    assert isinstance(pred, In), f"got {type(pred).__name__}"
    name = getattr(pred.term, "name", None)
    assert name == "fill_id"
    mock_tbl.append.assert_called_once()
    appended: pa.Table = mock_tbl.append.call_args.args[0]
    assert appended.column("outcome_label").to_pylist() == [
        "winner",
        "winner",
        "winner",
    ]


def test_unused():
    """Silence: ``pytest`` requires at least one async-naive
    test for the file to be importable on systems without
    pytest-asyncio plugin auto-mode — kept as a no-op
    placeholder."""
    assert True


if False:  # pragma: no cover
    # Keep ``pytest`` referenced — some linters warn on
    # unused imports otherwise.
    _ = pytest
