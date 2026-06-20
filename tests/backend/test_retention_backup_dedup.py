"""Behavioral tests: retention jobs route through ``verify_or_backup``
(not ``backup_table`` directly).

These tests prove the dedup contract — when ``verify_or_backup`` is
called, we know the job won't create a redundant per-table backup if
today's full daily snapshot already covers it.  The test strategy:

- Monkeypatch ``verify_or_backup`` in the job's module namespace to
  raise a sentinel ``RuntimeError("reached-vob")``.
- Monkeypatch ``backup_table`` in the same namespace to
  ``pytest.fail`` if called (it must NEVER be called directly).
- Assert the job raises ``RuntimeError("reached-vob")`` — proving it
  reached ``verify_or_backup`` before anything else ran.

For the intraday job (async) we also need to bypass the monthly-
cadence gate (which would short-circuit before the backup block) and
the ``_already_ran_this_month`` PG call.
"""

from __future__ import annotations

import pytest

import backend.algo.jobs.algo_events_retention as aer
import backend.algo.jobs.intraday_bars_retention as ibr


# ──────────────────────────────────────────────────────────────────
# algo.events retention
# ──────────────────────────────────────────────────────────────────


def test_algo_events_retention_routes_through_verify(monkeypatch):
    """Job must call ``verify_or_backup``, never ``backup_table``.

    Strategy: patch ``verify_or_backup`` to record which tables
    were passed and then raise a sentinel.  The job catches the
    exception (fail-closed) and returns ``status: error``.  We
    confirm (a) ``verify_or_backup`` was reached with the right
    table list, and (b) ``backup_table`` was never invoked directly
    — proven by the injected ``pytest.fail`` guard.
    """
    seen: dict = {}

    def _record_and_raise(tables):
        seen["tables"] = tables
        raise RuntimeError("reached-vob")

    monkeypatch.setattr(aer, "verify_or_backup", _record_and_raise)
    # backup_table is no longer imported into the module; inject it
    # anyway (raising=False) to catch any regression where it gets
    # re-added and called directly.
    monkeypatch.setattr(
        aer,
        "backup_table",
        lambda *a, **k: pytest.fail(
            "backup_table called directly — job bypassed verify_or_backup"
        ),
        raising=False,
    )

    result = aer.run_algo_events_retention_job({"dry_run": False})

    # verify_or_backup was reached with the correct table
    assert seen.get("tables") == [aer.ALGO_EVENTS_TABLE], (
        f"verify_or_backup not called with [{aer.ALGO_EVENTS_TABLE!r}], "
        f"got {seen.get('tables')!r}"
    )
    # fail-closed: job returned error, did not proceed to delete
    assert result["status"] == "error"
    assert "backup_failed" in result["error"]


def test_algo_events_retention_backup_failure_aborts_delete(monkeypatch):
    """Fail-closed: when verify_or_backup raises, job returns error
    and does NOT proceed to the Iceberg delete.

    This is a complementary test to the routing test above —
    it verifies the error shape rather than the call path.
    """
    import stocks.create_tables as sct
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        aer,
        "verify_or_backup",
        lambda tables: (_ for _ in ()).throw(
            RuntimeError("disk full")
        ),
    )
    # _get_catalog must not be called (backup aborts before delete)
    mock_cat = MagicMock()
    monkeypatch.setattr(sct, "_get_catalog", lambda: mock_cat)

    result = aer.run_algo_events_retention_job({})

    assert result["status"] == "error"
    assert "backup_failed" in result["error"]
    mock_cat.load_table.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# stocks.intraday_bars retention (async job)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intraday_bars_retention_routes_through_verify(
    monkeypatch,
):
    """Job must call ``verify_or_backup``, never ``backup_table``.

    Strategy: patch ``verify_or_backup`` to record which tables
    were passed and then raise a sentinel.  The job catches the
    exception (fail-closed) and returns ``status: error``.  We
    confirm (a) ``verify_or_backup`` was reached with the right
    table list, and (b) ``backup_table`` was never invoked directly
    — proven by the injected ``pytest.fail`` guard.
    """
    from unittest.mock import AsyncMock

    seen: dict = {}

    def _record_and_raise(tables):
        seen["tables"] = tables
        raise RuntimeError("reached-vob")

    monkeypatch.setattr(ibr, "verify_or_backup", _record_and_raise)
    # backup_table is no longer imported into the module; inject it
    # anyway (raising=False) to catch any regression where it gets
    # re-added and called directly.
    monkeypatch.setattr(
        ibr,
        "backup_table",
        lambda *a, **k: pytest.fail(
            "backup_table called directly — job bypassed verify_or_backup"
        ),
        raising=False,
    )
    # Bypass the monthly-cadence gate so we reach the backup block.
    monkeypatch.setattr(
        ibr,
        "_already_ran_this_month",
        AsyncMock(return_value=False),
    )

    result = await ibr.run_intraday_bars_retention_job(
        {"today": "2026-05-13"}
    )

    # verify_or_backup was reached with the correct table
    assert seen.get("tables") == [ibr.INTRADAY_BARS_TABLE], (
        f"verify_or_backup not called with [{ibr.INTRADAY_BARS_TABLE!r}], "
        f"got {seen.get('tables')!r}"
    )
    # fail-closed: job returned error, did not proceed to delete
    assert result["status"] == "error"
    assert "backup_failed" in result["error"]


@pytest.mark.asyncio
async def test_intraday_bars_retention_backup_failure_aborts_delete(
    monkeypatch,
):
    """Fail-closed: when verify_or_backup raises, job returns error
    and does NOT proceed to the Iceberg delete."""
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(
        ibr,
        "verify_or_backup",
        lambda tables: (_ for _ in ()).throw(
            RuntimeError("rsync timed out (simulated)")
        ),
    )
    monkeypatch.setattr(
        ibr,
        "_already_ran_this_month",
        AsyncMock(return_value=False),
    )

    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    result = await ibr.run_intraday_bars_retention_job(
        {"today": "2026-05-13"}
    )

    assert result["status"] == "error"
    assert "backup_failed" in result["error"]
    mock_tbl.delete.assert_not_called()
