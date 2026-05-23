"""Hydrate the cooldown gate from algo.events (ASETPLTFRM-436).

The cooldown gate (``cooldown_monitor.in_cooldown``) is pure: it
takes a ``closed_positions`` iterable + the cooldown window and
returns whether a ticker is in cooldown.

In backtest, the iterable is ``pt.closed_positions()`` — in-process
state for the whole run. In **paper / live**, runtime restarts wipe
that state. The durable source is ``algo.events`` (Iceberg),
specifically the ``order_filled`` / ``order_filled_live`` rows
whose payload has ``exit_reason`` (backtest, paper) or ``reason``
(live postback) ∈ {``time_stop``, ``stop_loss``}.

This module exposes a single helper that queries algo.events and
returns the same shape ``in_cooldown`` expects.

Called at paper / live runtime startup. As the session runs and new
failed exits land in algo.events, the in-process tracker stays in
sync. On restart, the helper is re-called.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID


_logger = logging.getLogger(__name__)


# algo.events row types that carry fill information across the
# 3 runtimes. mode column further scopes to paper or live.
_FILL_EVENT_TYPES = ("order_filled", "order_filled_live")
# Allowed mode values when hydrating a runtime's cooldown gate.
# Paper sessions read paper history; live sessions read live
# history. We don't cross-pollinate — a strategy that had a
# time_stop in paper shouldn't gate its live entries (paper
# universe may differ; paper price discovery may diverge).
_RUNTIME_MODE_MAP = {
    "paper": ("paper",),
    "live": ("live",),
}


@dataclass(frozen=True)
class _HydratedClose:
    """Shape that satisfies the ``_ClosedPositionLike`` protocol
    ``cooldown_monitor.in_cooldown`` expects. Lightweight stand-in
    for the full ``Position`` object — only the three fields the
    cooldown gate reads."""

    ticker: str
    exit_reason: str
    closed_at: date


def load_recent_failed_exits(
    *,
    user_id: UUID,
    strategy_id: UUID,
    cooldown_days: int,
    as_of: date,
    runtime_mode: str,
) -> list[_HydratedClose]:
    """Return synthetic ``_HydratedClose`` rows for failed exits.

    Output is a list of objects compatible with
    ``cooldown_monitor.in_cooldown(closed_positions=...)``.

    Queries ``algo.events`` for SELL fills with a failed
    ``exit_reason`` / ``reason`` within the last ``cooldown_days``
    calendar days, scoped to the runtime's own mode (``paper`` or
    ``live``).

    Returns an empty list on Iceberg miss (fresh installation) so
    the gate degrades open — the runner trades normally until the
    first failed exit lands in events.
    """
    if cooldown_days <= 0:
        return []

    modes = _RUNTIME_MODE_MAP.get(runtime_mode)
    if modes is None:
        _logger.warning(
            "load_recent_failed_exits: unknown runtime_mode=%r — "
            "returning empty (cooldown gate degrades open)",
            runtime_mode,
        )
        return []

    cutoff = as_of - timedelta(days=cooldown_days)

    from backend.db.duckdb_engine import query_iceberg_table

    modes_in = ",".join(f"'{m}'" for m in modes)
    types_in = ",".join(f"'{t}'" for t in _FILL_EVENT_TYPES)
    try:
        rows = query_iceberg_table(
            "algo.events",
            f"SELECT "
            f"  json_extract_string(payload_json, '$.ticker') "
            f"    AS ticker, "
            f"  MAX(ts_date) AS last_failed_exit "
            f"FROM events "
            f"WHERE user_id = ? "
            f"  AND strategy_id = ? "
            f"  AND mode IN ({modes_in}) "
            f"  AND type IN ({types_in}) "
            f"  AND ts_date >= ? "
            f"  AND COALESCE("
            f"      json_extract_string(payload_json, "
            f"          '$.exit_reason'), "
            f"      json_extract_string(payload_json, "
            f"          '$.reason')"
            f"  ) IN ('time_stop', 'stop_loss') "
            f"GROUP BY ticker",
            [str(user_id), str(strategy_id), cutoff],
        )
    except FileNotFoundError:
        # Fresh dev box / first deploy — events table not yet
        # created. Degrade open.
        _logger.info(
            "load_recent_failed_exits: algo.events not yet "
            "present — cooldown gate starts empty",
        )
        return []
    except Exception as exc:
        _logger.warning(
            "load_recent_failed_exits: algo.events query failed "
            "(%s) — cooldown gate degrades open",
            exc,
            exc_info=True,
        )
        return []

    out: list[_HydratedClose] = []
    for row in rows:
        ticker = row.get("ticker")
        last = row.get("last_failed_exit")
        if not ticker or last is None:
            continue
        # exit_reason on the synthesized row is informational —
        # in_cooldown filters by membership in the FAILED set,
        # and the query already pre-filtered to {time_stop,
        # stop_loss}. Use ``time_stop`` as a sentinel that
        # passes the membership check.
        out.append(_HydratedClose(
            ticker=ticker,
            exit_reason="time_stop",
            closed_at=last,
        ))
    _logger.info(
        "cooldown hydration: %d tickers loaded "
        "(strategy=%s mode=%s cooldown_days=%d cutoff=%s)",
        len(out), strategy_id, runtime_mode,
        cooldown_days, cutoff.isoformat(),
    )
    return out
