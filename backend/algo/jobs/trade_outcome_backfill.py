"""Scheduled job to backfill ``outcome_label`` on
``stocks.trade_feature_snapshots`` (FE-13, ASETPLTFRM-415).

Scans rows where ``outcome_label IS NULL`` and
``realised_pnl_inr IS NOT NULL`` over a rolling window, derives
the meta-label from the sign of ``realised_pnl_inr`` (winner /
loser / breakeven), then batch-upserts via
:func:`retry_iceberg_op` with a scoped pre-delete on
``In("fill_id", batch_fill_ids)`` so re-runs of the same window
remain idempotent.

CRITICAL — ``realised_pnl_inr`` itself is NOT computed by this
job. It's written by the position-closer in the runtime (when a
fill closes a prior open position). FE-13 only LABELS existing
pnl values, it doesn't compute them. If ``realised_pnl_inr``
remains null indefinitely (e.g. positions still open or the
realised-pnl backfill hasn't yet run), that row's
``outcome_label`` stays null too — the row is simply skipped on
each pass.

Wired via ``@register_job("trade_outcome_backfill")`` in
``backend/jobs/executor.py``. Activates on next backend restart.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import (
    AlwaysTrue,
    And,
    EqualTo,
    GreaterThanOrEqual,
    In,
    IsNull,
    LessThanOrEqual,
    NotNull,
)

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.cache import get_cache
from backend.db.engine import disposable_pg_session  # noqa: F401

_logger = logging.getLogger(__name__)

_TRADE_FEATURE_SNAPSHOTS_TABLE = "stocks.trade_feature_snapshots"

_DEFAULT_BATCH_SIZE = 500
_DEFAULT_WINDOW_DAYS = 30
_DEFAULT_MIN_WINNER_THRESHOLD = 0.01


_OUTCOME_WINNER = "winner"
_OUTCOME_LOSER = "loser"
_OUTCOME_BREAKEVEN = "breakeven"


def _ist_today() -> date:
    """IST-local date (UTC+5:30) — matches the rest of the daily
    pipeline so the default window is intuitive to operators."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _default_window() -> tuple[date, date]:
    """Default window = last 30 days IST inclusive."""
    today = _ist_today()
    return today - timedelta(days=_DEFAULT_WINDOW_DAYS), today


def _snapshot_arrow_schema() -> pa.Schema:
    """Arrow schema matching ``stocks.trade_feature_snapshots``
    (FE-5).

    Kept identical to :func:`backend.algo.features.snapshots.
    _snapshot_arrow_schema` so the rewrite path emits Arrow tables
    PyIceberg accepts without column-order drift.
    """
    return pa.schema(
        [
            pa.field("fill_id", pa.string(), nullable=False),
            pa.field("run_id", pa.string(), nullable=False),
            pa.field("strategy_id", pa.string(), nullable=False),
            pa.field("ticker", pa.string(), nullable=False),
            pa.field("side", pa.string(), nullable=False),
            pa.field("qty", pa.int64(), nullable=False),
            pa.field("fill_price", pa.float64(), nullable=False),
            pa.field("fill_ts_ns", pa.int64(), nullable=False),
            pa.field("bar_date", pa.string(), nullable=False),
            pa.field("year_month", pa.string(), nullable=False),
            pa.field("mode", pa.string(), nullable=False),
            pa.field("features_json", pa.string(), nullable=False),
            pa.field(
                "realised_pnl_inr",
                pa.float64(),
                nullable=True,
            ),
            pa.field(
                "outcome_label",
                pa.string(),
                nullable=True,
            ),
            pa.field(
                "written_at",
                pa.timestamp("us"),
                nullable=False,
            ),
        ]
    )


def _derive_outcome(
    realised_pnl_inr: float | None,
    *,
    min_winner_threshold: float,
) -> str | None:
    """Map a ``realised_pnl_inr`` value to a meta-label.

    Rules:
      * ``realised_pnl_inr > +min_winner_threshold`` → ``winner``
      * ``realised_pnl_inr < -min_winner_threshold`` → ``loser``
      * ``|realised_pnl_inr| <= min_winner_threshold`` →
        ``breakeven``
      * ``None`` / non-finite → ``None`` (caller SKIPs row)

    The default threshold (0.01 INR) covers single-paisa
    floating-point drift on synthetic / period-end-MTM fills
    where the "true" pnl is zero but accumulated multiplications
    leave residue < 1 paisa.
    """
    if realised_pnl_inr is None:
        return None
    try:
        pnl = float(realised_pnl_inr)
    except (TypeError, ValueError):
        return None
    if math.isnan(pnl) or math.isinf(pnl):
        return None
    if pnl > min_winner_threshold:
        return _OUTCOME_WINNER
    if pnl < -min_winner_threshold:
        return _OUTCOME_LOSER
    return _OUTCOME_BREAKEVEN


def _build_row_filter(
    *,
    period_start: date,
    period_end: date,
    strategy_id: str | None,
):
    """Compose the PyIceberg predicate for the scan.

    Walks down to a single ``And`` tree of:
      * ``IsNull("outcome_label")``
      * ``NotNull("realised_pnl_inr")``
      * ``GreaterThanOrEqual("bar_date", iso_start)``
      * ``LessThanOrEqual("bar_date", iso_end)``
      * ``EqualTo("strategy_id", sid)`` when provided
    """
    iso_start = period_start.strftime("%Y-%m-%d")
    iso_end = period_end.strftime("%Y-%m-%d")
    preds: list[Any] = [
        IsNull("outcome_label"),
        NotNull("realised_pnl_inr"),
        GreaterThanOrEqual("bar_date", iso_start),
        LessThanOrEqual("bar_date", iso_end),
    ]
    if strategy_id:
        preds.append(EqualTo("strategy_id", str(strategy_id)))
    if not preds:
        return AlwaysTrue()
    expr = preds[0]
    for p in preds[1:]:
        expr = And(expr, p)
    return expr


def _scan_candidate_rows(
    *,
    period_start: date,
    period_end: date,
    strategy_id: str | None,
) -> list[dict[str, Any]]:
    """Scan ``stocks.trade_feature_snapshots`` for candidate rows.

    Returns a list of dicts (one per row) with all 15 columns
    populated so the rewrite path can emit a schema-complete
    Arrow batch. Failures bubble up — the caller catches them and
    aborts the run with a structured error in stats.
    """
    from stocks.create_tables import _get_catalog

    row_filter = _build_row_filter(
        period_start=period_start,
        period_end=period_end,
        strategy_id=strategy_id,
    )
    cat = _get_catalog()
    tbl = cat.load_table(_TRADE_FEATURE_SNAPSHOTS_TABLE)
    arrow_tbl = tbl.scan(row_filter=row_filter).to_arrow()
    out: list[dict[str, Any]] = []
    for row in arrow_tbl.to_pylist():
        out.append(row)
    return out


def _coerce_row_for_rewrite(
    row: dict[str, Any],
    *,
    new_label: str,
) -> dict[str, Any]:
    """Stamp ``outcome_label`` onto a row dict and coerce every
    column to the Arrow-friendly Python type the snapshot schema
    expects. Mirrors :func:`backend.algo.features.snapshots.
    write_trade_feature_snapshot`'s coercion shape so re-appended
    rows are byte-equivalent to writer output (modulo the new
    label + a fresh ``written_at``).
    """
    written_at = datetime.now(timezone.utc).replace(tzinfo=None)
    raw_written = row.get("written_at")
    if isinstance(raw_written, datetime):
        # Preserve the original written_at — meta-labelling is
        # NOT a re-write of the snapshot's authoritative
        # timestamp; the writer's clock is the source of truth.
        written_at = (
            raw_written.replace(tzinfo=None)
            if raw_written.tzinfo is not None
            else raw_written
        )
    try:
        qty_val = int(row.get("qty") or 0)
    except (TypeError, ValueError):
        qty_val = 0
    try:
        fill_price_val = float(row.get("fill_price") or 0.0)
    except (TypeError, ValueError):
        fill_price_val = 0.0
    try:
        fill_ts_ns_val = int(row.get("fill_ts_ns") or 0)
    except (TypeError, ValueError):
        fill_ts_ns_val = 0
    realised = row.get("realised_pnl_inr")
    try:
        realised_val: float | None = (
            float(realised) if realised is not None else None
        )
    except (TypeError, ValueError):
        realised_val = None
    return {
        "fill_id": str(row["fill_id"]),
        "run_id": str(row.get("run_id") or ""),
        "strategy_id": str(row.get("strategy_id") or ""),
        "ticker": str(row.get("ticker") or ""),
        "side": str(row.get("side") or ""),
        "qty": qty_val,
        "fill_price": fill_price_val,
        "fill_ts_ns": fill_ts_ns_val,
        "bar_date": str(row.get("bar_date") or ""),
        "year_month": str(row.get("year_month") or ""),
        "mode": str(row.get("mode") or ""),
        "features_json": str(row.get("features_json") or "{}"),
        "realised_pnl_inr": realised_val,
        "outcome_label": new_label,
        "written_at": written_at,
    }


def _write_labeled_batch(
    *,
    rewrite_rows: list[dict[str, Any]],
    fill_ids: list[str],
) -> int:
    """Scoped pre-delete + append for one batch of labelled rows.

    Per CLAUDE.md §4.3 #18 ``In("fill_id", batch)`` is scoped to
    the touched fill_ids only — never a global predicate. The
    same retry helper used by the FE-5 writer absorbs concurrent
    commit conflicts.
    """
    if not rewrite_rows:
        return 0
    schema = _snapshot_arrow_schema()
    cols = {k: [r[k] for r in rewrite_rows] for k in schema.names}
    arrow_tbl = pa.table(cols, schema=schema)

    def _do_upsert() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(_TRADE_FEATURE_SNAPSHOTS_TABLE)
        try:
            tbl.delete(In("fill_id", fill_ids))
        except Exception as exc:  # noqa: BLE001
            # First-touch / catalog-not-yet-populated pre-delete
            # failures are benign — nothing to delete.
            _logger.debug(
                "trade_outcome_backfill pre-delete skipped " "(%s): %s",
                _TRADE_FEATURE_SNAPSHOTS_TABLE,
                exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(_TRADE_FEATURE_SNAPSHOTS_TABLE, _do_upsert)
    return arrow_tbl.num_rows


def _invalidate_feature_importance_cache() -> None:
    """FE-11 caches feature-importance scores keyed on
    ``strategy_id``. A successful outcome backfill changes the
    label distribution → importance scores stale. Glob-pattern
    invalidate per CLAUDE.md §5.13.
    """
    try:
        cache = get_cache()
        cache.invalidate("cache:feature_importance:*")
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[trade-outcome-backfill] cache invalidate skipped: " "%s",
            exc,
            exc_info=True,
        )


async def run_trade_outcome_backfill_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan ``stocks.trade_feature_snapshots`` → label unlabeled
    rows whose ``realised_pnl_inr`` is non-null.

    Payload keys (all optional):
      * ``period_start`` / ``period_end`` (ISO ``YYYY-MM-DD``):
        explicit window. Default last 30 days IST.
      * ``strategy_id`` (str): filter to one strategy. Default
        all strategies.
      * ``batch_size`` (int, default 500): ``fill_id`` count per
        Iceberg commit.
      * ``min_winner_threshold`` (float, default 0.01): absolute
        INR threshold below which pnl labels as ``breakeven``.
      * ``dry_run`` (bool, default False): count + log only, no
        Iceberg write.

    Returns::

        {
            "rows_scanned": int,
            "rows_labeled": int,
            "rows_unchanged": int,
            "failures": list[tuple[str, str]],
            "elapsed_s": float,
            "window": [iso_start, iso_end],
            "strategy_id": str | None,
            "dry_run": bool,
            "status": "ok" | "error",
        }
    """
    started = time.perf_counter()
    payload = payload or {}

    start_d, end_d = _default_window()
    if payload.get("period_start"):
        start_d = date.fromisoformat(payload["period_start"])
    if payload.get("period_end"):
        end_d = date.fromisoformat(payload["period_end"])
    strategy_id = payload.get("strategy_id") or None
    batch_size = int(
        payload.get("batch_size") or _DEFAULT_BATCH_SIZE,
    )
    try:
        min_winner_threshold = float(
            (
                payload.get("min_winner_threshold")
                if payload.get("min_winner_threshold") is not None
                else _DEFAULT_MIN_WINNER_THRESHOLD
            ),
        )
    except (TypeError, ValueError):
        min_winner_threshold = _DEFAULT_MIN_WINNER_THRESHOLD
    dry_run = bool(payload.get("dry_run") or False)

    stats: dict[str, Any] = {
        "rows_scanned": 0,
        "rows_labeled": 0,
        "rows_unchanged": 0,
        "failures": [],
        "elapsed_s": 0.0,
        "window": [start_d.isoformat(), end_d.isoformat()],
        "strategy_id": strategy_id,
        "dry_run": dry_run,
    }

    _logger.info(
        "[trade-outcome-backfill] start window=%s..%s "
        "strategy_id=%s batch_size=%d threshold=%.4f dry_run=%s",
        start_d.isoformat(),
        end_d.isoformat(),
        strategy_id,
        batch_size,
        min_winner_threshold,
        dry_run,
    )

    try:
        candidates = _scan_candidate_rows(
            period_start=start_d,
            period_end=end_d,
            strategy_id=strategy_id,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[trade-outcome-backfill] scan failed: %s",
            exc,
            exc_info=True,
        )
        stats["status"] = "error"
        stats["error"] = f"scan:{exc!s}"[:200]
        stats["elapsed_s"] = time.perf_counter() - started
        return stats

    stats["rows_scanned"] = len(candidates)
    if not candidates:
        stats["status"] = "ok"
        stats["elapsed_s"] = time.perf_counter() - started
        _logger.info(
            "[trade-outcome-backfill] no candidate rows in "
            "window %s..%s — nothing to label",
            start_d.isoformat(),
            end_d.isoformat(),
        )
        return stats

    # Derive labels per-row, swallowing per-row failures so the
    # whole batch never strands on one malformed row.
    labeled_rows: list[dict[str, Any]] = []
    labeled_fill_ids: list[str] = []
    for row in candidates:
        fill_id = row.get("fill_id")
        if not fill_id:
            stats["rows_unchanged"] += 1
            continue
        # Guard #1: row already has a label (shouldn't happen
        # given the scan filter, but the scan can race a parallel
        # writer; recheck before rewriting).
        existing = row.get("outcome_label")
        if existing:
            stats["rows_unchanged"] += 1
            continue
        # Guard #2: realised_pnl_inr null after all (same race).
        realised = row.get("realised_pnl_inr")
        if realised is None:
            stats["rows_unchanged"] += 1
            continue
        try:
            new_label = _derive_outcome(
                realised,
                min_winner_threshold=min_winner_threshold,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[trade-outcome-backfill] derive failed for " "fill_id=%s: %s",
                fill_id,
                exc,
                exc_info=True,
            )
            stats["failures"].append(
                (str(fill_id), f"derive:{exc!s}"[:200]),
            )
            continue
        if new_label is None:
            stats["rows_unchanged"] += 1
            continue
        try:
            rewrite = _coerce_row_for_rewrite(
                row,
                new_label=new_label,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[trade-outcome-backfill] coerce failed for " "fill_id=%s: %s",
                fill_id,
                exc,
                exc_info=True,
            )
            stats["failures"].append(
                (str(fill_id), f"coerce:{exc!s}"[:200]),
            )
            continue
        labeled_rows.append(rewrite)
        labeled_fill_ids.append(str(fill_id))

    if dry_run:
        stats["rows_labeled"] = len(labeled_rows)
        stats["status"] = "ok"
        stats["elapsed_s"] = time.perf_counter() - started
        _logger.info(
            "[trade-outcome-backfill] dry_run=True scanned=%d "
            "would_label=%d unchanged=%d failures=%d",
            stats["rows_scanned"],
            stats["rows_labeled"],
            stats["rows_unchanged"],
            len(stats["failures"]),
        )
        return stats

    # Batch the writes — each batch is one Iceberg commit.
    written_total = 0
    for i in range(0, len(labeled_rows), batch_size):
        rows_chunk = labeled_rows[i : i + batch_size]
        fids_chunk = labeled_fill_ids[i : i + batch_size]
        try:
            written = _write_labeled_batch(
                rewrite_rows=rows_chunk,
                fill_ids=fids_chunk,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[trade-outcome-backfill] batch upsert failed "
                "(rows=%d): %s",
                len(rows_chunk),
                exc,
                exc_info=True,
            )
            for fid in fids_chunk:
                stats["failures"].append(
                    (str(fid), f"upsert:{exc!s}"[:200]),
                )
            continue
        written_total += int(written or 0)

    stats["rows_labeled"] = written_total

    if written_total > 0:
        _invalidate_feature_importance_cache()

    # Cap failures list so scheduler_runs doesn't store an
    # unbounded blob.
    if len(stats["failures"]) > 50:
        stats["failures"] = stats["failures"][:50]

    stats["status"] = "ok"
    stats["elapsed_s"] = time.perf_counter() - started
    _logger.info(
        "[trade-outcome-backfill] complete scanned=%d "
        "labeled=%d unchanged=%d failures=%d elapsed_s=%.3f",
        stats["rows_scanned"],
        stats["rows_labeled"],
        stats["rows_unchanged"],
        len(stats["failures"]),
        stats["elapsed_s"],
    )
    return stats
