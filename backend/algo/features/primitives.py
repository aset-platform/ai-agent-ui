"""Pure-function primitive math for the centralized feature
engine. NO I/O, NO logging, NO global state.

Every helper here takes a list (or list-of-Decimal) input and
returns a same-length list of ``Decimal | None`` outputs where
``None`` means "not yet computable" (insufficient history).
The engine layer (``engine.py``) consumes these lists and
emits per-bar feature dicts, omitting keys for which the
underlying primitive returned ``None``.

Decimal arithmetic is used throughout so the engine output
matches the existing slice-4b convention (which the runner
expects). The ``time_of_day_bucket`` primitive returns a ``str``
because that's its semantic — every other primitive returns
numeric.

Bit-for-bit parity with the slice-4b helpers in
``backend/algo/backtest/indicators.py`` is mandatory for
``_vwap_intraday``, ``_wilder_rsi``, ``_rolling_sma``; FE-4
will delete the originals once the centralized engine is the
sole producer. The implementations here are intentionally
identical line-for-line so the parity test trivially passes.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Mapping

from backend.algo.backtest.types import BarData

# Indian Standard Time — UTC+05:30. The NSE intraday session
# is anchored to IST so every time-of-day computation must
# convert ``bar_open_ts_ns`` (UTC ns since epoch) into IST
# before slicing.
IST = timezone(timedelta(minutes=330))

# NSE intraday session anchors (IST). ORB = first 15 minutes
# of the session (09:15 - 09:30).
SESSION_OPEN_IST = time(9, 15)
ORB_END_IST = time(9, 30)

# Time-of-day buckets, IST. Bounds are inclusive-on-left,
# exclusive-on-right except for the last bucket which extends
# through session close.
_TOD_OPENING_END = time(10, 30)
_TOD_MIDDAY_END = time(13, 0)
_TOD_LUNCH_END = time(14, 0)

# Volatility / band primitives that need a population (NOT
# sample) standard deviation. Decimal does not ship a std, so
# we compute it manually with the same algorithm numpy uses
# under ``ddof=0``.

DEC_ZERO = Decimal("0")
DEC_ONE = Decimal("1")
DEC_TWO = Decimal("2")
DEC_THREE = Decimal("3")
DEC_HUNDRED = Decimal("100")


# ────────────────────────────────────────────────────────────────
# Slice-4b parity primitives (re-implemented bit-for-bit)
# ────────────────────────────────────────────────────────────────


def vwap_intraday(bars: list[BarData]) -> list[Decimal | None]:
    """Intraday VWAP — running mean of typical_price weighted by
    volume, reset at each calendar-day boundary (matches NSE
    session convention). Bit-for-bit parity with slice-4b
    ``_vwap_intraday``.
    """
    out: list[Decimal | None] = [None] * len(bars)
    if not bars:
        return out
    cum_pv = DEC_ZERO
    cum_v = DEC_ZERO
    last_date: date | None = None
    for i, bar in enumerate(bars):
        if bar.date != last_date:
            cum_pv = DEC_ZERO
            cum_v = DEC_ZERO
            last_date = bar.date
        typical = (bar.high + bar.low + bar.close) / DEC_THREE
        vol = Decimal(bar.volume)
        cum_pv += typical * vol
        cum_v += vol
        if cum_v > 0:
            out[i] = cum_pv / cum_v
    return out


def wilder_rsi(
    closes: list[Decimal],
    window: int = 14,
) -> list[Decimal | None]:
    """Wilder's RSI. Output[i] is None for the first ``window``
    bars; subsequent bars use Wilder's exponential smoothing
    (alpha = 1/window). Bit-for-bit parity with slice-4b
    ``_wilder_rsi``.
    """
    n = len(closes)
    out: list[Decimal | None] = [None] * n
    if n <= window:
        return out
    gains = DEC_ZERO
    losses = DEC_ZERO
    for i in range(1, window + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / Decimal(window)
    avg_loss = losses / Decimal(window)
    if avg_loss == 0:
        out[window] = DEC_HUNDRED
    else:
        rs = avg_gain / avg_loss
        out[window] = DEC_HUNDRED - DEC_HUNDRED / (DEC_ONE + rs)
    w = Decimal(window)
    for i in range(window + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else DEC_ZERO
        loss = -diff if diff < 0 else DEC_ZERO
        avg_gain = (avg_gain * (w - 1) + gain) / w
        avg_loss = (avg_loss * (w - 1) + loss) / w
        if avg_loss == 0:
            out[i] = DEC_HUNDRED
        else:
            rs = avg_gain / avg_loss
            out[i] = DEC_HUNDRED - DEC_HUNDRED / (DEC_ONE + rs)
    return out


def rolling_sma(
    closes: list[Decimal],
    window: int,
) -> list[Decimal | None]:
    """Right-aligned simple moving average. Output[i] = mean of
    closes[i-window+1 : i+1] when ≥ window points exist, else
    None. Bit-for-bit parity with slice-4b ``_rolling_sma``.
    """
    out: list[Decimal | None] = [None] * len(closes)
    if not closes or window <= 0:
        return out
    running = DEC_ZERO
    for i, c in enumerate(closes):
        running += c
        if i >= window:
            running -= closes[i - window]
        if i >= window - 1:
            out[i] = running / Decimal(window)
    return out


# ────────────────────────────────────────────────────────────────
# New Phase-1 primitives
# ────────────────────────────────────────────────────────────────


def ema(
    closes: list[Decimal],
    span: int,
) -> list[Decimal | None]:
    """Exponential moving average with TA-Lib seeding convention.

    The first ``span`` bars are None. Output[span-1] is seeded
    with SMA(span) of closes[0:span]; subsequent bars apply the
    exponential decay ``alpha = 2 / (span + 1)``:

        ema[i] = alpha × close[i] + (1 − alpha) × ema[i−1]

    This matches TA-Lib's default ``EMA`` behaviour and is the
    convention used by every charting platform (TradingView,
    pandas-ta, talib). Explicitly documented per spec §9.1
    (EMA-vs-SMA semantics on warmup-truncated series).
    """
    n = len(closes)
    out: list[Decimal | None] = [None] * n
    if span <= 0 or n < span:
        return out
    # Seed with SMA(span).
    seed = sum(closes[:span], DEC_ZERO) / Decimal(span)
    out[span - 1] = seed
    alpha = DEC_TWO / Decimal(span + 1)
    one_minus_alpha = DEC_ONE - alpha
    prev = seed
    for i in range(span, n):
        cur = alpha * closes[i] + one_minus_alpha * prev
        out[i] = cur
        prev = cur
    return out


def series_slope_n_bar(
    series: list[Decimal | None],
    lag: int,
) -> list[Decimal | None]:
    """Per-bar difference ``series[i] − series[i − lag]``.

    Output[i] is None for the first ``lag`` bars OR if either
    endpoint is None.
    """
    n = len(series)
    out: list[Decimal | None] = [None] * n
    if lag <= 0:
        return out
    for i in range(lag, n):
        cur = series[i]
        prev = series[i - lag]
        if cur is None or prev is None:
            continue
        out[i] = cur - prev
    return out


def roc_n_bar(
    closes: list[Decimal],
    lag: int,
) -> list[Decimal | None]:
    """Rate of change ``(close[i] / close[i − lag]) − 1`` as a
    Decimal fraction (NOT percent). Output[i] is None for the
    first ``lag`` bars or if ``close[i − lag] == 0``.
    """
    n = len(closes)
    out: list[Decimal | None] = [None] * n
    if lag <= 0:
        return out
    for i in range(lag, n):
        prior = closes[i - lag]
        if prior == 0:
            continue
        out[i] = closes[i] / prior - DEC_ONE
    return out


def wilder_atr(
    bars: list[BarData],
    window: int = 14,
) -> list[Decimal | None]:
    """Wilder's Average True Range.

    true_range[i] = max(
        high[i] − low[i],
        abs(high[i] − close[i − 1]),
        abs(low[i]  − close[i − 1]),
    )

    ATR[window] is seeded with the simple mean of the first
    ``window`` true-range values; subsequent bars apply Wilder's
    smoothing ``ATR[i] = (ATR[i−1] × (window − 1) + TR[i]) /
    window``. Output[i] is None for i < window.

    Note: true_range[0] has no prev close — falls back to
    (high − low). It contributes to the window-seed mean.
    """
    n = len(bars)
    out: list[Decimal | None] = [None] * n
    if window <= 0 or n < window:
        return out
    tr: list[Decimal] = [DEC_ZERO] * n
    for i, bar in enumerate(bars):
        hl = bar.high - bar.low
        if i == 0:
            tr[i] = hl
        else:
            prev_close = bars[i - 1].close
            tr[i] = max(
                hl,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
    seed = sum(tr[:window], DEC_ZERO) / Decimal(window)
    out[window - 1] = seed
    w = Decimal(window)
    prev = seed
    for i in range(window, n):
        cur = (prev * (w - 1) + tr[i]) / w
        out[i] = cur
        prev = cur
    return out


def bollinger_band_width(
    closes: list[Decimal],
    window: int = 20,
) -> list[Decimal | None]:
    """Bollinger band width = ``2 × std(close, window) / sma``.

    Population std (ddof=0) is used — matches the standard
    Bollinger Bands definition and TradingView's default.
    Output[i] is None for the first ``window − 1`` bars, or if
    ``sma == 0`` (degenerate, should never happen for prices).
    """
    n = len(closes)
    out: list[Decimal | None] = [None] * n
    if window <= 0 or n < window:
        return out
    sma = rolling_sma(closes, window)
    w_dec = Decimal(window)
    for i in range(window - 1, n):
        mean = sma[i]
        if mean is None or mean == 0:
            continue
        # Population variance: Σ(x − mean)² / window.
        window_slice = closes[i - window + 1 : i + 1]
        var = (
            sum(
                ((x - mean) * (x - mean) for x in window_slice),
                DEC_ZERO,
            )
            / w_dec
        )
        std = var.sqrt() if hasattr(var, "sqrt") else _decimal_sqrt(var)
        out[i] = DEC_TWO * std / mean
    return out


def _decimal_sqrt(x: Decimal) -> Decimal:
    """Decimal sqrt without relying on Python 3.13's ``Decimal.sqrt``.

    Uses ``getcontext().sqrt`` which has been available since
    Python 3.0. We wrap it in a helper so the call site stays
    expressive.
    """
    if x < 0:
        raise ValueError("sqrt of negative Decimal")
    # ``Decimal`` instances expose ``.sqrt(context)`` since 3.0
    # via the C accelerator; fall through to the context-level
    # call for safety.
    from decimal import getcontext

    return getcontext().sqrt(x)


def rolling_avg_volume(
    bars: list[BarData],
    window: int = 20,
) -> list[Decimal | None]:
    """Rolling average of the ``volume`` field over the last
    ``window`` bars (inclusive of the current bar). Output[i]
    is None until ≥ ``window`` bars seen.
    """
    n = len(bars)
    out: list[Decimal | None] = [None] * n
    if window <= 0 or n < window:
        return out
    running = DEC_ZERO
    for i, bar in enumerate(bars):
        running += Decimal(bar.volume)
        if i >= window:
            running -= Decimal(bars[i - window].volume)
        if i >= window - 1:
            out[i] = running / Decimal(window)
    return out


def relative_volume_by_time_of_day(
    bars: list[BarData],
    *,
    lookback_days: int = 20,
) -> list[Decimal | None]:
    """``volume[i] / avg(volume_at_same (hour, minute), prior
    occurrences within the last ``lookback_days`` calendar days)``.

    Output[i] is None until ≥ 1 prior occurrence of the same
    ``(hour, minute)`` IST exists. The "last 20 days" window is
    measured in IST calendar days from the current bar's date.

    Per spec §4: this is the canonical intraday volume-anomaly
    feature. Equal-time-of-day comparison matters because NSE
    sessions show strong intraday seasonality (open + close
    have ~4× midday volume on most tickers).
    """
    n = len(bars)
    out: list[Decimal | None] = [None] * n
    if n == 0:
        return out
    # Bucket each bar by (hour, minute) IST. Track per-bucket
    # list of (date, volume) so we can window by calendar days.
    bucket: dict[tuple[int, int], list[tuple[date, int]]] = {}
    for i, bar in enumerate(bars):
        if bar.bar_open_ts_ns is None:
            continue
        bar_dt = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).astimezone(IST)
        key = (bar_dt.hour, bar_dt.minute)
        bar_date_ist = bar_dt.date()
        history = bucket.get(key)
        if history:
            # Filter to prior bars within the lookback window
            # (strict cutoff: bar_date_ist − lookback_days).
            cutoff = bar_date_ist - timedelta(days=lookback_days)
            prior_vols = [
                v for (d, v) in history if cutoff <= d < bar_date_ist
            ]
            if prior_vols:
                avg = Decimal(sum(prior_vols)) / Decimal(len(prior_vols))
                if avg > 0:
                    out[i] = Decimal(bar.volume) / avg
        history = bucket.setdefault(key, [])
        history.append((bar_date_ist, bar.volume))
    return out


def volume_spike_flag(
    bars: list[BarData],
    *,
    window: int = 20,
    multiplier: Decimal = DEC_TWO,
) -> list[Decimal | None]:
    """Binary flag (1.0 / 0.0 as Decimal) emitted when
    ``volume[i] > multiplier × rolling_avg(volume, window)``.

    Output[i] is None until ≥ ``window`` bars seen.
    """
    n = len(bars)
    out: list[Decimal | None] = [None] * n
    avg_series = rolling_avg_volume(bars, window=window)
    for i, bar in enumerate(bars):
        avg = avg_series[i]
        if avg is None:
            continue
        out[i] = (
            DEC_ONE if Decimal(bar.volume) > multiplier * avg else DEC_ZERO
        )
    return out


# ────────────────────────────────────────────────────────────────
# Calendar-day structure primitives
# ────────────────────────────────────────────────────────────────


def per_day_index(bars: list[BarData]) -> list[int]:
    """Per-bar IST-calendar-day index. Output[i] is the 0-based
    ordinal of bar i within its trading day (first bar of the
    day = 0, etc.).

    Uses each bar's IST-date (derived from ``bar_open_ts_ns``)
    so a bar straddling midnight UTC gets bucketed correctly.
    Falls back to ``bar.date`` when ``bar_open_ts_ns`` is None.
    """
    n = len(bars)
    out: list[int] = [0] * n
    last_key: date | None = None
    counter = 0
    for i, bar in enumerate(bars):
        key = _ist_date(bar)
        if key != last_key:
            counter = 0
            last_key = key
        out[i] = counter
        counter += 1
    return out


def _ist_date(bar: BarData) -> date:
    """Return the bar's IST calendar date. Prefers
    ``bar_open_ts_ns`` (UTC ns since epoch) so a bar opened at
    18:30 UTC (= 00:00 IST next day) bucketed correctly.
    """
    if bar.bar_open_ts_ns is None:
        return bar.date
    dt = datetime.fromtimestamp(
        bar.bar_open_ts_ns / 1_000_000_000,
        tz=timezone.utc,
    ).astimezone(IST)
    return dt.date()


def prev_day_close_high_low(
    bars: list[BarData],
) -> list[tuple[Decimal, Decimal, Decimal] | None]:
    """Per-bar tuple ``(prev_day_close, prev_day_high, prev_day_low)``.

    Output[i] is None for every bar on day 1 (no prior day).
    For day N ≥ 2, every bar of that day gets the same triple
    aggregated from day (N − 1)'s bars: close = last bar's
    close; high = max(high); low = min(low).
    """
    n = len(bars)
    out: list[tuple[Decimal, Decimal, Decimal] | None] = [None] * n
    if n == 0:
        return out
    # Per-IST-date aggregates: close (last bar), high (max), low (min).
    cur_date = _ist_date(bars[0])
    cur_high = bars[0].high
    cur_low = bars[0].low
    cur_last_close = bars[0].close
    last_complete: tuple[Decimal, Decimal, Decimal] | None = None
    for i, bar in enumerate(bars):
        d = _ist_date(bar)
        if d != cur_date:
            last_complete = (cur_last_close, cur_high, cur_low)
            cur_date = d
            cur_high = bar.high
            cur_low = bar.low
            cur_last_close = bar.close
        else:
            if bar.high > cur_high:
                cur_high = bar.high
            if bar.low < cur_low:
                cur_low = bar.low
            cur_last_close = bar.close
        if last_complete is not None:
            out[i] = last_complete
    return out


def today_open_per_bar(bars: list[BarData]) -> list[Decimal | None]:
    """Per-bar ``today_open``: the open of the first bar of the
    IST-calendar day containing bar i. Constant within a day.
    None should never happen on a well-formed series (every
    bar has a valid day-anchor); we leave None for safety.
    """
    n = len(bars)
    out: list[Decimal | None] = [None] * n
    if n == 0:
        return out
    cur_date = _ist_date(bars[0])
    cur_open = bars[0].open
    for i, bar in enumerate(bars):
        d = _ist_date(bar)
        if d != cur_date:
            cur_date = d
            cur_open = bar.open
        out[i] = cur_open
    return out


def orb_per_bar(
    bars: list[BarData],
) -> list[tuple[Decimal, Decimal] | None]:
    """Per-bar ORB (Opening Range Breakout) tuple ``(orb_high,
    orb_low)`` computed over IST 09:15-09:30 (exclusive of
    09:30) — i.e. the first 15 minutes of the NSE session.

    The 09:15 bar itself has no ORB yet (the range isn't
    complete). The 09:30 bar onwards gets the (constant) ORB
    of that trading day.

    Output[i] is None when:
    - bar i is on a day with NO bars in [09:15, 09:30) IST, OR
    - bar i is itself inside the [09:15, 09:30) window.

    Defensive: if a day's data starts at e.g. 09:30 (no 09:15
    bar), ORB returns None for that whole day rather than
    quietly using the wrong bar as the opening range.
    """
    n = len(bars)
    out: list[tuple[Decimal, Decimal] | None] = [None] * n
    if n == 0:
        return out
    # First pass: compute (orb_high, orb_low) per IST-date.
    orb_by_date: dict[date, tuple[Decimal, Decimal]] = {}
    contributing_idx_by_date: dict[date, list[int]] = {}
    for i, bar in enumerate(bars):
        if bar.bar_open_ts_ns is None:
            continue
        dt = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).astimezone(IST)
        if SESSION_OPEN_IST <= dt.time() < ORB_END_IST:
            d = dt.date()
            contributing_idx_by_date.setdefault(d, []).append(i)
            existing = orb_by_date.get(d)
            if existing is None:
                orb_by_date[d] = (bar.high, bar.low)
            else:
                orb_by_date[d] = (
                    max(existing[0], bar.high),
                    min(existing[1], bar.low),
                )
    # Second pass: emit per-bar ORB, skipping bars within the
    # opening-range window itself.
    contributing_set: set[int] = set()
    for indices in contributing_idx_by_date.values():
        contributing_set.update(indices)
    for i, bar in enumerate(bars):
        if i in contributing_set:
            continue
        d = _ist_date(bar)
        orb = orb_by_date.get(d)
        if orb is not None:
            out[i] = orb
    return out


def minutes_since_open(bars: list[BarData]) -> list[int | None]:
    """Per-bar minutes since 09:15 IST (the NSE session open).

    Output[i] is None when ``bar_open_ts_ns`` is missing.
    Anchored to each bar's own IST date — the 09:15 bar of any
    day is 0; the 09:30 bar is 15; etc. Negative values (pre-
    market) are technically possible but should never appear
    in a well-formed NSE intraday series; we return them as-is
    rather than clamping.
    """
    n = len(bars)
    out: list[int | None] = [None] * n
    for i, bar in enumerate(bars):
        if bar.bar_open_ts_ns is None:
            continue
        dt = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).astimezone(IST)
        open_dt = datetime.combine(
            dt.date(),
            SESSION_OPEN_IST,
            tzinfo=IST,
        )
        delta = dt - open_dt
        out[i] = int(delta.total_seconds() // 60)
    return out


# ────────────────────────────────────────────────────────────────
# FE-9 cross-sectional primitives
# ────────────────────────────────────────────────────────────────


def compute_sector_rotation_at_bar(
    sectoral_returns: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    """Rank sectoral indices by their bar return and return a
    per-sector normalised score in ``[0.0, 1.0]``.

    - Best-performing sector (highest return) → ``1.0``
    - Worst-performing sector → ``0.0``
    - Intermediate ranks evenly spaced via
      ``score = (N - rank) / (N - 1)`` for ``N`` sectors

    The caller (FE-9 cohort pass) feeds a single bar's per-sector
    bar-to-bar returns and then maps each equity ticker to its
    sector symbol → score.

    Pure function. No I/O.

    Empty input or a single-sector input returns ``{}`` — the
    feature is undefined for fewer than 2 sectors (rank
    normalisation would divide by zero with ``N == 1``).

    Tie-breaking: ``sorted(..., key=..., reverse=True)`` on
    ``(return, symbol)`` pairs is Python-stable. When two sectors
    have identical returns the one whose symbol sorts EARLIER
    alphabetically gets the better rank — deterministic and
    reproducible across runs.
    """
    if len(sectoral_returns) < 2:
        return {}
    # Sort descending by (return, symbol). Symbol is used as a
    # deterministic tie-breaker so identical returns produce a
    # stable ordering (Python ``sorted`` is stable but the
    # initial input dict ordering is not). Negating the
    # alphabetical symbol order is non-trivial for strings, so
    # we lean on the natural (best-return, lex-smallest-symbol)
    # ordering by sorting by ``(-return, symbol)``.
    ordered = sorted(
        sectoral_returns.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    n = len(ordered)
    denom = Decimal(n - 1)
    out: dict[str, Decimal] = {}
    for rank, (sym, _ret) in enumerate(ordered):
        # rank 0 = best → score 1.0; rank n-1 = worst → score 0.
        out[sym] = (Decimal(n - 1 - rank)) / denom
    return out


def time_of_day_bucket(bars: list[BarData]) -> list[str | None]:
    """Per-bar IST time-of-day bucket label.

    - ``"opening"`` for 09:15 - 10:30 (inclusive-left, excl-right)
    - ``"midday"``  for 10:30 - 13:00
    - ``"lunch"``   for 13:00 - 14:00
    - ``"closing"`` for 14:00 - 15:30 (inclusive of 15:30)

    Bars outside the NSE session (e.g. an off-hours bar in
    test data) get a bucket based on the same rules — there's
    no "after_hours" bucket in Phase 1.

    Output[i] is None when ``bar_open_ts_ns`` is missing.
    """
    n = len(bars)
    out: list[str | None] = [None] * n
    for i, bar in enumerate(bars):
        if bar.bar_open_ts_ns is None:
            continue
        dt = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).astimezone(IST)
        t = dt.time()
        if t < _TOD_OPENING_END:
            out[i] = "opening"
        elif t < _TOD_MIDDAY_END:
            out[i] = "midday"
        elif t < _TOD_LUNCH_END:
            out[i] = "lunch"
        else:
            out[i] = "closing"
    return out
