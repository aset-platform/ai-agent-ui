"""EOD drain of live-mode trade_feature_snapshots from Redis
into ``stocks.trade_feature_snapshots`` (ASETPLTFRM-417 /
FE-5.1).

Cron: Mon-Fri 15:30 IST. Standalone job — does NOT chain with
the 15:45 IST Intraday Bars Daily Pipeline so the two pipelines
can run concurrently without sharing an event loop.

Per-user algorithm
==================
1. SCAN ``algo:live:snapshots:*:{trading_date}`` to find every
   user's LIST for the target date.
2. For each LIST:
   * ``LRANGE`` -> deserialize JSON -> :class:`FillSnapshotRow`
   * Scoped pre-delete on
     ``In("fill_id", batch_fill_ids)`` to make the write
     idempotent under partial-flush re-runs (mirrors the FE-13
     scoped-delete pattern).
   * :func:`write_trade_feature_snapshots_batch` — ONE Iceberg
     commit per user.
   * On success: ``DEL`` the Redis key.
   * On failure: log ``exc_info=True``, leave the Redis key in
     place (48h TTL is the safety net; next-day run retries).

Idempotency
===========
* Re-running for the same ``trading_date`` is safe — already-
  flushed users have their Redis key deleted, so ``LRANGE``
  returns ``[]``.
* Partial-flush re-runs use the scoped pre-delete on the
  ``fill_id`` IN-set so re-emitted rows overwrite cleanly
  without bloating the table (FE-13 pattern, consistent with
  the rest of the FE-* writers).

Payload knobs
=============
* ``trading_date``: ISO ``YYYY-MM-DD``. Default = today IST.
* ``user_ids``: explicit user-id list. Default = SCAN.
* ``dry_run``: read Redis, skip write + skip DEL. Default
  ``False``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pyiceberg.expressions import And, EqualTo, In

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.algo.features.snapshots import (
    FillSnapshotRow,
    write_trade_feature_snapshots_batch,
)
from backend.cache import get_cache
from backend.db.duckdb_engine import invalidate_metadata

_logger = logging.getLogger(__name__)

_TABLE = "stocks.trade_feature_snapshots"
_LIVE_REDIS_PREFIX = "algo:live:snapshots"


def _ist_today() -> date:
    """Today's date in Asia/Kolkata (UTC + 5:30)."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _redis_key(user_id: str, trading_date: date) -> str:
    return f"{_LIVE_REDIS_PREFIX}:{user_id}:{trading_date.isoformat()}"


def _deserialize_rows(
    raw_rows: list[str],
) -> list[FillSnapshotRow]:
    """Decode JSON payloads pushed by
    :func:`backend.algo.features.snapshots._push_live_snapshot_to_redis`.

    Malformed entries are logged and skipped — one bad payload
    must not strand the whole batch.
    """
    out: list[FillSnapshotRow] = []
    for raw in raw_rows:
        try:
            d = json.loads(raw)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "[fe5.1-eod] dropping malformed live snapshot "
                "payload (json decode failed); raw=%s",
                raw[:200] if isinstance(raw, str) else "?",
                exc_info=True,
            )
            continue
        try:
            fp_raw = d.get("fill_price")
            try:
                fill_price = (
                    Decimal(str(fp_raw))
                    if fp_raw is not None
                    else Decimal("0")
                )
            except Exception:  # noqa: BLE001
                fill_price = Decimal("0")
            row = FillSnapshotRow(
                fill_id=str(d["fill_id"]),
                run_id=str(d["run_id"]),
                strategy_id=str(d["strategy_id"]),
                ticker=str(d["ticker"]),
                side=str(d["side"]),
                qty=int(d["qty"]),
                fill_price=fill_price,
                fill_ts_ns=(
                    int(d["fill_ts_ns"])
                    if d.get("fill_ts_ns") is not None
                    else None
                ),
                bar_date=str(d["bar_date"]),
                mode=str(d.get("mode") or "live"),
                features=(dict(d["features"]) if d.get("features") else None),
            )
        except (KeyError, ValueError, TypeError) as exc:
            _logger.warning(
                "[fe5.1-eod] dropping malformed live snapshot "
                "payload (%s): keys=%s",
                exc,
                list(d.keys()) if isinstance(d, dict) else "?",
            )
            continue
        out.append(row)
    return out


def _scoped_predelete_fill_ids(
    *,
    fill_ids: list[str],
    mode: str = "live",
) -> None:
    """Pre-delete any rows whose ``fill_id`` is in the incoming
    batch (scoped to ``mode='live'`` so a re-flush can't touch
    backtest / paper rows for the same UUID by accident).
    First-run / empty-partition failures are benign (no rows
    to delete) — logged at debug only.
    """
    if not fill_ids:
        return

    def _do_delete() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(_TABLE)
        tbl.delete(
            And(
                In("fill_id", fill_ids),
                EqualTo("mode", mode),
            ),
        )

    try:
        retry_iceberg_op(_TABLE, _do_delete)
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "[fe5.1-eod] scoped pre-delete skipped " "(non-fatal): %s",
            exc,
        )


def _list_user_keys(
    trading_date: date,
    explicit_user_ids: list[str] | None,
) -> list[str]:
    """Return Redis LIST keys to drain. When ``explicit_user_ids``
    is supplied, build keys directly (no SCAN). Otherwise
    enumerate via ``SCAN``.
    """
    cache = get_cache()
    if explicit_user_ids:
        return [_redis_key(uid, trading_date) for uid in explicit_user_ids]
    pattern = f"{_LIVE_REDIS_PREFIX}:*:{trading_date.isoformat()}"
    return cache.scan_keys(pattern)


def _user_id_from_key(key: str, trading_date: date) -> str | None:
    """Extract ``user_id`` from a fully-qualified Redis key.
    Returns ``None`` if the key doesn't match the expected
    shape — defensive against unrelated keys leaking into the
    SCAN."""
    suffix = f":{trading_date.isoformat()}"
    if not key.endswith(suffix):
        return None
    head = key[: -len(suffix)]
    prefix = f"{_LIVE_REDIS_PREFIX}:"
    if not head.startswith(prefix):
        return None
    return head[len(prefix) :]


async def run_trade_feature_snapshots_eod_flush_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drain Redis live-snapshot lists into Iceberg in ONE
    commit per user.

    Payload keys (all optional):
      * ``trading_date``: ISO ``YYYY-MM-DD``. Default = today
        IST.
      * ``user_ids``: list of user ids to flush. Default =
        SCAN ``algo:live:snapshots:*:{trading_date}``.
      * ``dry_run``: bool. When ``True``, reads Redis but does
        NOT write Iceberg + does NOT delete keys.

    Returns a stats dict suitable for ``scheduler_runs``::

        {
            "status": "ok",
            "trading_date": iso,
            "users_scanned": int,
            "users_flushed": int,
            "rows_written": int,
            "failures": list[tuple[user_id, reason]],
            "elapsed_s": float,
            "dry_run": bool,
        }
    """
    payload = payload or {}
    started = time.monotonic()
    if payload.get("trading_date"):
        trading_date = date.fromisoformat(payload["trading_date"])
    else:
        trading_date = _ist_today()
    explicit_user_ids = payload.get("user_ids")
    if explicit_user_ids is not None:
        explicit_user_ids = [
            str(u).strip() for u in explicit_user_ids if str(u).strip()
        ]
    dry_run = bool(payload.get("dry_run") or False)

    cache = get_cache()
    keys = _list_user_keys(trading_date, explicit_user_ids)

    stats: dict[str, Any] = {
        "status": "ok",
        "trading_date": trading_date.isoformat(),
        "users_scanned": len(keys),
        "users_flushed": 0,
        "rows_written": 0,
        "failures": [],
        "dry_run": dry_run,
    }

    _logger.info(
        "[fe5.1-eod] start trading_date=%s users=%d " "dry_run=%s",
        trading_date.isoformat(),
        len(keys),
        dry_run,
    )

    for key in keys:
        uid = _user_id_from_key(key, trading_date)
        if not uid:
            _logger.debug(
                "[fe5.1-eod] skipping unrelated key=%s",
                key,
            )
            continue
        try:
            raw_rows = cache.lrange(key)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[fe5.1-eod] LRANGE failed user_id=%s key=%s: " "%s",
                uid,
                key,
                exc,
                exc_info=True,
            )
            stats["failures"].append((uid, f"lrange:{exc!s}"[:200]))
            continue
        if not raw_rows:
            # Already drained (idempotent re-run) or the key
            # had a stale TTL just expire. Treat as success.
            if not dry_run:
                cache.delete(key)
            continue

        rows = _deserialize_rows(raw_rows)
        if not rows:
            _logger.warning(
                "[fe5.1-eod] user_id=%s key=%s: all rows "
                "malformed; deleting key",
                uid,
                key,
            )
            if not dry_run:
                cache.delete(key)
            stats["failures"].append((uid, "all_malformed"))
            continue

        if dry_run:
            _logger.info(
                "[fe5.1-eod] dry_run user_id=%s rows=%d "
                "(no Iceberg write, no DEL)",
                uid,
                len(rows),
            )
            stats["users_flushed"] += 1
            stats["rows_written"] += len(rows)
            continue

        # Scoped pre-delete on fill_id (FE-13 pattern) so a
        # partial-flush re-run doesn't double-write rows.
        fill_ids = [r.fill_id for r in rows]
        try:
            _scoped_predelete_fill_ids(fill_ids=fill_ids)
        except Exception as exc:  # noqa: BLE001
            # Pre-delete failure is non-fatal — log + continue.
            _logger.warning(
                "[fe5.1-eod] pre-delete failed user_id=%s "
                "rows=%d (proceeding anyway): %s",
                uid,
                len(rows),
                exc,
                exc_info=True,
            )

        try:
            written = write_trade_feature_snapshots_batch(rows)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[fe5.1-eod] batch write failed user_id=%s " "rows=%d: %s",
                uid,
                len(rows),
                exc,
                exc_info=True,
            )
            stats["failures"].append((uid, f"write:{exc!s}"[:200]))
            # Leave the Redis key in place; 48h TTL is the
            # safety net and next-day run retries.
            continue

        try:
            invalidate_metadata(_TABLE)
        except Exception:  # noqa: BLE001
            _logger.debug(
                "[fe5.1-eod] invalidate_metadata failed " "(non-fatal)",
                exc_info=True,
            )

        try:
            cache.delete(key)
        except Exception as exc:  # noqa: BLE001
            # Write succeeded but DEL failed — log and move on.
            # The next run will see an empty LIST anyway because
            # of the scoped pre-delete + dedup behavior.
            _logger.warning(
                "[fe5.1-eod] DEL failed user_id=%s key=%s " "(non-fatal): %s",
                uid,
                key,
                exc,
                exc_info=True,
            )

        stats["users_flushed"] += 1
        stats["rows_written"] += int(written)

    stats["elapsed_s"] = round(time.monotonic() - started, 3)
    if len(stats["failures"]) > 50:
        stats["failures"] = stats["failures"][:50]
    _logger.info(
        "[fe5.1-eod] complete trading_date=%s users_scanned=%d "
        "users_flushed=%d rows_written=%d failures=%d "
        "elapsed=%.3fs dry_run=%s",
        stats["trading_date"],
        stats["users_scanned"],
        stats["users_flushed"],
        stats["rows_written"],
        len(stats["failures"]),
        stats["elapsed_s"],
        dry_run,
    )
    return stats
