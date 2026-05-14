"""Intraday entry / square-off window helpers.

Single source of truth for the MIS "no new entries after T-1h"
rule. Used by backtest, paper, dry-run, and live runtimes so a
strategy behaves identically across all four surfaces — when we
tighten the gate, every runtime tightens together.

Time semantics: all comparisons happen in IST (Asia/Kolkata)
because square-off times are defined in IST on the AST. Callers
must pass a tz-aware datetime or an explicit `time` object in
IST. The helpers below convert string IST stamps like
``"15:10 IST"`` / ``"14:00"`` into ``datetime.time`` once at
parse time.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta


# Default headroom between square-off and the no-new-entries gate.
# 60 min is enough for ~4 bars on a 15m strategy, ~12 bars on 5m,
# ~60 bars on 1m — plenty for a position to develop OR hit its
# stop-loss before the forced square-off.
DEFAULT_ENTRY_CUTOFF_HEADROOM = timedelta(minutes=60)

# Sane fallback when ``square_off_time`` is omitted on the AST.
# Mirrors LiveRuntime's existing default of 15:14 IST so behaviour
# stays consistent across the codebase.
_DEFAULT_SQUARE_OFF = time(15, 14)


_IST_PATTERN = re.compile(
    r"^\s*(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(IST)?\s*$"
)


def parse_ist_time(raw: str | None) -> time | None:
    """Parse strings like ``"15:10"`` / ``"15:10 IST"`` /
    ``"15:10:30 IST"``. Returns ``None`` for None input. Raises
    ``ValueError`` on a malformed string so the AST validator can
    surface the issue at parse time."""
    if raw is None:
        return None
    m = _IST_PATTERN.match(raw)
    if not m:
        raise ValueError(
            f"Cannot parse IST time {raw!r}; expected formats: "
            f"'HH:MM', 'HH:MM IST', 'HH:MM:SS IST'."
        )
    return time(
        int(m.group("h")),
        int(m.group("m")),
        int(m.group("s") or 0),
    )


def default_entry_cutoff(square_off_raw: str | None) -> str:
    """Compute the default entry-cutoff string given a square-off
    string. Returns ``"HH:MM IST"`` formatted output so downstream
    code can store the result back on the AST without re-deriving.

    The AST model_validator calls this when ``product=='MIS'`` and
    the user hasn't pinned ``entry_cutoff_time``.
    """
    sq = parse_ist_time(square_off_raw) or _DEFAULT_SQUARE_OFF
    base = datetime(2000, 1, 1, sq.hour, sq.minute, sq.second)
    cutoff = (base - DEFAULT_ENTRY_CUTOFF_HEADROOM).time()
    return f"{cutoff.hour:02d}:{cutoff.minute:02d} IST"


def is_entry_allowed(
    *,
    product: str | None,
    entry_cutoff_raw: str | None,
    bar_time_ist: time,
) -> bool:
    """Return ``False`` when a new long entry should be skipped
    because we're past the MIS no-new-entries cutoff.

    Non-MIS products (CNC, None, or anything unrecognised) always
    return ``True`` — the gate is intraday-MIS-specific. SELL /
    exit intents are unaffected by this function; callers should
    only gate BUY intents.
    """
    if product != "MIS":
        return True
    cutoff = parse_ist_time(entry_cutoff_raw)
    if cutoff is None:
        # No cutoff set and we couldn't derive one — fail safe by
        # allowing the entry. The AST validator should have
        # filled in a default for MIS, so this branch is for
        # legacy persisted strategies that pre-date the field.
        return True
    return bar_time_ist < cutoff


def is_past_square_off(
    *,
    product: str | None,
    square_off_raw: str | None,
    bar_time_ist: time,
) -> bool:
    """``True`` when a bar's IST time is at-or-after the square-off
    cutoff. Used by the backtest runner's day-end MIS check and by
    LiveRuntime to know when to flush exits."""
    if product != "MIS":
        return False
    sq = parse_ist_time(square_off_raw) or _DEFAULT_SQUARE_OFF
    return bar_time_ist >= sq


def ist_time_from_ns(bar_open_ts_ns: int | None) -> time | None:
    """Convert a UTC ns-since-epoch bar stamp into IST clock time
    (no date). Returns ``None`` on None input. Pure-Python; no
    pytz dependency."""
    if bar_open_ts_ns is None:
        return None
    seconds = bar_open_ts_ns // 1_000_000_000
    # IST = UTC + 5:30. We do integer math directly to avoid
    # constructing a tz-aware datetime; the date drops out below.
    seconds_ist = seconds + (5 * 3600 + 30 * 60)
    secs_in_day = seconds_ist % 86_400
    h = (secs_in_day // 3600) % 24
    m = (secs_in_day // 60) % 60
    s = secs_in_day % 60
    return time(int(h), int(m), int(s))
