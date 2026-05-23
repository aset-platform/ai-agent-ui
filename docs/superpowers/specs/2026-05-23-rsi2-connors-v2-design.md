# RSI(2) Connors Daily v2 — Design

| | |
|---|---|
| Date | 2026-05-23 |
| Jira | ASETPLTFRM-430 |
| Predecessor | `docs/research/2026-05-23-rsi2-connors-stop3-postfix.md` (negative-result triage that motivated v2) |
| v1 spec | `docs/superpowers/specs/2026-05-22-rsi2-connors-daily-design.md` |
| Status | Implementation in flight on `strategy/rsi2-connors-daily-spec` (framework PR #232 merged locally) |

## 1. Why v2

v1 with `stop_loss_pct=3.0` actively enforced fails 4 of 5 gates:

| Gate | v1 (3% stop, post-fix) | Threshold | Status |
|---|---:|---:|---|
| G1 trades | 1,482 | ≥ 200 | ✅ |
| G2 CAGR | -11.33% | ≥ 8% | ❌ |
| G3 win% ex-stops | 80.22% | ≥ 60% | ✅ |
| G4 max DD | -29.80% | ≤ 15% | ❌ |
| G5 concentration | 73.78% (APOLLO.NS) | ≤ 20% | ❌ |

3-stop-level sweep (3% / 5% / 7%) showed G4 floor is **structural** (-28% to -32%) regardless of stop tuning. Mean-reversion strategies live inside the 2-5 day reversion window; price stops fire within that window and truncate winners. **The price stop is not the right risk control for RSI(2).**

## 2. Four structural experiments (run sequentially)

Stop early as soon as one combination passes all 5 gates.

### Experiment 1 — ADTV filter at universe construction (HIGHEST LEVERAGE)

| | |
|---|---|
| Hypothesis | Concentration outliers (APOLLO.NS @ 73%, JAIBALAJI.NS, DIACABS.NS) are illiquid names whose entry-then-stop-out cycles dominate P&L. The strategy has no liquidity floor today. |
| Mechanism | Add `min_adtv_inr: float \| None = None` to `UniverseFilter` (AST). `resolve_universe()` reads latest `algo.universe_snapshot.adtv_inr_60d` and drops tickers below the floor. |
| Setting | `min_adtv_inr: 50_000_000` (₹5 Cr/day) — matches existing slippage classifier's mid-cap threshold. Tighter than the snapshot's `ADTV_MIN_INR=100_000_000` (₹10 Cr) so the filter actually constrains (most snapshot rows already at ≥ 10 Cr). |
| Expected | G5 concentration drops sharply; G4 improves moderately (less cluster-stop on low-float names); G2 may improve as illiquid loss-leaders removed. |

### Experiment 2 — Concurrent-entry cap (if Experiment 1 insufficient)

| | |
|---|---|
| Hypothesis | Multiple oversold scans fire on the same red day → cluster entries → all stop out together → larger single-day drawdown than letting positions ride. |
| Mechanism | Add `max_concurrent_entries: int \| None = None` to `RiskDaily`. Backtest runner enforces per-bar new-entry cap (existing positions unaffected). |
| Setting | `max_concurrent_entries: 2` (cap new entries to 2 per bar across the strategy's 5 max-positions). |
| Expected | Smoother daily P&L; G4 improves; CAGR may drop slightly. |

### Experiment 3 — Time-based stop replaces price stop (structural pivot)

| | |
|---|---|
| Hypothesis | Per Connors's published RSI(2) work, mean reversion completes in 2-5 trading days OR fails to materialize. A price stop fires INSIDE the reversion window; a time stop fires AFTER it. |
| Mechanism | Add `max_holding_days: int \| None = None` to `RiskPerTrade`. Backtest runner monitors per-position bar-count-since-open; force-exit at next bar open after threshold. |
| Setting | `max_holding_days: 5`, `stop_loss_pct: 0.0` (price stop disabled). |
| Expected | Stop-out rate drops to 0%; G3 win rate INCLUDING stops rises significantly (no premature stops); G4 may improve dramatically (drawdowns capped by holding-period exits not panic exits). |

### Experiment 4 — Regime gate (last resort)

| | |
|---|---|
| Hypothesis | Buy oversold-in-bear only when broader market hasn't joined the panic. Skip entries when NIFTY's own RSI(2) ≤ 10. |
| Mechanism | Add an AST condition referencing the `nifty_rsi_2` or `regime_label` feature (verify which exists). |
| Expected | Trade count drops; G4 improves materially; G2 likely lower but Sharpe higher. |

## 3. Scope of THIS spec

**Experiment 1 only** as the first implementation step. If E1 passes all 5 gates, ship v2 with just the ADTV filter and close ASETPLTFRM-430. If E1 fails one or more gates, layer E2 (and so on), updating this spec inline with results.

## 4. Implementation plan — Experiment 1

### 4.1 Schema change

`backend/algo/strategy/ast.py` — `UniverseFilter`:

```python
class UniverseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker_type: list[Literal["stock", "etf"]] = Field(min_length=1)
    market: Literal["india", "us", "all"] = "india"
    # NEW: liquidity floor. None = no filter. Value in INR/day
    # checked against algo.universe_snapshot.adtv_inr_60d (latest
    # snapshot). Tickers below the floor are dropped at universe-
    # resolution time, before any AST evaluation runs.
    min_adtv_inr: float | None = Field(default=None, ge=0)
```

### 4.2 Resolver change

`backend/algo/backtest/universe.py` — after the existing `_apply_filter` call:

```python
# NEW: liquidity floor against algo.universe_snapshot
min_adtv = getattr(filter_obj, "min_adtv_inr", None)
if min_adtv is not None and min_adtv > 0:
    snapshot_adtv = _load_snapshot_adtv()
    filtered = [
        t for t in filtered
        if snapshot_adtv.get(t, 0.0) >= min_adtv
    ]
```

Where `_load_snapshot_adtv()` queries `algo.universe_snapshot` for the latest `rebalance_date` and returns `{ticker: adtv_inr_60d}`.

### 4.3 v2 template

Copy `rsi2_connors_daily_v1.json` to `rsi2_connors_daily_v2.json`:
- `id`: new UUID
- `name`: "RSI(2) Connors Daily v2 — Long-only mean reversion + ADTV filter"
- `universe.filter.min_adtv_inr`: 50_000_000
- `risk.per_trade.stop_loss_pct`: keep at 3.0 for direct comparability with v1 post-fix run

### 4.4 Tests

1. Unit test: `UniverseFilter(min_adtv_inr=50_000_000)` parses; `min_adtv_inr=-1` rejected.
2. Integration test: `resolve_universe` with `min_adtv_inr=50_000_000` returns fewer tickers than without (use existing fixture or mock the snapshot loader).
3. Template parse: `rsi2_connors_daily_v2.json` parses cleanly via `parse_strategy()`.

### 4.5 Backtest re-run

Same setup as the v1 post-fix run:
- Period 2022-01-01 → 2026-05-21
- NAV ₹10 lakh
- ex-DIACABS still excluded (out-of-distribution single ticker)
- Tag: `rsi2-connors-v2-adtv50cr-stop3`

Compare against v1 post-fix at stop=3. Headline question: G4 ≤ -15%? G5 ≤ 20%?

## 5. Acceptance gates (same as v1)

| Gate | Threshold |
|---|---:|
| G1 trade count | ≥ 200 |
| G2 CAGR | ≥ 8% |
| G3 win rate (ex-stops) | ≥ 60% |
| G4 max drawdown | ≤ 15% |
| G5 concentration | ≤ 20% |

If all 5 pass → ship v2 template, close ASETPLTFRM-430, raise paper promotion.
If any fail → layer next experiment, update spec, re-run.

## 6. Non-goals

- Walk-forward optimization
- Multi-period backtest matrix
- Cross-validation with other liquidity sources (only `algo.universe_snapshot.adtv_inr_60d`)
- Per-bar PIT universe resolution (one-shot resolution at backtest start is fine for v2)
- Frontend feature catalog changes (no new features added — only filter logic)
