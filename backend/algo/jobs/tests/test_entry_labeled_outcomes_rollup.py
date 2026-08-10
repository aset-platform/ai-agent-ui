"""Tests for ``entry_labeled_outcomes_rollup`` (PRE-6).

Targets the pure ``_assemble_rows``/``_aggregate_fills`` helpers per
the design tip in the task brief for (a)-(c); (d) idempotency has
TWO layers: a pure-helper check (calling ``_assemble_rows`` twice
with identical inputs) AND a real integration test against the
Docker Postgres (``TestRunUpsertEnrichmentSurvives`` below) that
exercises the actual ``_UPSERT_SQL`` text — the pure-helper check
alone is tautological for upsert correctness since it never touches
SQL. The integration tests are the regression guard for the
2026-08-10 data-loss bug: a straight ``EXCLUDED.<col>`` assignment
on feature/QM/ESS columns nulled all 70 PRE-1-reconstructed rows on
this job's very first real run, because a fill with no matching
``entry_strength_snapshot`` has NULL ``EXCLUDED.<feature>``.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from backend.algo.jobs.entry_labeled_outcomes_rollup import (
    _aggregate_fills,
    _assemble_rows,
    _bare_ticker,
    _compute_outcomes,
    _run,
    _scale_pct,
    _to_float,
    _to_ns_ticker,
)

_STRAT = "5c4aa66f-887c-44d6-a945-cefd4feec926"
_USER = "60d30496-acb9-4530-8a4c-cf774c4934f4"
_DAY = date(2026, 8, 10)
_MODULE = "backend.algo.jobs.entry_labeled_outcomes_rollup"

# A ticker that can never collide with real candidate data — the
# integration tests below seed/upsert/delete rows scoped to exactly
# this (strategy_id, ticker, trade_date) key against the real Docker
# Postgres, using the real _STRAT (FK-valid) so no schema constraint
# stands in the way, and clean up unconditionally in a finally block.
_QA_TICKER = "ZZQATEST"
_QA_DATE = date(2020, 1, 1)


def _snapshot(**overrides):
    base = {
        "user_id": _USER,
        "signal_ts_ns": 1786363450365483008,
        "trigger": "intraday_forming",
        "rsi2_at_entry": 4.15,
        "dist_sma50_pct": 5.34,
        "dist_sma200_pct": 22.90,
        "ret_3d_prior": -3.07,
        "gap_pct": -0.41,
        "breadth_oversold": 15,
        "breadth_total": 266,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _fill(**overrides):
    base = {
        "user_id": _USER,
        "strategy_id": _STRAT,
        "mode": "live",
        "ticker": "NYKAA",
        "trade_date": _DAY,
        "qty": 12,
        "entry_price": 100.0,
        "exit_price": 105.0,
        "realised_pnl_inr": 60.0,
        "return_pct": 5.0,
        "opened_at_ts_ns": 1000,
        "closed_at_ts_ns": 2000,
        "closed_at": _DAY,
        "exit_reason": "signal",
        "buy_event_id": "buy-1",
        "sell_event_id": "sell-1",
        "dry_run": False,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- #
# _scale_pct / _to_float / ticker helpers
# --------------------------------------------------------------- #


def test_scale_pct_converts_fraction_to_percent():
    assert _scale_pct(0.2555) == 25.55


def test_scale_pct_handles_decimal_string_payload():
    # entry_strength_snapshot payloads round-trip decimals as
    # strings via json.dumps(default=str).
    assert round(_scale_pct("0.05342975317303809664439"), 4) == (
        5.3430
    )


def test_to_float_none_and_nan_safe():
    assert _to_float(None) is None
    assert _to_float("not-a-number") is None
    assert _to_float(float("nan")) is None
    assert _to_float("3.5") == 3.5


def test_bare_and_ns_ticker_roundtrip():
    assert _bare_ticker("NYKAA.NS") == "NYKAA"
    assert _bare_ticker("KTKBANK") == "KTKBANK"
    assert _to_ns_ticker("NYKAA") == "NYKAA.NS"
    assert _to_ns_ticker("NYKAA.NS") == "NYKAA.NS"


# --------------------------------------------------------------- #
# (a) filled single-lot candidate
# --------------------------------------------------------------- #


def test_assemble_rows_filled_single_lot_real_settled_row():
    key = (_STRAT, "live", "NYKAA", _DAY)
    snapshots = {key: _snapshot()}
    fills = [_fill()]

    rows = _assemble_rows(snapshots, {}, fills, {}, {})

    assert len(rows) == 1
    row = rows[0]
    assert row["filled"] is True
    assert row["outcome_kind"] == "real"
    assert row["outcome_settled"] is True
    assert row["label_win"] is True
    assert row["rejection_reason"] is None
    assert row["realised_pnl_inr"] == 60.0
    assert row["entry_price"] == 100.0
    assert row["exit_price"] == 105.0
    # Feature scaling already applied at the fetch layer — the
    # snapshot's dist_sma50_pct flows through unchanged.
    assert row["dist_sma50_pct"] == 5.34
    assert row["dist_sma200_pct"] == 22.90
    assert row["ret_1d_prior"] is None


def test_assemble_rows_filled_loss_labels_win_false():
    key = (_STRAT, "live", "NYKAA", _DAY)
    snapshots = {key: _snapshot()}
    fills = [_fill(realised_pnl_inr=-10.0)]

    rows = _assemble_rows(snapshots, {}, fills, {}, {})

    assert rows[0]["label_win"] is False


def test_assemble_rows_joins_qm_ess_and_mfe_mae():
    key = (_STRAT, "live", "NYKAA", _DAY)
    snapshots = {key: _snapshot()}
    fills = [_fill()]
    eqd = {
        ("NYKAA", _DAY): {
            "qm_score": 61.2,
            "ess_score": 70.0,
            "ess_gate_passed": True,
            "qm_mdd_pctile": 40.0,
            "qm_rs_pctile": 55.0,
            "qm_sharpe_pctile": 65.0,
            "ess_absorption_volume_score": 0.8,
            "ess_selling_deceleration_score": 0.6,
            "ess_trend_stability_score": 0.9,
        },
    }
    outcomes = {
        key: {
            "mfe_pct": 8.0,
            "mae_pct": -2.0,
            "outcome_src": "intraday15m",
        },
    }

    rows = _assemble_rows(snapshots, {}, fills, eqd, outcomes)

    row = rows[0]
    assert row["qm_score"] == 61.2
    assert row["ess_gate_passed"] is True
    assert row["mfe_pct"] == 8.0
    assert row["mae_pct"] == -2.0
    assert row["outcome_src"] == "intraday15m"


# --------------------------------------------------------------- #
# (b) multi-lot same-day fill aggregation
# --------------------------------------------------------------- #


def test_aggregate_fills_multi_lot_ktkbank_style():
    """Mirrors the real KTKBANK 2026-06-24 x3-lot case in the dev
    DB: same buy_event_id across lots, distinct sell_event_ids,
    distinct closed_at dates, aggregated into ONE row."""
    lots = [
        {
            "user_id": _USER, "strategy_id": _STRAT, "mode": "live",
            "ticker": "KTKBANK", "qty": 1, "avg_price": 266.3,
            "fill_price": 267.0,
            "opened_at": date(2026, 6, 24),
            "closed_at": date(2026, 6, 25),
            "opened_at_ts_ns": 1782291003985060096,
            "closed_at_ts_ns": 1782358657695329792,
            "realised_pnl_inr": 0.70, "exit_reason": "signal",
            "dry_run": False, "buy_event_id": "buy-ktk",
            "sell_event_id": "sell-1",
        },
        {
            "user_id": _USER, "strategy_id": _STRAT, "mode": "live",
            "ticker": "KTKBANK", "qty": 1, "avg_price": 266.3,
            "fill_price": 267.0,
            "opened_at": date(2026, 6, 24),
            "closed_at": date(2026, 6, 25),
            "opened_at_ts_ns": 1782291003985060096,
            "closed_at_ts_ns": 1782358657777071872,
            "realised_pnl_inr": 0.70, "exit_reason": "signal",
            "dry_run": False, "buy_event_id": "buy-ktk",
            "sell_event_id": "sell-2",
        },
        {
            "user_id": _USER, "strategy_id": _STRAT, "mode": "live",
            "ticker": "KTKBANK", "qty": 14, "avg_price": 266.3,
            "fill_price": 270.15,
            "opened_at": date(2026, 6, 24),
            "closed_at": date(2026, 7, 2),
            "opened_at_ts_ns": 1782291003985060096,
            "closed_at_ts_ns": 1782969417911209216,
            "realised_pnl_inr": 53.90, "exit_reason": "user_exit",
            "dry_run": False, "buy_event_id": "buy-ktk",
            "sell_event_id": "sell-3",
        },
    ]

    out = _aggregate_fills(lots)

    assert len(out) == 1
    row = out[0]
    assert row["qty"] == 16
    assert round(row["entry_price"], 4) == 266.30
    assert round(row["exit_price"], 4) == 269.7562
    assert round(row["realised_pnl_inr"], 2) == 55.30
    assert round(row["return_pct"], 4) == round(
        (269.7563 / 266.3 - 1) * 100, 4,
    )
    assert row["opened_at_ts_ns"] == 1782291003985060096
    assert row["closed_at_ts_ns"] == 1782969417911209216
    assert row["closed_at"] == date(2026, 7, 2)
    # Last-to-close lot's exit_reason/buy/sell ids win per spec.
    assert row["exit_reason"] == "user_exit"
    assert row["buy_event_id"] == "buy-ktk"
    assert row["sell_event_id"] == "sell-1"  # first to close
    assert row["trade_date"] == date(2026, 6, 24)


def test_aggregate_fills_single_lot_passthrough():
    lots = [
        {
            "user_id": _USER, "strategy_id": _STRAT, "mode": "paper",
            "ticker": "SANSERA", "qty": 5, "avg_price": 50.0,
            "fill_price": 55.0,
            "opened_at": date(2026, 6, 27),
            "closed_at": date(2026, 6, 27),
            "opened_at_ts_ns": 100, "closed_at_ts_ns": 200,
            "realised_pnl_inr": 25.0, "exit_reason": "time_stop",
            "dry_run": True, "buy_event_id": "b1",
            "sell_event_id": "s1",
        },
    ]

    out = _aggregate_fills(lots)

    assert len(out) == 1
    assert out[0]["qty"] == 5
    assert out[0]["entry_price"] == 50.0
    assert out[0]["exit_price"] == 55.0
    assert out[0]["dry_run"] is True


# --------------------------------------------------------------- #
# (c) snapshot-only rejected candidate
# --------------------------------------------------------------- #


def test_assemble_rows_rejected_candidate_unsettled():
    key = (_STRAT, "live", "NYKAA", _DAY)
    snapshots = {key: _snapshot()}
    rejections = {key: "ticker_not_allowed"}

    rows = _assemble_rows(snapshots, rejections, [], {}, {})

    assert len(rows) == 1
    row = rows[0]
    assert row["filled"] is False
    assert row["rejection_reason"] == "ticker_not_allowed"
    assert row["outcome_kind"] == "counterfactual"
    assert row["outcome_settled"] is False
    assert row["label_win"] is None
    assert row["realised_pnl_inr"] is None
    assert row["entry_price"] is None
    assert row["mfe_pct"] is None
    assert row["outcome_src"] is None


def test_assemble_rows_snapshot_without_rejection_still_unsettled():
    """A snapshot with no matching fill and no signal_rejected
    event (e.g. the strategy simply never re-evaluated) still
    materializes as an unsettled counterfactual candidate."""
    key = (_STRAT, "live", "NYKAA", _DAY)
    snapshots = {key: _snapshot()}

    rows = _assemble_rows(snapshots, {}, [], {}, {})

    assert len(rows) == 1
    assert rows[0]["filled"] is False
    assert rows[0]["rejection_reason"] is None
    assert rows[0]["outcome_settled"] is False


def test_assemble_rows_fill_without_snapshot_still_materializes():
    """Per the module docstring: a fill with no snapshot (e.g.
    pre-Release-2 history) still produces a row with NULL
    features rather than being dropped."""
    fills = [_fill(ticker="OLDTICKER")]

    rows = _assemble_rows({}, {}, fills, {}, {})

    assert len(rows) == 1
    row = rows[0]
    assert row["filled"] is True
    assert row["trigger"] is None
    assert row["dist_sma50_pct"] is None


def test_assemble_rows_skips_row_with_no_user_id():
    key = (_STRAT, "live", "NYKAA", _DAY)
    snapshots = {key: _snapshot(user_id=None)}

    rows = _assemble_rows(snapshots, {}, [], {}, {})

    assert rows == []


# --------------------------------------------------------------- #
# (d) idempotency
# --------------------------------------------------------------- #


def test_assemble_rows_idempotent_across_repeated_calls():
    key1 = (_STRAT, "live", "NYKAA", _DAY)
    key2 = (_STRAT, "live", "JSFB", _DAY)
    snapshots = {
        key1: _snapshot(),
        key2: _snapshot(user_id=_USER, trigger="yesterday_close"),
    }
    rejections = {key2: "ticker_not_allowed"}
    fills = [_fill()]

    rows_first = _assemble_rows(snapshots, rejections, fills, {}, {})
    rows_second = _assemble_rows(
        snapshots, rejections, fills, {}, {},
    )

    assert rows_first == rows_second
    assert len(rows_first) == 2


# --------------------------------------------------------------- #
# _compute_outcomes (MFE/MAE) — I/O mocked, math verified
# --------------------------------------------------------------- #


def test_compute_outcomes_intraday_preferred_over_daily():
    fill = _fill(
        entry_price=100.0, opened_at_ts_ns=1000, closed_at_ts_ns=2000,
    )
    intraday = {
        "NYKAA.NS": [
            {"bar_open_ts_ns": 1500, "high": 108.0, "low": 95.0},
        ],
    }
    with (
        patch(
            "backend.algo.jobs.entry_labeled_outcomes_rollup."
            "_fetch_intraday_bars",
            return_value=intraday,
        ),
        patch(
            "backend.algo.jobs.entry_labeled_outcomes_rollup."
            "_fetch_daily_ohlcv",
            return_value={},
        ),
    ):
        out = _compute_outcomes([fill])

    key = (_STRAT, "live", "NYKAA", _DAY)
    assert out[key]["outcome_src"] == "intraday15m"
    assert round(out[key]["mfe_pct"], 6) == 8.0
    assert round(out[key]["mae_pct"], 6) == -5.0


def test_compute_outcomes_falls_back_to_daily():
    fill = _fill(entry_price=100.0)
    with (
        patch(
            "backend.algo.jobs.entry_labeled_outcomes_rollup."
            "_fetch_intraday_bars",
            return_value={},
        ),
        patch(
            "backend.algo.jobs.entry_labeled_outcomes_rollup."
            "_fetch_daily_ohlcv",
            return_value={
                "NYKAA.NS": [
                    {"date": _DAY, "high": 110.0, "low": 90.0},
                ],
            },
        ),
    ):
        out = _compute_outcomes([fill])

    key = (_STRAT, "live", "NYKAA", _DAY)
    assert out[key]["outcome_src"] == "daily"
    assert round(out[key]["mfe_pct"], 6) == 10.0
    assert round(out[key]["mae_pct"], 6) == -10.0


def test_compute_outcomes_none_when_no_bars_found():
    fill = _fill(entry_price=100.0)
    with (
        patch(
            "backend.algo.jobs.entry_labeled_outcomes_rollup."
            "_fetch_intraday_bars",
            return_value={},
        ),
        patch(
            "backend.algo.jobs.entry_labeled_outcomes_rollup."
            "_fetch_daily_ohlcv",
            return_value={},
        ),
    ):
        out = _compute_outcomes([fill])

    key = (_STRAT, "live", "NYKAA", _DAY)
    assert out[key]["outcome_src"] == "none"
    assert out[key]["mfe_pct"] is None
    assert out[key]["mae_pct"] is None


# --------------------------------------------------------------- #
# Integration: real Docker Postgres, real _UPSERT_SQL
# --------------------------------------------------------------- #
#
# These hit the actual algo.entry_labeled_outcomes table (via the
# real disposable_pg_session, unmocked) to prove the DO UPDATE SET
# clause itself is correct — a pure-Python mock session cannot
# validate SQL semantics like COALESCE-vs-EXCLUDED. Only the Iceberg
# gather functions (_fetch_snapshots/_fetch_rejections/
# _fetch_closed_trades/_fetch_eqd/_compute_outcomes) are mocked, so
# the run processes exactly the one synthetic candidate below and
# never touches real dev data.


async def _cleanup_qa_row() -> None:
    async with _pg_session_cm() as session:
        await session.execute(
            text(
                "DELETE FROM algo.entry_labeled_outcomes "
                "WHERE ticker = :ticker AND trade_date = :trade_date "
                "AND strategy_id = :strategy_id"
            ),
            {
                "ticker": _QA_TICKER, "trade_date": _QA_DATE,
                "strategy_id": _STRAT,
            },
        )
        await session.commit()


async def _seed_qa_row(**overrides) -> None:
    """Simulate a PRE-1-reconstructed row: non-NULL features/QM/ESS
    seeded directly (as PRE-1's backfill would have done), which the
    daily job must never null out on a re-run."""
    base = {
        "user_id": _USER, "strategy_id": _STRAT, "mode": "live",
        "ticker": _QA_TICKER, "trade_date": _QA_DATE,
        "rsi2_at_entry": 42.5, "dist_sma50_pct": 12.3,
        "dist_sma200_pct": 30.1, "qm_score": 77.3, "ess_score": 61.0,
        "filled": True, "rejection_reason": None,
        "realised_pnl_inr": 10.0, "outcome_settled": True,
        "label_win": True, "dry_run": False,
    }
    base.update(overrides)
    async with _pg_session_cm() as session:
        await session.execute(
            text(
                "INSERT INTO algo.entry_labeled_outcomes ("
                " user_id, strategy_id, mode, ticker, trade_date,"
                " rsi2_at_entry, dist_sma50_pct, dist_sma200_pct,"
                " qm_score, ess_score, filled, rejection_reason,"
                " realised_pnl_inr, outcome_settled, label_win,"
                " dry_run"
                ") VALUES ("
                " :user_id, :strategy_id, :mode, :ticker, :trade_date,"
                " :rsi2_at_entry, :dist_sma50_pct, :dist_sma200_pct,"
                " :qm_score, :ess_score, :filled, :rejection_reason,"
                " :realised_pnl_inr, :outcome_settled, :label_win,"
                " :dry_run"
                ")"
            ),
            base,
        )
        await session.commit()


def _pg_session_cm():
    from backend.db.engine import disposable_pg_session

    return disposable_pg_session()


def _qa_fill(**overrides) -> dict:
    base = {
        "user_id": _USER, "strategy_id": _STRAT, "mode": "live",
        "ticker": _QA_TICKER, "trade_date": _QA_DATE,
        "entry_price": 100.0, "exit_price": 115.0,
        "realised_pnl_inr": 15.0, "return_pct": 15.0,
        "opened_at_ts_ns": 1000, "closed_at_ts_ns": 2000,
        "closed_at": _QA_DATE, "exit_reason": "signal",
        "buy_event_id": "qa-buy", "sell_event_id": "qa-sell",
        "dry_run": False,
    }
    base.update(overrides)
    return base


async def _select_qa_row() -> dict:
    async with _pg_session_cm() as session:
        result = await session.execute(
            text(
                "SELECT rsi2_at_entry, dist_sma50_pct, "
                "dist_sma200_pct, qm_score, ess_score, filled, "
                "rejection_reason, realised_pnl_inr, "
                "outcome_settled, label_win "
                "FROM algo.entry_labeled_outcomes "
                "WHERE ticker = :ticker AND trade_date = :trade_date "
                "AND strategy_id = :strategy_id"
            ),
            {
                "ticker": _QA_TICKER, "trade_date": _QA_DATE,
                "strategy_id": _STRAT,
            },
        )
        return dict(result.mappings().one())


async def test_run_upsert_preserves_reconstructed_features():
    """(1) seed a PRE-1-style row with non-NULL features for a fill
    that has NO snapshot this run; (2) run the real upsert path;
    (3) features survive (COALESCE), outcome cols update
    (unconditional EXCLUDED). Runs the job TWICE to also prove the
    real upsert path — not just the pure helper — is idempotent."""
    await _cleanup_qa_row()
    await _seed_qa_row()
    try:
        fill = _qa_fill()
        with (
            patch(f"{_MODULE}._fetch_snapshots", return_value={}),
            patch(f"{_MODULE}._fetch_rejections", return_value={}),
            patch(
                f"{_MODULE}._fetch_closed_trades",
                new=AsyncMock(return_value=[fill]),
            ),
            patch(f"{_MODULE}._fetch_eqd", return_value={}),
            patch(f"{_MODULE}._compute_outcomes", return_value={}),
        ):
            result = await _run(
                {"today": "2026-08-10", "window_days": 400},
            )
            assert result["rows_upserted"] == 1

            row = await _select_qa_row()
            # Enrichment columns SURVIVE: this run's fill carries no
            # snapshot and _fetch_eqd found nothing new, so
            # EXCLUDED.<feature> is NULL and COALESCE must fall back
            # to the PRE-1-reconstructed values seeded above.
            assert float(row["rsi2_at_entry"]) == 42.5
            assert float(row["dist_sma50_pct"]) == 12.3
            assert float(row["dist_sma200_pct"]) == 30.1
            assert float(row["qm_score"]) == 77.3
            assert float(row["ess_score"]) == 61.0
            # Outcome/state columns DO update unconditionally.
            assert row["filled"] is True
            assert row["rejection_reason"] is None
            assert float(row["realised_pnl_inr"]) == 15.0
            assert row["outcome_settled"] is True

            # Re-run: real upsert idempotency, not the pure helper.
            result2 = await _run(
                {"today": "2026-08-10", "window_days": 400},
            )
            assert result2["rows_upserted"] == 1
            row2 = await _select_qa_row()
            assert row2 == row
    finally:
        await _cleanup_qa_row()


async def test_run_upsert_clears_rejection_reason_when_fills():
    """A candidate rejected on an earlier run (rejection_reason set,
    filled=false) that now FILLS must transition rejection_reason ->
    NULL and filled -> true. Proves outcome/state columns are NOT
    coalesced (a COALESCE there would wrongly freeze the stale
    rejection_reason forever)."""
    await _cleanup_qa_row()
    await _seed_qa_row(
        filled=False, rejection_reason="ticker_not_allowed",
        realised_pnl_inr=None, outcome_settled=False,
        label_win=None,
    )
    try:
        fill = _qa_fill(realised_pnl_inr=20.0, return_pct=20.0)
        with (
            patch(f"{_MODULE}._fetch_snapshots", return_value={}),
            patch(f"{_MODULE}._fetch_rejections", return_value={}),
            patch(
                f"{_MODULE}._fetch_closed_trades",
                new=AsyncMock(return_value=[fill]),
            ),
            patch(f"{_MODULE}._fetch_eqd", return_value={}),
            patch(f"{_MODULE}._compute_outcomes", return_value={}),
        ):
            result = await _run(
                {"today": "2026-08-10", "window_days": 400},
            )
            assert result["rows_upserted"] == 1

            row = await _select_qa_row()
            assert row["filled"] is True
            assert row["rejection_reason"] is None
            assert float(row["realised_pnl_inr"]) == 20.0
            assert row["outcome_settled"] is True
            # Features seeded by PRE-1 still survive this transition.
            assert float(row["rsi2_at_entry"]) == 42.5
            assert float(row["qm_score"]) == 77.3
    finally:
        await _cleanup_qa_row()
