"""Per-fill trade-feature snapshots for the alpha-research
dataset (ASETPLTFRM-402 / FE-5, FE-5.1 buffered).

Original FE-5 wrote one Iceberg commit per fill. FE-5.1
(ASETPLTFRM-417) replaces that with a mode-aware dispatcher:

* ``mode in ("backtest", "paper")`` -> add row to the
  in-process :class:`SnapshotsBuffer`; flushed in ONE commit at
  run / session end by the runtime's ``finally`` block.
* ``mode == "live"`` -> push row to a Redis LIST keyed by
  ``(user_id, trading_date_ist)``; drained in ONE commit per
  user at 15:30 IST by the
  ``trade_feature_snapshots_eod_flush`` scheduled job.

The original :func:`write_trade_feature_snapshot` signature is
preserved as the dispatcher entry point so existing FE-5 hooks
(backtest runner, paper runtime, live runtime) and existing
tests that patch this symbol keep working unchanged.

CRITICAL DESIGN — Promotion gate independence
=============================================
The strategy promotion workflow (PR #221) scans
``algo.events WHERE mode='paper' AND type='order_filled'`` to
decide paper -> live eligibility. Snapshot writes here go to a
SEPARATE Iceberg table (``stocks.trade_feature_snapshots``).
The ``order_filled`` event on ``algo.events`` is untouched.
Snapshot failure NEVER blocks the fill or the event emission —
every PyIceberg / serialization / Redis exception is caught and
logged with ``exc_info=True``.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

import pyarrow as pa

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.algo.features.snapshots_buffer import (
    FillSnapshotRow,
    get_buffer,
)
from backend.cache import get_cache

_logger = logging.getLogger(__name__)

_TABLE = "stocks.trade_feature_snapshots"

# Redis key prefix for live-mode snapshots. Listed for the EOD
# flush job's SCAN cursor.
_LIVE_REDIS_PREFIX = "algo:live:snapshots"

# 48 hour safety-net TTL on each live LIST. The same-day 15:30
# IST flush job is the primary drain; the TTL means a missed
# flush still bounds Redis growth at ~2 days.
_LIVE_REDIS_TTL_SEC = 48 * 60 * 60


def _snapshot_arrow_schema() -> pa.Schema:
    """Arrow schema matching ``stocks.trade_feature_snapshots``
    (FE-5).

    Every required field is ``nullable=False`` so PyIceberg's
    required-column enforcement fires at write time;
    ``realised_pnl_inr`` and ``outcome_label`` are
    ``nullable=True`` because the Iceberg schema marks them
    ``required=False`` (backfilled by Phase-3 jobs).
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


def _serialize_features(
    features: Mapping[str, Any] | None,
) -> str:
    """Serialize a feature mapping to JSON.

    Decimal values are converted to ``str`` preserving the
    same precision the existing fill-event payloads use for
    numeric values. String features (e.g.
    ``time_of_day_bucket``) pass through. ``NaN`` / ``inf``
    are dropped (the reader interprets a missing key as
    "feature not computable" at that bar). Returns ``"{}"``
    for ``None`` / empty input so the schema's
    ``features_json required=True`` constraint is always
    satisfiable — synthetic fills (e.g. period_end_mtm /
    MIS square-off when those paths emit through this
    writer) carry no feature context.
    """
    if not features:
        return "{}"
    out: dict[str, Any] = {}
    for k, v in features.items():
        if v is None:
            continue
        if isinstance(v, Decimal):
            try:
                fv = float(v)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isnan(fv) or math.isinf(fv):
                continue
            out[str(k)] = str(v)
        elif isinstance(v, str):
            out[str(k)] = v
        elif isinstance(v, (int, float)):
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                continue
            out[str(k)] = fv
        else:
            # Best-effort stringify for unexpected types so
            # the row never fails serialization. Research can
            # filter on these later.
            out[str(k)] = str(v)
    return json.dumps(out, sort_keys=True)


def _row_to_arrow_dict(row: FillSnapshotRow) -> dict[str, Any]:
    """Coerce a :class:`FillSnapshotRow` into the column dict
    shape the Arrow table builder expects. Mirrors the
    per-row coercion the original FE-5 single-row writer did.
    """
    features_json = _serialize_features(row.features)
    if row.fill_ts_ns is None:
        try:
            d = datetime.strptime(row.bar_date, "%Y-%m-%d")
            d = d.replace(tzinfo=timezone.utc)
            fill_ts_ns_value = int(d.timestamp() * 1_000_000_000)
        except ValueError:
            fill_ts_ns_value = 0
    else:
        fill_ts_ns_value = int(row.fill_ts_ns)
    try:
        fill_price_f = float(row.fill_price)
    except (TypeError, ValueError):
        fill_price_f = 0.0
    year_month = row.bar_date[:7]
    written_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return {
        "fill_id": str(row.fill_id),
        "run_id": str(row.run_id),
        "strategy_id": str(row.strategy_id),
        "ticker": str(row.ticker),
        "side": str(row.side),
        "qty": int(row.qty),
        "fill_price": fill_price_f,
        "fill_ts_ns": fill_ts_ns_value,
        "bar_date": str(row.bar_date),
        "year_month": str(year_month),
        "mode": str(row.mode),
        "features_json": features_json,
        "realised_pnl_inr": None,
        "outcome_label": None,
        "written_at": written_at,
    }


def write_trade_feature_snapshots_batch(
    rows: list[FillSnapshotRow] | Iterable[FillSnapshotRow],
) -> int:
    """Bulk-append N fills in ONE Iceberg commit (FE-5.1).

    This is the canonical writer used by the
    :class:`SnapshotsBuffer` flush path and by the EOD flush
    job for live-mode snapshots. The single-row dispatcher
    :func:`write_trade_feature_snapshot` routes to this
    function only indirectly (via the buffer); direct callers
    are the runtime ``finally`` blocks and the EOD job.

    Failure semantics: this function MAY raise. Callers (the
    buffer flush, the EOD job) catch + log ``exc_info=True``;
    bounded snapshot loss is the design contract per ticket.
    Returning 0 for an empty input keeps caller code simple.

    Args:
        rows: Iterable of :class:`FillSnapshotRow`.

    Returns:
        Number of rows appended.
    """
    row_list = list(rows)
    if not row_list:
        return 0
    schema = _snapshot_arrow_schema()
    arrow_dicts = [_row_to_arrow_dict(r) for r in row_list]
    cols = {k: [d[k] for d in arrow_dicts] for k in schema.names}
    arrow_tbl = pa.table(cols, schema=schema)

    def _do_append() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(_TABLE)
        tbl.append(arrow_tbl)

    retry_iceberg_op(_TABLE, _do_append)
    return arrow_tbl.num_rows


def _ist_today() -> date:
    """Return today's date in Asia/Kolkata (UTC + 5:30).

    Avoids the ``zoneinfo`` dependency / Docker TZ-file
    surface and mirrors the helper used in
    ``backend/algo/jobs/intraday_features_daily_compute.py``.
    """
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _push_live_snapshot_to_redis(
    *,
    row: FillSnapshotRow,
    user_id: str,
    trading_date_ist: date | None = None,
) -> None:
    """Best-effort Redis LIST push for a live-mode fill.

    Key: ``algo:live:snapshots:{user_id}:{trading_date_ist}``.
    Value: JSON-serialised row (Decimals stringified). TTL
    refreshed to :data:`_LIVE_REDIS_TTL_SEC` (48h) on every
    push so an idle list doesn't get garbage-collected mid-day.

    Failure semantics: NEVER raises. If Redis is down, the
    snapshot is lost for that fill — acceptable because the
    fill itself is in ``algo.events`` (snapshot is enrichment,
    not source of truth).
    """
    try:
        cache = get_cache()
        if trading_date_ist is None:
            trading_date_ist = _ist_today()
        key = (
            f"{_LIVE_REDIS_PREFIX}:{user_id}:"
            f"{trading_date_ist.isoformat()}"
        )
        payload = {
            "fill_id": str(row.fill_id),
            "run_id": str(row.run_id),
            "strategy_id": str(row.strategy_id),
            "ticker": str(row.ticker),
            "side": str(row.side),
            "qty": int(row.qty),
            "fill_price": str(row.fill_price),
            "fill_ts_ns": (
                int(row.fill_ts_ns) if row.fill_ts_ns is not None else None
            ),
            "bar_date": str(row.bar_date),
            "mode": str(row.mode),
            # Features are already a plain dict; let the EOD
            # flush re-route through ``_serialize_features``
            # when writing to Iceberg. Decimal here -> str so
            # the JSON dump can't trip on un-serialisable types.
            "features": _features_for_redis(row.features),
        }
        cache.rpush(key, json.dumps(payload))
        cache.expire(key, _LIVE_REDIS_TTL_SEC)
    except Exception:
        _logger.exception(
            "live snapshot redis push failed (non-fatal): "
            "ticker=%s user_id=%s fill_id=%s",
            getattr(row, "ticker", "?"),
            user_id,
            getattr(row, "fill_id", "?"),
        )


def _features_for_redis(
    features: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Stringify Decimals so the JSON dump for Redis never
    raises on non-serialisable types. NaN / inf dropped (same
    rule as :func:`_serialize_features`)."""
    if not features:
        return {}
    out: dict[str, Any] = {}
    for k, v in features.items():
        if v is None:
            continue
        if isinstance(v, Decimal):
            try:
                fv = float(v)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isnan(fv) or math.isinf(fv):
                continue
            out[str(k)] = str(v)
        elif isinstance(v, (int, float)):
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                continue
            out[str(k)] = fv
        elif isinstance(v, str):
            out[str(k)] = v
        else:
            out[str(k)] = str(v)
    return out


def _resolve_live_user_id() -> str | None:
    """Live-mode dispatcher pulls the active user id from a
    context var the live runtime sets per-session. We avoid a
    hard import here so this module stays usable in unit-test
    contexts where the live runtime isn't loaded.

    Returns ``None`` if no user can be resolved — the
    dispatcher falls back to logging + drop in that case so the
    fill ledger isn't blocked.
    """
    try:
        from backend.algo.live.runtime import (
            current_live_user_id,
        )

        return current_live_user_id()
    except Exception:
        return None


def write_trade_feature_snapshot(
    *,
    fill_id: str,
    run_id: str,
    strategy_id: str,
    ticker: str,
    side: str,
    qty: int,
    fill_price: Decimal,
    fill_ts_ns: int | None,
    bar_date: str,
    mode: str,
    features: Mapping[str, Any] | None,
    force_immediate: bool = False,
    user_id: str | None = None,
) -> None:
    """Dispatcher (FE-5.1) — routes a per-fill snapshot based
    on ``mode``.

    * ``mode in ("backtest", "paper")`` -> append to the
      in-process :class:`SnapshotsBuffer` keyed on
      ``(strategy_id, run_id)``. Flushed in ONE commit by the
      runtime's ``finally`` block.
    * ``mode == "live"`` -> push to Redis LIST. Drained at
      15:30 IST by the
      ``trade_feature_snapshots_eod_flush`` scheduled job.
    * Unknown ``mode`` -> log + drop (defensive).

    ``force_immediate=True`` bypasses the buffer + Redis paths
    and writes a single-row Iceberg commit immediately. Kept
    as an escape hatch for ad-hoc admin / test fixtures that
    need the old per-fill durability guarantee.

    Failure semantics: this function MUST NOT raise — that's
    the contract with the calling fill paths (backtest runner,
    paper runtime, live runtime). Every error is caught + logged
    with ``exc_info=True``.

    Args:
        fill_id: Stable per-fill identifier. Backtest /
            paper derive from ``intent_id`` + fill bar;
            live uses ``kite_order_id`` / internal id.
        run_id: Backtest session id, paper session id, or
            live run id.
        strategy_id: Strategy UUID as string.
        ticker: e.g. ``"RELIANCE.NS"``.
        side: ``"BUY"`` or ``"SELL"``.
        qty: Executed quantity.
        fill_price: Executed price in INR.
        fill_ts_ns: Bar-open ns-since-epoch for intraday
            cadences. For daily cadences ``None`` is allowed.
        bar_date: ISO YYYY-MM-DD for the fill bar.
        mode: ``"backtest"`` / ``"paper"`` / ``"live"``.
        features: Feature mapping at decision time.
        force_immediate: Bypass buffer + Redis; write a single
            Iceberg row synchronously. Default False.
        user_id: Required when ``mode == "live"`` to derive
            the Redis key. If omitted, the dispatcher tries to
            read it from the live runtime's contextvar (added
            for back-compat with the existing FE-5 hook
            signature in ``backend/algo/live/runtime.py``).
    """
    try:
        row = FillSnapshotRow(
            fill_id=str(fill_id),
            run_id=str(run_id),
            strategy_id=str(strategy_id),
            ticker=str(ticker),
            side=str(side),
            qty=int(qty),
            fill_price=fill_price,
            fill_ts_ns=fill_ts_ns,
            bar_date=str(bar_date),
            mode=str(mode),
            features=dict(features) if features else None,
        )

        if force_immediate:
            try:
                write_trade_feature_snapshots_batch([row])
            except Exception:
                _logger.exception(
                    "trade_feature_snapshot force_immediate "
                    "write failed (non-fatal): ticker=%s "
                    "mode=%s bar_date=%s",
                    ticker,
                    mode,
                    bar_date,
                )
            return None

        if mode in ("backtest", "paper"):
            get_buffer().add(
                row,
                key=(str(strategy_id), str(run_id)),
            )
            return None

        if mode == "live":
            uid = user_id or _resolve_live_user_id()
            if not uid:
                # Fail-safe: if we can't resolve a user, write
                # one row immediately so the snapshot isn't
                # silently dropped. Matches the original FE-5
                # contract (per-fill commit) for the rare live
                # path that lacks a user context.
                _logger.warning(
                    "live snapshot: user_id unresolved, "
                    "falling back to immediate write "
                    "(ticker=%s fill_id=%s)",
                    ticker,
                    fill_id,
                )
                try:
                    write_trade_feature_snapshots_batch([row])
                except Exception:
                    _logger.exception(
                        "live snapshot fallback write failed "
                        "(non-fatal): ticker=%s",
                        ticker,
                    )
                return None
            _push_live_snapshot_to_redis(
                row=row,
                user_id=str(uid),
            )
            return None

        # Unknown mode — defensive log + drop. Snapshot loss is
        # acceptable; the fill itself is in algo.events.
        _logger.warning(
            "trade_feature_snapshot: unknown mode=%s "
            "(ticker=%s fill_id=%s) — dropped",
            mode,
            ticker,
            fill_id,
        )
    except Exception:
        _logger.exception(
            "trade_feature_snapshot dispatcher failed "
            "(non-fatal): ticker=%s mode=%s bar_date=%s",
            ticker,
            mode,
            bar_date,
        )
    return None


# Re-export for callers that want the row dataclass without
# touching the buffer module directly.
__all__ = [
    "FillSnapshotRow",
    "write_trade_feature_snapshot",
    "write_trade_feature_snapshots_batch",
]
