"""Iceberg CRUD for stocks.regime_history + stocks.regime_hmm_state.

Reads use ``query_iceberg_table`` (DuckDB fast-path); writes use
PyIceberg directly. NaN-replaceable upsert pattern (scoped
pre-delete by ``bar_date``, then append) keeps re-runs idempotent.

Cache invalidation: every successful write calls
``get_cache().invalidate("cache:regime:*")`` so the API endpoints
serve fresh data within one round-trip.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import EqualTo, In

from backend.algo.regime.iceberg_init import (
    REGIME_HISTORY_TABLE,
    REGIME_HMM_STATE_TABLE,
)
from cache import get_cache  # backend/ on PYTHONPATH
from db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)

_logger = logging.getLogger(__name__)


@dataclass
class RegimeRow:
    bar_date: date
    regime_label: str  # BULL | SIDEWAYS | BEAR
    stress_prob: float | None
    rule_inputs: dict[str, Any]
    classifier_version: str = "v1.0"


@dataclass
class HmmStateRow:
    trained_through: date
    transmat: list[list[float]]
    means: list[list[float]]
    covars: list[list[list[float]]]
    n_observations: int


def _catalog():
    from stocks.create_tables import _get_catalog
    return _get_catalog()


def _invalidate_regime_cache() -> None:
    """Best-effort wildcard invalidate. Sync; never await."""
    try:
        get_cache().invalidate("cache:regime:*")
    except Exception as exc:  # pragma: no cover
        _logger.debug("regime cache invalidate skipped: %s", exc)


def upsert_regime_history(rows: list[RegimeRow]) -> int:
    """NaN-replaceable upsert. Pre-deletes any existing rows for
    the incoming bar_dates; appends the new batch. Invalidates
    ``cache:regime:*`` on success.

    Wrapped in ``retry_iceberg_op`` so concurrent writers (e.g.
    the backfill running while the daily pipeline fires) don't
    crash with ``CommitFailedException``."""
    if not rows:
        return 0
    from backend.algo._iceberg_retry import retry_iceberg_op

    incoming_dates = [r.bar_date for r in rows]
    arrow_schema = pa.schema([
        pa.field("bar_date", pa.date32(), nullable=False),
        pa.field("regime_label", pa.string(), nullable=False),
        pa.field("stress_prob", pa.float64(), nullable=True),
        pa.field("rule_inputs_json", pa.string(), nullable=False),
        pa.field("classifier_version", pa.string(), nullable=False),
    ])
    arrow_tbl = pa.table(
        {
            "bar_date": [r.bar_date for r in rows],
            "regime_label": [r.regime_label for r in rows],
            "stress_prob": [r.stress_prob for r in rows],
            "rule_inputs_json": [
                json.dumps(r.rule_inputs, default=str) for r in rows
            ],
            "classifier_version": [
                r.classifier_version for r in rows
            ],
        },
        schema=arrow_schema,
    )

    def _do_upsert() -> None:
        cat = _catalog()
        tbl = cat.load_table(REGIME_HISTORY_TABLE)
        try:
            tbl.delete(In("bar_date", incoming_dates))
        except Exception as exc:  # first run on empty table
            _logger.debug(
                "regime_history pre-delete skipped: %s", exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(REGIME_HISTORY_TABLE, _do_upsert)
    invalidate_metadata(REGIME_HISTORY_TABLE)
    _invalidate_regime_cache()
    return len(rows)


def upsert_hmm_state(row: HmmStateRow) -> None:
    from backend.algo._iceberg_retry import retry_iceberg_op

    arrow_schema = pa.schema([
        pa.field("trained_through", pa.date32(), nullable=False),
        pa.field("transmat_json", pa.string(), nullable=False),
        pa.field("means_json", pa.string(), nullable=False),
        pa.field("covars_json", pa.string(), nullable=False),
        pa.field("n_observations", pa.int32(), nullable=False),
    ])
    arrow_tbl = pa.table(
        {
            "trained_through": [row.trained_through],
            "transmat_json": [json.dumps(row.transmat)],
            "means_json": [json.dumps(row.means)],
            "covars_json": [json.dumps(row.covars)],
            "n_observations": [row.n_observations],
        },
        schema=arrow_schema,
    )

    def _do_upsert() -> None:
        cat = _catalog()
        tbl = cat.load_table(REGIME_HMM_STATE_TABLE)
        try:
            tbl.delete(
                EqualTo("trained_through", row.trained_through),
            )
        except Exception as exc:
            _logger.debug(
                "regime_hmm_state pre-delete skipped: %s", exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(REGIME_HMM_STATE_TABLE, _do_upsert)
    invalidate_metadata(REGIME_HMM_STATE_TABLE)
    _invalidate_regime_cache()


def _row_from_dict(d: dict) -> RegimeRow:
    raw = d.get("rule_inputs_json")
    parsed = json.loads(raw) if raw else {}
    return RegimeRow(
        bar_date=d["bar_date"],
        regime_label=d["regime_label"],
        stress_prob=d.get("stress_prob"),
        rule_inputs=parsed,
        classifier_version=d.get("classifier_version", "v1.0"),
    )


def get_latest_regime() -> RegimeRow | None:
    rows = query_iceberg_table(
        REGIME_HISTORY_TABLE,
        "SELECT bar_date, regime_label, stress_prob, "
        "rule_inputs_json, classifier_version "
        "FROM regime_history ORDER BY bar_date DESC LIMIT 1",
        [],
    )
    return _row_from_dict(rows[0]) if rows else None


def get_regime_history(start: date, end: date) -> list[RegimeRow]:
    rows = query_iceberg_table(
        REGIME_HISTORY_TABLE,
        "SELECT bar_date, regime_label, stress_prob, "
        "rule_inputs_json, classifier_version "
        "FROM regime_history WHERE bar_date BETWEEN ? AND ? "
        "ORDER BY bar_date ASC",
        [start, end],
    )
    return [_row_from_dict(r) for r in rows]


def get_latest_hmm_state() -> HmmStateRow | None:
    rows = query_iceberg_table(
        REGIME_HMM_STATE_TABLE,
        "SELECT trained_through, transmat_json, means_json, "
        "covars_json, n_observations FROM regime_hmm_state "
        "ORDER BY trained_through DESC LIMIT 1",
        [],
    )
    if not rows:
        return None
    r = rows[0]
    return HmmStateRow(
        trained_through=r["trained_through"],
        transmat=json.loads(r["transmat_json"]),
        means=json.loads(r["means_json"]),
        covars=json.loads(r["covars_json"]),
        n_observations=r["n_observations"],
    )
