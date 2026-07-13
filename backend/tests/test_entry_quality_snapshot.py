from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.jobs.entry_quality_snapshot import (
    _append_snapshot_rows,
    _run,
)


@pytest.mark.asyncio
async def test_snapshot_job_writes_expected_row_count():
    ohlcv_df = pd.DataFrame(
        {
            "ticker": ["TCS.NS"] * 300,
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "open": [100.0] * 300,
            "high": [101.0] * 300,
            "low": [99.0] * 300,
            "close": [100.0] * 300,
            "volume": [1_000_000.0] * 300,
        }
    )
    nifty_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "close": [100.0] * 300,
        }
    )

    with (
        patch(
            "backend.jobs.entry_quality_snapshot.disposable_pg_session"
        ) as mock_pg,
        patch(
            "backend.jobs.entry_quality_snapshot._full_universe_tickers",
            new_callable=AsyncMock,
            return_value=["TCS.NS"],
        ),
        patch(
            "backend.jobs.entry_quality_snapshot.query_iceberg_df",
            new_callable=AsyncMock,
        ) as mock_query,
        patch(
            "backend.jobs.entry_quality_snapshot._append_snapshot_rows"
        ) as mock_append,
    ):
        # MagicMock base + explicit AsyncMock only on ``execute`` —
        # matches the established PG-session mock convention in
        # test_closed_trades_rollup.py::fake_session. A freshly
        # created ``AsyncMock()``'s own ``.return_value`` is ALSO an
        # AsyncMock by default (cascades recursively), so ``execute``
        # .return_value``.fetchall`` would come back an
        # awaitable coroutine — but the real SQLAlchemy Result
        # object's ``.fetchall()`` is a SYNC method. Explicitly pin
        # ``execute.return_value`` to a plain ``MagicMock`` to break
        # that cascade and match production semantics.
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("TCS.NS",)]
        mock_session.execute.return_value = mock_result
        mock_pg.return_value.__aenter__.return_value = mock_session
        mock_query.side_effect = [ohlcv_df, nifty_df]

        result = await _run({})

    assert result["rows_written"] == 1
    mock_append.assert_called_once()
    # Task 15: qm_score must be a real computed value now, not the
    # None placeholder Task 14 shipped.
    written_rows = mock_append.call_args.args[0]
    assert written_rows[0]["qm_score"] is not None


def test_append_snapshot_rows_scopes_delete_on_ticker_and_trade_date():
    """Re-triggering the job for a ``trade_date`` already written
    MUST NOT duplicate rows — the pre-delete predicate scopes on
    the incoming batch's ``(ticker, trade_date)`` pairs, mirroring
    ``daily_features_daily_compute.py``'s NaN-replaceable upsert
    pattern (idempotency-gap review finding)."""
    import pyarrow as pa
    from pyiceberg.expressions import And

    rows = [
        {
            "trade_date": date(2026, 7, 12),
            "ticker": "TCS.NS",
            "market": "india",
        }
    ]
    arrow_schema = pa.schema(
        [
            ("trade_date", pa.date32()),
            ("ticker", pa.string()),
            ("market", pa.string()),
        ]
    )
    mock_tbl = MagicMock()
    mock_tbl.schema.return_value.as_arrow.return_value = arrow_schema
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    with (
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch("backend.jobs.entry_quality_snapshot.invalidate_metadata"),
    ):
        _append_snapshot_rows(rows)

    mock_tbl.delete.assert_called_once()
    pred = mock_tbl.delete.call_args.args[0]
    assert isinstance(pred, And)

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
    assert "trade_date" in seen_refs, f"got {seen_refs}"
    mock_tbl.append.assert_called_once()


def test_delete_predicate_is_exact_not_cross_product():
    """Regression test for the ticker/trade_date cross-product data
    -loss bug: ``trade_date`` is computed independently per ticker
    (``grp["date"].iloc[-1].date()``), so a batch can legitimately
    contain two tickers with two DIFFERENT trade_dates (e.g. one
    ticker's OHLCV ingestion lagged). The old
    ``And(In(tickers), In(trade_dates))`` predicate would match
    ALL 4 (ticker, trade_date) combinations — including
    (TCS.NS, 2026-07-11) and (INFY.NS, 2026-07-12), neither of
    which is actually in the batch — silently deleting valid
    historical rows. The fixed predicate must match only the 2
    pairs actually written."""
    from backend.jobs.entry_quality_snapshot import _delete_predicate

    d1 = date(2026, 7, 12)
    d2 = date(2026, 7, 11)  # INFY.NS lagged a day behind TCS.NS
    rows = [
        {"trade_date": d1, "ticker": "TCS.NS", "market": "india"},
        {"trade_date": d2, "ticker": "INFY.NS", "market": "india"},
    ]

    pred = _delete_predicate(rows)

    def _matches(p, ticker: str, trade_date: date) -> bool:
        """Evaluate the predicate tree against a single
        (ticker, trade_date) row-shaped dict, mirroring how
        PyIceberg's row-filter evaluation walks And/Or/EqualTo."""
        from pyiceberg.expressions import And, EqualTo, Or

        if isinstance(p, And):
            return _matches(p.left, ticker, trade_date) and _matches(
                p.right, ticker, trade_date
            )
        if isinstance(p, Or):
            return _matches(p.left, ticker, trade_date) or _matches(
                p.right, ticker, trade_date
            )
        if isinstance(p, EqualTo):
            name = p.term.name
            if name == "ticker":
                return ticker == p.literal.value
            if name == "trade_date":
                # DateLiteral.value is epoch-day int, not a
                # date — round-trip through date.fromordinal.
                epoch_days = p.literal.value
                lit_date = date(1970, 1, 1) + timedelta(days=epoch_days)
                return trade_date == lit_date
            raise AssertionError(f"unexpected column {name}")
        raise AssertionError(f"unexpected node {p!r}")

    # The 2 pairs actually in the batch MUST match.
    assert _matches(pred, "TCS.NS", d1)
    assert _matches(pred, "INFY.NS", d2)

    # The cross-product combinations that were NEVER written by
    # any run and are NOT part of this batch MUST NOT match — this
    # is the exact case the old In()xIn() predicate got wrong.
    assert not _matches(pred, "TCS.NS", d2)
    assert not _matches(pred, "INFY.NS", d1)


def test_delete_predicate_none_for_empty_rows():
    """Zero rows must short-circuit to ``None`` (no-op delete) —
    the caller (``_run``) already guards ``if rows:`` before
    calling ``_append_snapshot_rows``, but ``_delete_predicate``
    itself must not crash or build a vacuous predicate."""
    from backend.jobs.entry_quality_snapshot import _delete_predicate

    assert _delete_predicate([]) is None


def test_delete_predicate_single_pair_no_or_wrapper():
    """A single (ticker, trade_date) pair must not be wrapped in an
    ``Or`` node — just the bare ``And(EqualTo, EqualTo)``."""
    from pyiceberg.expressions import And, Or

    from backend.jobs.entry_quality_snapshot import _delete_predicate

    rows = [
        {"trade_date": date(2026, 7, 12), "ticker": "TCS.NS"},
    ]
    pred = _delete_predicate(rows)
    assert isinstance(pred, And)
    assert not isinstance(pred, Or)


@pytest.mark.asyncio
async def test_snapshot_job_uses_detect_market_not_hardcoded_india():
    """``market`` MUST come from ``detect_market(ticker)`` (CLAUDE.md
    §4.3 #19) rather than a hardcoded ``"india"`` literal — a US
    ticker in the allowed-tickers universe must be tagged ``"us"``."""
    ohlcv_df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * 300,
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "open": [100.0] * 300,
            "high": [101.0] * 300,
            "low": [99.0] * 300,
            "close": [100.0] * 300,
            "volume": [1_000_000.0] * 300,
        }
    )
    nifty_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "close": [100.0] * 300,
        }
    )

    with (
        patch(
            "backend.jobs.entry_quality_snapshot.disposable_pg_session"
        ) as mock_pg,
        patch(
            "backend.jobs.entry_quality_snapshot._full_universe_tickers",
            new_callable=AsyncMock,
            return_value=["AAPL"],
        ),
        patch(
            "backend.jobs.entry_quality_snapshot.query_iceberg_df",
            new_callable=AsyncMock,
        ) as mock_query,
        patch(
            "backend.jobs.entry_quality_snapshot._append_snapshot_rows"
        ) as mock_append,
    ):
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("AAPL",)]
        mock_session.execute.return_value = mock_result
        mock_pg.return_value.__aenter__.return_value = mock_session
        mock_query.side_effect = [ohlcv_df, nifty_df]

        await _run({})

    written_rows = mock_append.call_args.args[0]
    assert written_rows[0]["market"] == "us"


def _linear_ohlcv(ticker: str, start: float, slope: float, wobble: float):
    """Build a 300-bar synthetic OHLCV frame for *ticker* with a
    linear close-price trend (``start + slope * i``) plus a small
    alternating +/-``wobble`` daily oscillation (so pct-change std is
    non-zero and Sharpe is computable), and OHLC bands derived from
    each day's close."""
    n = 300
    closes = [
        max(start + slope * i + (wobble if i % 2 == 0 else -wobble), 1.0)
        for i in range(n)
    ]
    return pd.DataFrame(
        {
            "ticker": [ticker] * n,
            "date": pd.date_range("2025-01-01", periods=n, freq="D"),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * n,
        }
    )


@pytest.mark.asyncio
async def test_qualifying_discovers_ticker_outside_allowed():
    """The actual bug this task fixes: before this change, this job's
    OHLCV fetch (and therefore ``compute_qm_scores``) was scoped to
    ``allowed_tickers`` only, so ``qualifying = allowed | {...QM >=
    58...}`` could NEVER pick up a ticker outside ``allowed`` — QM
    Score was never even computed for anything else. This test builds
    a full-universe candidate set of TWO tickers where only
    ``TCS.NS`` is in ``allowed_tickers``, but ``INFY.NS`` (a strong,
    low-drawdown uptrend) clears QM Score >= 58 in the full-universe
    cohort while ``TCS.NS`` (a declining, high-drawdown trend) does
    not. ``INFY.NS`` must appear in the persisted rows with
    ``in_allowed_tickers=False`` — pre-change, the OHLCV query would
    never have included INFY.NS at all (it isn't in ``allowed``), so
    this assertion would have failed with a KeyError/empty result.
    """
    tcs_df = _linear_ohlcv("TCS.NS", start=200.0, slope=-0.3, wobble=5.0)
    infy_df = _linear_ohlcv("INFY.NS", start=100.0, slope=0.6, wobble=3.0)
    ohlcv_df = pd.concat([tcs_df, infy_df], ignore_index=True)
    nifty_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="D"),
            "close": [100.0] * 300,
        }
    )

    with (
        patch(
            "backend.jobs.entry_quality_snapshot.disposable_pg_session"
        ) as mock_pg,
        patch(
            "backend.jobs.entry_quality_snapshot._full_universe_tickers",
            new_callable=AsyncMock,
            return_value=["TCS.NS", "INFY.NS"],
        ) as mock_universe,
        patch(
            "backend.jobs.entry_quality_snapshot.query_iceberg_df",
            new_callable=AsyncMock,
        ) as mock_query,
        patch(
            "backend.jobs.entry_quality_snapshot._append_snapshot_rows"
        ) as mock_append,
    ):
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        # Only TCS.NS is on a live strategy's allowed_tickers list.
        mock_result.fetchall.return_value = [("TCS.NS",)]
        mock_session.execute.return_value = mock_result
        mock_pg.return_value.__aenter__.return_value = mock_session
        mock_query.side_effect = [ohlcv_df, nifty_df]

        result = await _run({})

    # Full-universe fetch actually happened.
    mock_universe.assert_awaited_once()

    # The OHLCV batch fetch was scoped to the allowed UNION
    # full-universe candidate set — both tickers, not just `allowed`.
    ohlcv_sql = mock_query.call_args_list[0].args[1]
    assert "'TCS.NS'" in ohlcv_sql
    assert "'INFY.NS'" in ohlcv_sql

    written_rows = mock_append.call_args.args[0]
    by_ticker = {r["ticker"]: r for r in written_rows}

    assert result["rows_written"] == len(written_rows)
    assert "INFY.NS" in by_ticker, (
        "INFY.NS was never allowed_tickers-scoped and must only be "
        "discoverable via full-universe QM Score >= 58 — pre-change "
        "this job's OHLCV fetch never included it at all."
    )
    infy_row = by_ticker["INFY.NS"]
    assert infy_row["in_allowed_tickers"] is False
    assert infy_row["qm_score"] is not None
    assert infy_row["qm_score"] >= 58

    # TCS.NS is still present (it's in allowed_tickers regardless of
    # its own QM Score) but its declining/high-drawdown profile
    # should score well below the >=58 bar in this 2-ticker cohort —
    # confirms the union is genuine, not "everything qualifies".
    tcs_row = by_ticker["TCS.NS"]
    assert tcs_row["in_allowed_tickers"] is True
    assert tcs_row["qm_score"] is not None
    assert tcs_row["qm_score"] < 58
