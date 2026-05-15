"""Centralized pure-function feature engine — entrypoint.

``compute_intraday_features(bars)`` is the canonical Phase-1
producer. Same shape contract as slice-4b
``compute_indicators_intraday(bars)`` so FE-4 can swap it in
mechanically. Bit-for-bit parity is enforced on the overlap
set (vwap, rsi, rsi_14, sma_20/50/100/200, today_ltp,
today_vol, golden_cross_bars_ago) by ``test_slice4b_parity.py``.

Phase-1 features emitted (26 — the two RS-vs-* features defer
to FE-8 because they need ``stocks.index_intraday_bars`` and
``stocks.sector_intraday_bars`` which only exist after
FE-6 / FE-7):

  trend       vwap, dist_from_vwap_pct,
              sma_20, sma_50, sma_100, sma_200,
              ema_20, ema_50, ema_20_slope_5bar,
              golden_cross_bars_ago
  momentum    rsi (alias rsi_14), rsi_5, roc_5
  volatility  atr_14, range_expansion, bb_width
  volume      relative_volume, volume_spike
  structure   gap_pct, orb_high_15min, orb_low_15min,
              dist_from_prev_day_high_pct,
              dist_from_prev_day_low_pct
  time        minutes_since_open, time_of_day_bucket
  trivial     today_ltp, today_vol

Skip-emission contract per CLAUDE.md feature-key-error: keys
absent (never None / NaN) when a feature isn't computable for
that bar (warmup, missing prior day, etc.). The runner's
``KeyError`` counter is the authoritative "feature wasn't
ready" signal.

NO I/O. Inputs in, outputs out. The Iceberg / Redis loader
layers land in FE-3 / FE-5.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from backend.algo.backtest.types import BarData
from backend.algo.features import primitives as p
from backend.algo.features.types import (
    TickerFeaturePanel,
    UniverseFeaturePanel,
)
from backend.algo.features.version import (
    DEFAULT_INTRADAY_SMA_WINDOWS,
    FEATURE_SET_VERSION,
    NO_CROSS_SENTINEL,
)


def compute_intraday_features(
    bars: list[BarData],
    *,
    sma_windows: Iterable[int] = DEFAULT_INTRADAY_SMA_WINDOWS,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> TickerFeaturePanel:
    """Per-bar feature dict for a single ticker's intraday series.

    Args:
        bars: Ascending-by-``bar_open_ts_ns`` list of bars from
            a single ticker. Bars without ``bar_open_ts_ns``
            (i.e. daily-shaped input) are skipped defensively.
        sma_windows: SMA windows to emit. Defaults to
            ``DEFAULT_INTRADAY_SMA_WINDOWS = (20, 50, 100, 200)``.
        feature_set_version: Reserved for FE-3's persisted-row
            stamping. Not embedded in the returned dict — the
            persister adds the column on write.

    Returns:
        ``{bar_open_ts_ns: {feature_name: Decimal | str}}``.
        ``time_of_day_bucket`` is ``str``; every other feature
        is ``Decimal``. Missing-feature keys are simply absent.
    """
    del feature_set_version  # accepted for forward-compat; reserved.
    if not bars:
        return {}
    series = [b for b in bars if b.bar_open_ts_ns is not None]
    if not series:
        return {}

    closes = [b.close for b in series]
    sma_windows_t = tuple(sma_windows)
    sma_by_w: dict[int, list[Decimal | None]] = {
        w: p.rolling_sma(closes, w) for w in sma_windows_t
    }
    rsi_14 = p.wilder_rsi(closes, 14)
    rsi_5 = p.wilder_rsi(closes, 5)
    vwap = p.vwap_intraday(series)
    ema_20 = p.ema(closes, 20)
    ema_50 = p.ema(closes, 50)
    ema_20_slope = p.series_slope_n_bar(ema_20, 5)
    roc_5 = p.roc_n_bar(closes, 5)
    atr_14 = p.wilder_atr(series, 14)
    bb_w = p.bollinger_band_width(closes, 20)
    rel_vol = p.relative_volume_by_time_of_day(
        series,
        lookback_days=20,
    )
    vol_spike = p.volume_spike_flag(series, window=20)
    prev_day = p.prev_day_close_high_low(series)
    today_open = p.today_open_per_bar(series)
    orb = p.orb_per_bar(series)
    mins_since_open = p.minutes_since_open(series)
    tod_bucket = p.time_of_day_bucket(series)

    s50 = sma_by_w.get(50)
    s200 = sma_by_w.get(200)
    last_cross_up_idx: int | None = None

    out: TickerFeaturePanel = {}
    for i, bar in enumerate(series):
        feats: dict[str, Decimal | str] = {
            "today_ltp": bar.close,
            "today_vol": Decimal(bar.volume),
        }

        # SMA family.
        for w in sma_windows_t:
            v = sma_by_w[w][i]
            if v is not None:
                feats[f"sma_{w}"] = v

        # RSI family.
        rsi_v = rsi_14[i]
        if rsi_v is not None:
            feats["rsi"] = rsi_v
            feats["rsi_14"] = rsi_v
        rsi5_v = rsi_5[i]
        if rsi5_v is not None:
            feats["rsi_5"] = rsi5_v

        # VWAP + distance.
        vwap_v = vwap[i]
        if vwap_v is not None:
            feats["vwap"] = vwap_v
            if vwap_v != 0:
                feats["dist_from_vwap_pct"] = (
                    (bar.close - vwap_v) / vwap_v * Decimal("100")
                )

        # EMA family + slope.
        ema20_v = ema_20[i]
        if ema20_v is not None:
            feats["ema_20"] = ema20_v
        ema50_v = ema_50[i]
        if ema50_v is not None:
            feats["ema_50"] = ema50_v
        ema20_slope_v = ema_20_slope[i]
        if ema20_slope_v is not None:
            feats["ema_20_slope_5bar"] = ema20_slope_v

        # ROC.
        roc_v = roc_5[i]
        if roc_v is not None:
            feats["roc_5"] = roc_v

        # ATR + range expansion.
        atr_v = atr_14[i]
        if atr_v is not None:
            feats["atr_14"] = atr_v
            if atr_v != 0:
                feats["range_expansion"] = (bar.high - bar.low) / atr_v

        # Bollinger band width.
        bb_v = bb_w[i]
        if bb_v is not None:
            feats["bb_width"] = bb_v

        # Volume features.
        rel_v = rel_vol[i]
        if rel_v is not None:
            feats["relative_volume"] = rel_v
        spike_v = vol_spike[i]
        if spike_v is not None:
            feats["volume_spike"] = spike_v

        # Structure: gap + prev-day distances.
        prev = prev_day[i]
        open_today = today_open[i]
        if prev is not None and open_today is not None:
            prev_close, prev_high, prev_low = prev
            if prev_close != 0:
                feats["gap_pct"] = (
                    (open_today - prev_close) / prev_close * Decimal("100")
                )
            if prev_high != 0:
                feats["dist_from_prev_day_high_pct"] = (
                    (bar.close - prev_high) / prev_high * Decimal("100")
                )
            if prev_low != 0:
                feats["dist_from_prev_day_low_pct"] = (
                    (bar.close - prev_low) / prev_low * Decimal("100")
                )

        # Structure: ORB.
        orb_v = orb[i]
        if orb_v is not None:
            feats["orb_high_15min"] = orb_v[0]
            feats["orb_low_15min"] = orb_v[1]

        # Time features.
        mins_v = mins_since_open[i]
        if mins_v is not None:
            feats["minutes_since_open"] = Decimal(mins_v)
        tod_v = tod_bucket[i]
        if tod_v is not None:
            feats["time_of_day_bucket"] = tod_v

        # Golden-cross bars-ago (bit-for-bit parity with slice-4b).
        if s50 is not None and s200 is not None and i > 0:
            cur_50 = s50[i]
            cur_200 = s200[i]
            prev_50 = s50[i - 1]
            prev_200 = s200[i - 1]
            if (
                cur_50 is not None
                and cur_200 is not None
                and prev_50 is not None
                and prev_200 is not None
            ):
                if prev_50 <= prev_200 and cur_50 > cur_200:
                    last_cross_up_idx = i
                if prev_50 >= prev_200 and cur_50 < cur_200:
                    last_cross_up_idx = None
        if last_cross_up_idx is not None:
            feats["golden_cross_bars_ago"] = Decimal(
                i - last_cross_up_idx,
            )
        else:
            feats["golden_cross_bars_ago"] = NO_CROSS_SENTINEL

        out[bar.bar_open_ts_ns] = feats  # type: ignore[index]
    return out


def compute_intraday_features_for_universe(
    bars_by_ticker: dict[str, list[BarData]],
    *,
    index_bars_by_symbol: dict[str, list[BarData]] | None = None,
    ticker_to_sector_index: dict[str, str] | None = None,
    sma_windows: Iterable[int] = DEFAULT_INTRADAY_SMA_WINDOWS,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> UniverseFeaturePanel:
    """Apply :func:`compute_intraday_features` per-ticker, then run
    the FE-8 cohort pass to merge in cross-sectional features.

    Phase A (per-ticker, unchanged from FE-2): every ticker's bar
    series is sorted ascending and fed through
    :func:`compute_intraday_features`. The output for non-FE-8
    feature keys is byte-identical to the pre-FE-8 behaviour.

    Phase B (FE-8 cohort pass, NEW): if ``index_bars_by_symbol``
    is provided, the engine layers four cross-sectional features
    onto each ticker's panel at every overlapping ``bar_open_ts_ns``:

    * ``rs_vs_nifty_15m`` — ``stock_ret − nifty_ret`` across the
      previous → current bar.
    * ``rs_vs_sector_15m`` — same against the ticker's mapped
      sector index. Emitted only for tickers whose entry is
      present in ``ticker_to_sector_index``.
    * ``market_breadth_pct_above_sma200`` — cohort-wide ``%`` of
      tickers with ``close > sma_200`` at the bar.
    * ``advance_decline_ratio`` — cohort-wide
      ``advancers / decliners``. Absent (skip-emission, not NaN)
      when ``decliners == 0`` (div-by-zero guard).

    When ``index_bars_by_symbol`` is ``None`` or missing
    ``"NIFTY 50"`` bars, the four FE-8 features are absent for
    every ticker; the rest of the panel is unaffected. This is
    the "warmup-style skip" contract — strategies referencing
    FE-8 features see absence (KeyError) and the pipeline
    assertion ``intraday-features-coverage-floor`` still passes
    because the non-cohort features remain ≥ 5 per bar.

    Pure-function discipline holds — no I/O. The compute job and
    on-demand backfill are responsible for fetching index bars +
    sector mappings.

    Args:
        bars_by_ticker: Per-ticker bar series.
        index_bars_by_symbol: Per-symbol bar series for the index
            universe (FE-6's ``stocks.index_intraday_bars``). When
            ``None``, FE-8 features are absent.
        ticker_to_sector_index: Maps equity ticker → Kite index
            tradingsymbol. Tickers absent from this dict do not
            receive ``rs_vs_sector_15m``.
        sma_windows: SMA windows (passed through to
            :func:`compute_intraday_features`).
        feature_set_version: Reserved (passed through).

    Returns:
        Universe panel
        ``{ticker: {bar_open_ts_ns: {feature_name: Decimal | str}}}``.
    """
    out: UniverseFeaturePanel = {}
    for ticker, blist in bars_by_ticker.items():
        if not blist:
            continue
        sorted_bars = sorted(
            blist,
            key=lambda b: (b.bar_open_ts_ns or 0),
        )
        out[ticker] = compute_intraday_features(
            sorted_bars,
            sma_windows=sma_windows,
            feature_set_version=feature_set_version,
        )
    if index_bars_by_symbol is not None:
        _apply_cohort_features(
            panel=out,
            bars_by_ticker=bars_by_ticker,
            index_bars_by_symbol=index_bars_by_symbol,
            ticker_to_sector_index=ticker_to_sector_index or {},
        )
    return out


def _close_by_ts(bars: list[BarData]) -> dict[int, Decimal]:
    """Helper: ``{bar_open_ts_ns: close}`` for a sorted series.

    Bars without ``bar_open_ts_ns`` are skipped defensively (daily-
    shaped inputs land here through the test harness occasionally).
    """
    return {
        b.bar_open_ts_ns: b.close
        for b in bars
        if b.bar_open_ts_ns is not None
    }


def _bar_to_bar_return(
    close: Decimal,
    prev_close: Decimal | None,
) -> Decimal | None:
    """Pure-Decimal bar-to-bar return ``(close/prev_close - 1)``.

    Returns ``None`` if ``prev_close`` is ``None`` or zero so the
    caller emits skip-absence rather than poisoning the cohort
    pass with ``NaN`` / ``inf``.
    """
    if prev_close is None or prev_close == 0:
        return None
    return (close / prev_close) - Decimal("1")


def _apply_cohort_features(
    *,
    panel: UniverseFeaturePanel,
    bars_by_ticker: dict[str, list[BarData]],
    index_bars_by_symbol: dict[str, list[BarData]],
    ticker_to_sector_index: dict[str, str],
) -> None:
    """In-place merge of FE-8 cross-sectional features onto
    ``panel``.

    Each cohort feature is treated independently — failure to
    compute one (e.g. NIFTY 50 bars missing) does not block the
    others. Skip-emission contract: features simply absent from
    each ts_ns entry when not computable.
    """
    # ── Index closes lookup (NIFTY 50 + sector indices) ──────
    nifty_closes = _close_by_ts(
        index_bars_by_symbol.get("NIFTY 50", []),
    )
    sector_closes: dict[str, dict[int, Decimal]] = {}
    for sym, blist in index_bars_by_symbol.items():
        if sym == "NIFTY 50":
            continue
        sector_closes[sym] = _close_by_ts(blist)

    # ── Per-ticker close lookups (sorted, ts → close) ────────
    ticker_closes: dict[str, dict[int, Decimal]] = {}
    ticker_sorted_ts: dict[str, list[int]] = {}
    for ticker, blist in bars_by_ticker.items():
        if not blist:
            continue
        sorted_bars = sorted(
            blist,
            key=lambda b: (b.bar_open_ts_ns or 0),
        )
        closes = _close_by_ts(sorted_bars)
        if not closes:
            continue
        ticker_closes[ticker] = closes
        ticker_sorted_ts[ticker] = sorted(closes.keys())

    # ── Cohort breadth + A/D — group by ts across all tickers
    breadth_pct: dict[int, Decimal] = {}
    adr_ratio: dict[int, Decimal] = {}
    all_ts_set: set[int] = set()
    for closes in ticker_closes.values():
        all_ts_set.update(closes.keys())
    sorted_all_ts = sorted(all_ts_set)
    for ts_ns in sorted_all_ts:
        # market_breadth_pct_above_sma200 — uses already-emitted
        # ``sma_200`` from Phase A so the cohort pass benefits
        # from the existing primitive without recomputing it.
        n_tot = 0
        n_above = 0
        for ticker, closes in ticker_closes.items():
            close = closes.get(ts_ns)
            if close is None:
                continue
            feats_at_ts = panel.get(ticker, {}).get(ts_ns)
            if not feats_at_ts:
                continue
            sma200 = feats_at_ts.get("sma_200")
            if not isinstance(sma200, Decimal):
                continue
            n_tot += 1
            if close > sma200:
                n_above += 1
        if n_tot > 0:
            breadth_pct[ts_ns] = (
                Decimal(n_above) / Decimal(n_tot) * Decimal("100")
            )

        # advance_decline_ratio — needs prev-bar close per ticker.
        # We use each ticker's OWN previous bar (sorted ts list)
        # rather than the cohort prev-ts, matching the intuition
        # of "did this stock close up vs its last bar".
        advancers = 0
        decliners = 0
        for ticker, closes in ticker_closes.items():
            close = closes.get(ts_ns)
            if close is None:
                continue
            sorted_ts = ticker_sorted_ts[ticker]
            try:
                idx = sorted_ts.index(ts_ns)
            except ValueError:
                continue
            if idx == 0:
                continue
            prev_close = closes.get(sorted_ts[idx - 1])
            if prev_close is None:
                continue
            if close > prev_close:
                advancers += 1
            elif close < prev_close:
                decliners += 1
        if decliners > 0:
            adr_ratio[ts_ns] = (
                Decimal(advancers) / Decimal(decliners)
            )

    # ── Emit per-(ticker, ts_ns) ───────────────────────────────
    have_nifty = len(nifty_closes) > 0
    nifty_sorted_ts = sorted(nifty_closes.keys()) if have_nifty else []
    sector_sorted_ts: dict[str, list[int]] = {
        sym: sorted(c.keys()) for sym, c in sector_closes.items()
    }

    def _index_return_at(
        closes: dict[int, Decimal],
        sorted_ts: list[int],
        ts_ns: int,
    ) -> Decimal | None:
        """Same bar-to-bar return rule applied to an index series
        using ITS OWN prior bar (not the equity ticker's prior).
        """
        close = closes.get(ts_ns)
        if close is None:
            return None
        try:
            idx = sorted_ts.index(ts_ns)
        except ValueError:
            return None
        if idx == 0:
            return None
        prev = closes.get(sorted_ts[idx - 1])
        return _bar_to_bar_return(close, prev)

    for ticker, closes in ticker_closes.items():
        ticker_panel = panel.setdefault(ticker, {})
        sorted_ts = ticker_sorted_ts[ticker]
        sector_sym = ticker_to_sector_index.get(ticker)
        for i, ts_ns in enumerate(sorted_ts):
            feats = ticker_panel.setdefault(ts_ns, {})
            # ── Breadth + A/D (cohort-pass, same value per ts) ─
            b = breadth_pct.get(ts_ns)
            if b is not None:
                feats["market_breadth_pct_above_sma200"] = b
            ad = adr_ratio.get(ts_ns)
            if ad is not None:
                feats["advance_decline_ratio"] = ad

            # ── RS vs NIFTY 50 ────────────────────────────────
            if have_nifty and i > 0:
                stock_close = closes.get(ts_ns)
                stock_prev = closes.get(sorted_ts[i - 1])
                stock_ret = _bar_to_bar_return(stock_close, stock_prev)
                nifty_ret = _index_return_at(
                    nifty_closes,
                    nifty_sorted_ts,
                    ts_ns,
                )
                if stock_ret is not None and nifty_ret is not None:
                    feats["rs_vs_nifty_15m"] = stock_ret - nifty_ret

            # ── RS vs sector ──────────────────────────────────
            if sector_sym and i > 0:
                closes_sec = sector_closes.get(sector_sym)
                if closes_sec is not None:
                    stock_close = closes.get(ts_ns)
                    stock_prev = closes.get(sorted_ts[i - 1])
                    stock_ret = _bar_to_bar_return(
                        stock_close,
                        stock_prev,
                    )
                    sec_ret = _index_return_at(
                        closes_sec,
                        sector_sorted_ts[sector_sym],
                        ts_ns,
                    )
                    if stock_ret is not None and sec_ret is not None:
                        feats["rs_vs_sector_15m"] = stock_ret - sec_ret
