"""Per-fill trade-feature snapshots for the alpha-research
dataset (ASETPLTFRM-402 / FE-5).

Single-row Iceberg appends keyed off the ``order_filled`` /
``order_filled_live`` event. Wrapped in ``retry_iceberg_op``
for cross-pipeline write safety (the same retry helper the
v3 regime / factors / universe writers use).

CRITICAL DESIGN — Promotion gate independence
=============================================
The strategy promotion workflow (PR #221) scans
``algo.events WHERE mode='paper' AND type='order_filled'``
to decide paper→live eligibility. Snapshot writes here go
to a SEPARATE Iceberg table
(``stocks.trade_feature_snapshots``). The ``order_filled``
event on ``algo.events`` is untouched. Snapshot failure
NEVER blocks the fill or the event emission — the caller
contract is "this function MUST NOT raise"; every
PyIceberg / serialization exception is caught and logged
with ``exc_info=True``.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

import pyarrow as pa

from backend.algo._iceberg_retry import retry_iceberg_op

_logger = logging.getLogger(__name__)

_TABLE = "stocks.trade_feature_snapshots"


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
) -> None:
    """Write one fill's feature snapshot to
    ``stocks.trade_feature_snapshots``.

    Failure isolation: every PyIceberg / serialization
    exception is caught and logged with ``exc_info=True``.
    The fill itself (already applied + already emitted to
    ``algo.events``) is untouched. This function MUST NOT
    raise — that's the contract with the calling fill paths
    (backtest runner, paper runtime, live runtime).

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
            cadences. For daily cadences ``None`` is
            allowed — the writer derives a deterministic
            ns from ``bar_date`` UTC midnight so the
            Iceberg ``required=True`` constraint holds.
        bar_date: ISO YYYY-MM-DD for the fill bar.
        mode: ``"backtest"`` / ``"paper"`` / ``"live"``
            (the partition key, NOT
            ``"live_dry_run"`` — the dry-run flag stays in
            the matching event payload only).
        features: Feature mapping at decision time. May be
            ``None`` or empty for synthetic fills with no
            feature context in scope.
    """
    try:
        features_json = _serialize_features(features)

        if fill_ts_ns is None:
            # Daily backtest fills don't carry a bar-open
            # ns. Derive a deterministic value from
            # bar_date so the Iceberg required=True
            # constraint is satisfiable AND alpha-research
            # consumers still get a sortable timestamp.
            try:
                d = datetime.strptime(bar_date, "%Y-%m-%d")
                d = d.replace(tzinfo=timezone.utc)
                fill_ts_ns_value = int(d.timestamp() * 1_000_000_000)
            except ValueError:
                fill_ts_ns_value = 0
        else:
            fill_ts_ns_value = int(fill_ts_ns)

        year_month = bar_date[:7]
        # Iceberg TimestampType is tz-naive — strip tz
        # before write per ``iceberg-tz-naive-timestamps``
        # memory.
        written_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            fill_price_f = float(fill_price)
        except (TypeError, ValueError):
            fill_price_f = 0.0

        schema = _snapshot_arrow_schema()
        row = {
            "fill_id": str(fill_id),
            "run_id": str(run_id),
            "strategy_id": str(strategy_id),
            "ticker": str(ticker),
            "side": str(side),
            "qty": int(qty),
            "fill_price": fill_price_f,
            "fill_ts_ns": fill_ts_ns_value,
            "bar_date": str(bar_date),
            "year_month": str(year_month),
            "mode": str(mode),
            "features_json": features_json,
            "realised_pnl_inr": None,
            "outcome_label": None,
            "written_at": written_at,
        }
        cols = {k: [row[k]] for k in schema.names}
        arrow_tbl = pa.table(cols, schema=schema)

        def _do_append() -> None:
            from stocks.create_tables import _get_catalog

            cat = _get_catalog()
            tbl = cat.load_table(_TABLE)
            tbl.append(arrow_tbl)

        retry_iceberg_op(_TABLE, _do_append)
    except Exception:
        _logger.exception(
            "trade_feature_snapshot write failed "
            "(non-fatal): ticker=%s mode=%s "
            "fill_ts_ns=%s bar_date=%s",
            ticker,
            mode,
            fill_ts_ns,
            bar_date,
        )
        return None
    return None
