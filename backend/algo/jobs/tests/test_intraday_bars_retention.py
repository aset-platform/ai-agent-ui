"""Tests for ``intraday_bars_retention`` (ASETPLTFRM-400 slice 1g).

Covers:
- Cutoff math (default 4 years, override, leap-year edge).
- Delete predicate is ``LessThan("bar_date", cutoff_iso)``.
- Iceberg roundtrip against the real local catalog: seed bars
  across the cutoff boundary, run the retention job, verify
  only post-cutoff rows survive.
- Idempotent re-run is a no-op.
- Sync + pipeline-compatible wrapper bridges to the async job.
- JOB_EXECUTORS registration.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.backtest.intraday_backfill import (
    upsert_intraday_bars,
)
from backend.algo.backtest.types import BarData
from backend.algo.jobs.intraday_bars_retention import (
    DEFAULT_RETENTION_YEARS,
    INTRADAY_BARS_TABLE,
    _retention_cutoff,
    run_intraday_bars_retention_job,
)

_TEST_TICKER_PREFIX = "RTN_"


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _stub_backup_table(monkeypatch):
    """Skip the real rsync from ``backup_table()`` during unit
    tests — we only want to exercise retention logic. The
    backup-gate behaviour itself is tested explicitly below
    with a failing stub."""
    from backend.algo.jobs import intraday_bars_retention as mod

    monkeypatch.setattr(
        mod,
        "backup_table",
        lambda table_id, **_kw: f"/tmp/fake-backup-{table_id}",
    )


@pytest.fixture(autouse=True)
def _ensure_table_and_clean_state():
    """Ensure ``stocks.intraday_bars`` is registered and wipe any
    ``RTN_`` test rows from previous runs."""
    from pyiceberg.expressions import StartsWith

    from stocks.create_tables import (
        _INTRADAY_BARS_TABLE,
        _create_table,
        _get_catalog,
        _intraday_bars_schema,
        _ticker_bar_date_partition_spec,
    )

    catalog = _get_catalog()
    schema = _intraday_bars_schema()
    _create_table(
        catalog,
        _INTRADAY_BARS_TABLE,
        schema,
        _ticker_bar_date_partition_spec(schema),
    )
    tbl = catalog.load_table(_INTRADAY_BARS_TABLE)
    try:
        tbl.delete(StartsWith("ticker", _TEST_TICKER_PREFIX))
    except Exception:
        pass
    from backend.db.duckdb_engine import invalidate_metadata

    invalidate_metadata(_INTRADAY_BARS_TABLE)
    yield


def _bar(ticker, day, hour=9, minute=15, close=100.0):
    from datetime import timedelta as _td

    ist = timezone(_td(minutes=330))
    open_dt = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=ist,
    )
    ns = int(
        open_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
    )
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal(str(close - 0.5)),
        high=Decimal(str(close + 0.5)),
        low=Decimal(str(close - 1.0)),
        close=Decimal(str(close)),
        volume=10,
        bar_open_ts_ns=ns,
    )


# ────────────────────────────────────────────────────────────────
# Cutoff math
# ────────────────────────────────────────────────────────────────


def test_cutoff_default_four_years():
    assert _retention_cutoff(date(2026, 5, 13)) == date(
        2022,
        5,
        13,
    )


def test_cutoff_year_override():
    assert _retention_cutoff(
        date(2026, 5, 13),
        years=2,
    ) == date(2024, 5, 13)


def test_cutoff_leap_day_to_leap_year_keeps_feb_29():
    """2024-02-29 minus 4 years → 2020-02-29 (also a leap year)."""
    assert _retention_cutoff(
        date(2024, 2, 29),
    ) == date(2020, 2, 29)


def test_cutoff_leap_day_to_non_leap_year_clamps_to_feb_28():
    """2024-02-29 minus 1 year → 2023 (non-leap) so we cap to
    Feb 28 rather than crash."""
    assert _retention_cutoff(
        date(2024, 2, 29),
        years=1,
    ) == date(2023, 2, 28)


def test_default_retention_years_is_four():
    assert DEFAULT_RETENTION_YEARS == 4


# ────────────────────────────────────────────────────────────────
# Delete predicate
# ────────────────────────────────────────────────────────────────


async def test_delete_predicate_is_less_than_cutoff_iso():
    """The Iceberg delete must be ``LessThan("bar_date", iso)``
    — the YYYY-MM-DD string format makes lexicographic ordering
    match chronological."""
    from pyiceberg.expressions import LessThan

    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_retention." "invalidate_metadata",
        ),
    ):
        result = await run_intraday_bars_retention_job(
            {"today": "2026-05-13", "years": 4},
        )

    assert result["status"] == "ok"
    assert result["cutoff"] == "2022-05-13"
    assert result["backup_path"] is not None
    # tbl.delete invoked exactly once with LessThan(bar_date, ...)
    mock_tbl.delete.assert_called_once()
    call_arg = mock_tbl.delete.call_args.args[0]
    assert isinstance(call_arg, LessThan)
    assert call_arg.term.name == "bar_date"
    assert call_arg.literal.value == "2022-05-13"


async def test_backup_failure_aborts_delete(monkeypatch):
    """If the pre-delete backup raises, the retention job MUST
    NOT issue the Iceberg delete — fail-closed contract."""
    from backend.algo.jobs import intraday_bars_retention as mod

    def _bad_backup(table_id, **_kw):
        raise RuntimeError("rsync timed out (simulated)")

    monkeypatch.setattr(mod, "backup_table", _bad_backup)

    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl
    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_retention." "invalidate_metadata",
        ),
    ):
        result = await run_intraday_bars_retention_job(
            {"today": "2026-05-13"},
        )

    assert result["status"] == "error"
    assert "backup_failed" in result["error"]
    # The Iceberg delete must NOT have been called.
    mock_tbl.delete.assert_not_called()


async def test_skip_backup_payload_bypasses_safety_gate():
    """Operators can disable the backup gate for ad-hoc runs
    via ``payload={"skip_backup": True}``. The delete still
    runs; ``backup_path`` is ``None`` in the response."""
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl
    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_retention." "invalidate_metadata",
        ),
        patch(
            "backend.algo.jobs.intraday_bars_retention." "backup_table",
        ) as mock_backup,
    ):
        result = await run_intraday_bars_retention_job(
            {"today": "2026-05-13", "skip_backup": True},
        )
    assert result["status"] == "ok"
    assert result["backup_path"] is None
    mock_backup.assert_not_called()
    mock_tbl.delete.assert_called_once()


# ────────────────────────────────────────────────────────────────
# Iceberg roundtrip
# ────────────────────────────────────────────────────────────────


async def test_retention_removes_only_pre_cutoff_rows():
    """Seed rows on both sides of the cutoff; only the
    post-cutoff rows must survive."""
    from backend.db.duckdb_engine import query_iceberg_table

    bars = [
        _bar("RTN_A.NS", date(2020, 1, 1)),  # pre-cutoff
        _bar("RTN_A.NS", date(2021, 12, 31)),  # pre-cutoff
        _bar("RTN_A.NS", date(2022, 5, 13)),  # exactly cutoff (kept)
        _bar("RTN_A.NS", date(2023, 6, 1)),  # post-cutoff
        _bar("RTN_A.NS", date(2025, 5, 13)),  # post-cutoff
    ]
    upsert_intraday_bars(bars, interval_sec=900, source="seed")

    result = await run_intraday_bars_retention_job(
        {"today": "2026-05-13"},
    )
    assert result["status"] == "ok"
    assert result["cutoff"] == "2022-05-13"

    rows = query_iceberg_table(
        INTRADAY_BARS_TABLE,
        "SELECT bar_date FROM intraday_bars "
        "WHERE ticker = ? ORDER BY bar_date",
        ["RTN_A.NS"],
    )
    kept = {r["bar_date"] for r in rows}
    assert kept == {"2022-05-13", "2023-06-01", "2025-05-13"}


async def test_retention_idempotent_rerun_is_noop():
    """Re-running after the cutoff has already advanced matches
    zero rows — verify the second call still returns ``ok`` and
    the table contents are unchanged."""
    from backend.db.duckdb_engine import query_iceberg_table

    bars = [_bar("RTN_IDM.NS", date(2025, 5, 13))]
    upsert_intraday_bars(bars, interval_sec=900, source="seed")

    r1 = await run_intraday_bars_retention_job(
        {"today": "2026-05-13"},
    )
    r2 = await run_intraday_bars_retention_job(
        {"today": "2026-05-13"},
    )
    assert r1["status"] == "ok"
    assert r2["status"] == "ok"

    rows = query_iceberg_table(
        INTRADAY_BARS_TABLE,
        "SELECT COUNT(*) AS c FROM intraday_bars " "WHERE ticker = ?",
        ["RTN_IDM.NS"],
    )
    assert rows[0]["c"] == 1


async def test_retention_empty_table_returns_ok():
    """No-row table → delete matches nothing → status ``ok``."""
    result = await run_intraday_bars_retention_job(
        {"today": "2026-05-13"},
    )
    assert result["status"] == "ok"
    assert result["cutoff"] == "2022-05-13"


# ────────────────────────────────────────────────────────────────
# Pipeline-wrapper wiring
# ────────────────────────────────────────────────────────────────


def test_register_job_dispatch_wiring():
    """``intraday_bars_retention`` must be in JOB_EXECUTORS so
    the pipeline executor can chain it as step 2."""
    from backend.jobs.executor import JOB_EXECUTORS

    assert "intraday_bars_retention" in JOB_EXECUTORS


def test_register_job_wrapper_is_pipeline_compatible():
    """Sync wrapper with the standard pipeline-step signature.
    Async-only would silent-succeed."""
    import inspect

    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["intraday_bars_retention"]
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


def test_register_job_wrapper_runs_async_job_via_asyncio():
    """Sync wrapper must drive the async job to a return value."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["intraday_bars_retention"]

    async def _fake_async_job(payload):
        return {
            "status": "ok",
            "payload_seen": payload,
            "cutoff": "2022-05-13",
        }

    with patch(
        "backend.algo.jobs.intraday_bars_retention."
        "run_intraday_bars_retention_job",
        new=_fake_async_job,
    ):
        result = fn(
            scope=None,
            run_id="abc",
            repo=None,
            cancel_event=None,
            force=False,
            payload={"today": "2026-05-13"},
        )
    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["payload_seen"] == {"today": "2026-05-13"}
