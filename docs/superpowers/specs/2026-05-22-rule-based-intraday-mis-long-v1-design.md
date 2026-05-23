# MIS Intraday Mean-Reversion Long v1 — Strategy Design

| | |
|---|---|
| Date | 2026-05-22 |
| Author | Abhay Kumar Singh |
| Status | Draft (design only — no code) |
| Scope | Rule-based long-only 15m MIS strategy for NSE F&O 200 universe, using gate + trigger features identified by the 2026-05-21 bake-off |
| Non-goals | Short side (framework extension required), walk-forward / hyperparameter optimization, sizing optimization, sector-balanced selection, UI changes |
| Predecessor | `docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md` + five bake-off runs documented under §1.2 |
| Follow-up spec(s) | (a) MIS short-side AST framework extension, (b) walk-forward parameter sweep if v1 ships to paper |

## 1. Motivation

### 1.1 What the bake-off told us

The 2026-05-21 feature-importance bake-off ran five experiments on 209 F&O 200 names over 6 months of 15-min bars:

| Run | Hypothesis | Best iter | Gate-4 Δ (model − baseline) |
|---|---|---:|---:|
| H=4 direction | absolute 1-hr direction | 14 | −0.012 |
| H=16 direction | absolute 4-hr direction | 2 | −0.092 |
| H=4 B' alpha-vs-Nifty | direction net of market beta | 0 | −0.050 |
| H=4 vol (broken formula) | 3-class realized-range tercile | 399 | −0.372 |
| H=4 vol (fixed formula) | same, with `expected_range = atr·√H` | 1 | −0.452 |

Every run failed Gate 4 (no XGBoost classifier on the FE-1..FE-14 feature set could beat a stratified-random predictor by 0.05 logloss on direction, alpha, or vol-tercile).

But three to five of the same features kept landing in the 5-seed-stable set across every run:

| Feature | Appeared in |
|---|---|
| `market_breadth_pct_above_sma200` | 5/5 runs |
| `stress_prob` | 4/5 |
| `minutes_since_open` | 3/5 |
| `rsi_5` | 2/5 |
| `gap_pct` | 2/5 |

The interpretation: these features encode **conditions, not predictions**. A classifier can't extract directional alpha from them, but they cleanly partition "when to consider trading" from "when not to". The right strategy shape is therefore a rule-based regime-gated entry rather than an ML classifier — exactly what this spec builds.

### 1.2 What this spec ships

A single new strategy template (`mis_intraday_meanrev_long_v1`) plus the smallest framework changes required to run it: an `is_fno` field on `UniverseFilter` and a runtime F&O-200 whitelist hook. Long-only mean reversion: when the market is calm and broad-based, fade extreme oversold 15-min RSI on F&O names during the mid-session window.

The short leg is deferred. The current AST framework is long-only (no `SetShortTargetWeightNode`, `SetTargetWeightNode.weight` clamped `[0, 1]`, `SellNode` semantics are "reduce existing long"). Adding shorts touches 6 layers (AST schema, backtest runner, sim broker, positions tracker, paper runtime, live runtime) and is its own spec.

## 2. Scope decisions (locked during brainstorm)

| Question | Decision | Why |
|---|---|---|
| Tuning approach | Sensible defaults + single backtest + manual iteration | Ship fast; the bake-off already told us which features to use; param sweep is a follow-up if v1 produces a positive baseline |
| AST structure | One AST (not paired long+short) | Long-only v1; short leg waits for framework extension |
| Strategy shape | Mean-reversion long, no short side | The 5 stable features map cleanly: 3 gates (market_breadth + stress_prob + minutes_since_open) + 2 triggers (rsi_5 + gap_pct). Classic intraday MR fits |
| F&O 200 restriction | Extend `UniverseFilter.is_fno: bool` + runtime whitelist hook | Liquidity floor for MIS exits; reusable for future MIS strategies |
| Backtest cadence | Single 6-month backtest, no walk-forward | Matches Section-1 path choice; compare directly against bake-off null result |

## 3. Architecture

### 3.1 What changes

```
backend/algo/
├── strategy/
│   ├── ast.py                    — extend UniverseFilter with is_fno: bool
│   └── templates/
│       └── mis_intraday_meanrev_long_v1.json   ← NEW AST template
└── backtest/
    └── universe.py               — runtime F&O 200 whitelist filter
                                    when strategy.universe.filter.is_fno is True
```

Plus a parallel hook in LiveRuntime. The exact file path that owns live universe expansion is intentionally not pinned in this spec — the plan stage's first task is to locate it (grep for callers of `Strategy.universe` in the live runtime) and wire the same `is_fno` filter there. This avoids a guessed file path that drifts as the runtime evolves.

### 3.2 What stays untouched

- The backtest runner, sim broker, positions tracker, paper runtime — all consume the AST as-is via `strategy_adapter.validate_python()`.
- The feature engine FE-1..FE-14 — all 5 features used by this AST are emitted today.
- The promotion workflow (`backend/algo/strategy/promotion.py`) — standard draft → paper → live applies.
- The 2026-05-21 bake-off research subtree — read-only reference; its `fno_200.csv` becomes the source of truth for the F&O whitelist.

### 3.3 Where the AST lives at runtime

The template is registered like every other in `templates/` and surfaced in the Strategies admin tab via the existing template registry. No new UI elements.

## 4. AST template

`backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json`:

```json
{
  "id": "00000000-0000-0000-0000-000000000030",
  "name": "MIS Intraday MR v1 — Long-only F&O",
  "universe": {
    "type": "scope",
    "scope": "discovery",
    "filter": {
      "ticker_type": ["stock"],
      "market": "india",
      "is_fno": true
    }
  },
  "schedule": {
    "type": "bar_close",
    "interval": "15m",
    "time": "15:00 IST"
  },
  "rebalance": {
    "type": "daily",
    "max_positions": 8
  },
  "product": "MIS",
  "square_off_time": "15:14 IST",
  "entry_cutoff_time": "13:45 IST",
  "root": {
    "type": "if",
    "cond": {
      "type": "and",
      "operands": [
        {"type": "compare",
         "left": {"feature": "market_breadth_pct_above_sma200"},
         "op": ">=", "right": {"literal": 0.50}},
        {"type": "compare",
         "left": {"feature": "stress_prob"},
         "op": "<=", "right": {"literal": 0.40}},
        {"type": "between",
         "value": {"feature": "minutes_since_open"},
         "low": {"literal": 30}, "high": {"literal": 270}}
      ]
    },
    "then": {
      "type": "if",
      "cond": {
        "type": "and",
        "operands": [
          {"type": "compare",
           "left": {"feature": "rsi_5"},
           "op": "<=", "right": {"literal": 25}},
          {"type": "compare",
           "left": {"feature": "gap_pct"},
           "op": ">=", "right": {"literal": -1.5}}
        ]
      },
      "then": {"type": "set_target_weight", "weight": 0.05},
      "else": {"type": "exit", "scope": "this_symbol"}
    },
    "else": {"type": "exit", "scope": "this_symbol"}
  },
  "risk": {
    "per_trade": {"stop_loss_pct": 2.0, "max_qty": 1000},
    "portfolio": {"max_exposure_pct": 40.0, "max_concentration_pct": 8.0},
    "daily": {"max_loss_pct": 3.0, "max_open_positions": 8}
  }
}
```

### 4.1 Parameter rationale

| Parameter | Value | Why |
|---|---|---|
| `interval` | `15m` | Matches bake-off cadence; the feature-importance evidence is at this granularity |
| `product` | `MIS` | Intraday only; defines what we're testing |
| `square_off_time` | `15:14 IST` | Default (1 min before broker auto-square) |
| `entry_cutoff_time` | `13:45 IST` | 90-min minimum holding window before square-off (overrides AST's 60-min default) |
| `max_positions` | 8 | Moderate concentration; 8 × 5% = 40% gross exposure |
| `market_breadth_pct_above_sma200 >= 0.50` | regime gate | At least half the F&O universe trading above its 200-bar SMA = broad market healthy |
| `stress_prob <= 0.40` | regime gate | Below the 0.5 mid-stress threshold; trade only in calm/normal regime |
| `minutes_since_open ∈ [30, 270]` | time gate | 09:45-13:45 IST — skip opening volatility + late-day MIS-pressure |
| `rsi_5 <= 25` | entry trigger | Oversold (mean-reversion long); 25 vs 30 is conservative |
| `gap_pct >= -1.5%` | quality filter | Avoid catching falling-knife gap-downs |
| `weight = 0.05` | sizing | Flat 5% per position; matches max_concentration_pct + 8 × 5% = 40% gross |
| `stop_loss_pct: 2.0` | per-trade kill | Tight stop; MR needs tight risk |
| `max_loss_pct: 3.0` daily | portfolio kill | Conservative; one bad day stops trading |

### 4.2 Behavior summary in English

Every 15m bar close between 09:45 and 13:45 IST, for each F&O 200 ticker:

- If broad-market breadth is ≥ 50% above SMA200 AND stress probability is ≤ 0.40:
  - If `rsi_5 ≤ 25` AND `gap_pct ≥ −1.5%`: buy at the next bar open up to 5% of NAV
  - Otherwise: exit any existing position in this symbol
- Otherwise: exit any existing position in this symbol

All positions held until `rsi_5` normalizes (next bar evaluation flips the trigger), OR `stop_loss_pct` hits, OR 13:45 IST entry cutoff (existing positions still held, just no new entries), OR 15:14 IST forced square-off. Daily kill at −3% NAV.

### 4.3 What's deliberately NOT in the AST

- **No short side** — long-only v1; short leg requires AST framework extension (separate spec)
- **No volume confirmation** — `relative_volume` was NOT in the bake-off's stable set; we trust the rule without it
- **No vol-targeted sizing** — `BuyQtyVolTarget` exists but adds complexity; equal-weight 5% for v1, vol-targeting is a follow-up if alpha is real
- **No sector cap beyond the per-name 8% concentration** — if 8 positions all land in IT, that's allowed
- **No multi-leg / hedged structures** — single-name longs only

## 5. F&O 200 universe restriction + runtime feature dependencies

### 5.1 `UniverseFilter.is_fno` extension

The AST's `UniverseFilter` today supports only `ticker_type` and `market`. Long-only doesn't strictly require F&O restriction (we're not shorting), but liquidity matters for MIS — a position in an illiquid name can't be exited cleanly at 15:14. The F&O 200 list is our proxy for "liquid enough for MIS".

Concrete change to `backend/algo/strategy/ast.py`:

```python
class UniverseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker_type: list[Literal["stock", "etf"]] = Field(min_length=1)
    market: Literal["india", "us", "all"] = "india"
    is_fno: bool = False    # NEW — intersect with F&O list at runtime
```

Backward-compatible: existing strategies all default to `is_fno: false`. The new AST sets `is_fno: true`.

### 5.2 Runtime whitelist hook

In `backend/algo/backtest/universe.py` (and the equivalent in LiveRuntime — path resolved at plan time), add a post-filter that intersects the resolved universe with the F&O 200 list whenever `strategy.universe.filter.is_fno` is True. Source: `backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv` (209 tickers; loader already exists at `backend/algo/research/intraday_15m_mis_bakeoff/universe.py::load_fno_universe()`).

The CSV refresh policy is quarterly per the bake-off spec; out of scope here.

### 5.3 Runtime feature dependencies

All 5 features the strategy uses are emitted by FE-1..FE-14 today:

| Feature | Source | Cadence | Runtime path |
|---|---|---|---|
| `market_breadth_pct_above_sma200` | `stocks.regime_history` daily snapshot | Daily | Cross-cadence overlay (ASETPLTFRM-419/420) — LiveRuntime preloads at startup, refreshes at EOD |
| `stress_prob` | `stocks.regime_history` | Daily | Same as above |
| `minutes_since_open` | Computed from `bar_open_ts_ns` and 09:15 IST | Per-bar | `backend/algo/features/per_bar.py::assemble_per_bar_features` |
| `rsi_5` | 5-bar RSI on close | Per-bar | `backend/algo/features/engine.py::compute_intraday_features` |
| `gap_pct` | First-bar gap vs prior day close | Per-day, available from bar 0 onwards | Same |

**Cross-cadence overlay integration test**: `market_breadth_pct_above_sma200` and `stress_prob` are daily features referenced by a 15m AST. The runner's feature lookup must inject the most-recent daily value at each 15m bar evaluation. ASETPLTFRM-419 (FE-15b) wired this. The plan MUST include a single integration test confirming the 15m runner serves daily-overlay values to this AST at evaluation time. If broken, the strategy silently fails to gate and emits orders on every bar.

## 6. Backtest plan

### 6.1 Single backtest config

```bash
PYTHONPATH=.:backend python -m backend.algo.backtest.runner \
    --strategy-template mis_intraday_meanrev_long_v1 \
    --start 2025-11-17 \
    --end   2026-05-21 \
    --interval 15m \
    --product MIS \
    --nav 1000000 \
    --tag "intraday-mr-v1-baseline"
```

| Parameter | Value | Why |
|---|---|---|
| Window | 2025-11-17 → 2026-05-21 (6 mo) | Same as bake-off; lets us compare against the null result |
| NAV | ₹10L | Standard default; max single position 5% = ₹50K, above any F&O 200 lot size |
| Fees | Existing MIS fee model | PR #221 Slice 4 (Zerodha MIS fees + STT + GST) |
| Slippage | Existing intraday slippage model | Bar-close fills with realistic spread |
| Universe | F&O 200 (intersected via §5) | Liquidity floor for MIS exits |

No walk-forward in v1.

### 6.2 Metrics extracted from `algo.runs` + `algo.events`

| Metric | Computed from |
|---|---|
| Total net return % | `run_summary.final_nav / 1_000_000 - 1` |
| Sharpe (annualized) | Daily P&L series; `√(252/N_days)` annualization |
| Win rate (excluding stop-outs) | `count(events.realized_pnl > 0) / count(events where exit_reason is NOT a stop-loss)` — the plan enumerates the concrete `exit_reason` enum values from `algo.events` schema (typically square-off, signal-flip, entry-cutoff close, etc.) |
| Total trades | `count(events.event_type = 'POSITION_OPEN')` |
| Avg holding (mins) | Mean of close.ts − open.ts |
| Max drawdown % | NAV high-water-mark on the daily series |
| Per-regime breakdown | Join `algo.events.bar_date` with `stocks.regime_history.regime_label` |
| Per-ticker concentration | `count(events) by ticker` + `sum(realized_pnl) by ticker` |
| Daily kill triggers | `count(days where daily_pnl < -3%)` |
| Entry-cutoff effect | `count(days where strategy attempted entry after 13:45)` — must be 0 |

### 6.3 Acceptance criteria — ship to paper

ALL five must hold for v1 to graduate from `draft` to `paper`:

| Gate | Threshold | Source |
|---|---|---|
| G1: Trade count | ≥ 100 trades over 6 months | Statistical power floor |
| G2: Net return | > 0% net of all fees + slippage | Trivial bar; below = burning capital |
| G3: Win rate (excluding stop-outs) | ≥ 50% | Match v4 daily baseline floor (53.6%); below 50% = anti-predictive |
| G4: Max drawdown | ≤ 5% | Daily kill is 3%; total DD should respect it |
| G5: Concentration check | No single ticker > 20% of total realized P&L | Avoids lucky-name attribution |

### 6.4 If a gate fails

- **G1 fail**: loosen ONE gate (e.g. `market_breadth >= 0.40` or extend `minutes_since_open` to [15, 285]). Re-backtest. Max one iteration.
- **G2/G3 fail**: hand-tune `rsi_5` to 20 (stricter oversold). Re-backtest. Max one iteration.
- **G4 fail**: tighten `stop_loss_pct` to 1.5%, or reduce `weight` to 0.04. Re-backtest.
- **G5 fail**: investigate the offending ticker — likely a corporate-action day. Document, possibly exclude that ticker.
- **Two or more gates fail**: do not tune. Document as negative result. The bake-off was right — rules don't generalize. Pivot research direction.

## 7. Promotion path & kill conditions

### 7.1 Lifecycle (per CLAUDE.md §5.16)

```
draft → paper → live
```

| Stage | Gate to enter | Duration | What runs |
|---|---|---|---|
| draft | Spec merged + AST template registered + backtest gates G1-G5 pass | — | Nothing yet |
| paper | Manual promotion via `algo.strategy.promotion.promote(strategy_id, "paper", reason)` | **30 days minimum** of live trading hours | Paper runtime emits PAPER_FILL events into `algo.events`; no real Kite orders |
| live | Paper-stage acceptance gates (§7.3) pass + typed-name confirmation per §5.16 | Indefinite | Real Kite MIS orders, real money, real square-off at 15:14 |

AST edits between paper and live force auto-demotion to draft (per §5.16). Bypass-to-live is **not** available for this strategy (no prior live history → no earned re-promotion).

### 7.2 Paper-stage monitoring (30 days)

Daily check via existing `algo.attribution` endpoints (every algo.events-reading panel scopes by mode + dry_run per project memory). Watch:

- **Trade count tracking**: paper should average ~0.5-1.5× the backtest's per-day trade count
- **Per-day P&L vs backtest baseline**: cumulative paper P&L should track within ±50% of cumulative backtest P&L at the same percentile of elapsed time
- **Daily-kill firing**: ≤ 1× per month
- **Order rejection rate**: should be ~0; > 5% means universe filter not working
- **Square-off slippage**: tracked automatically in `algo.events`

### 7.3 Paper → live acceptance gates (30-day window)

ALL must hold:

| Gate | Threshold |
|---|---|
| P1: Trade count realism | Paper trades/day ∈ [0.5×, 1.5×] of backtest |
| P2: Net return tracking | Paper Sharpe ≥ 0.5 × backtest Sharpe |
| P3: Daily-kill rate | ≤ 1 trigger per 30 days |
| P4: Order rejection rate | < 1% |
| P5: No unexpected losses > 1.5% NAV in a single day | Catches tail risk the backtest missed |

### 7.4 Live kill conditions

Auto-demote to paper (no human action) if any of:

- Cumulative live P&L < −5% NAV at any point
- Single-day loss > 3% NAV (= `max_loss_pct` daily gate; framework enforces, this is the alarm)
- 3 consecutive daily-kill triggers in 7 days
- Order rejection rate > 5% over rolling 7-day window
- Spec edit lands on the live AST (forced auto-demote to draft per §5.16)

Demotion writes an `algo.strategy_mode_transitions` row with `reason` populated.

## 8. Non-goals (explicit out-of-scope)

- Short side — separate spec required for AST framework extension
- Walk-forward / hyperparameter optimization — follow-up if v1 ships to paper
- Multi-strategy portfolio composition — single strategy; 40% gross exposure cap so it composes safely with v4 daily and regime templates
- Cross-cadence ensembling — v1 runs alongside, not blended with, v3/v4 daily
- New features — uses only emitted-today features from FE-1..FE-14
- Sizing optimization — flat 5% per position; vol-targeting is a follow-up
- Sector-balanced selection — no sector cap beyond per-name 8% concentration
- UI changes — strategy appears in the existing Strategies admin tab via standard template registration

## 9. References

- Bake-off design: `docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md`
- Bake-off plan: `docs/superpowers/plans/2026-05-21-intraday-15m-mis-research-bakeoff.md`
- Bake-off PR: #229 (`research/intraday-15m-mis-bakeoff-spec`)
- F&O 200 list + loader: `backend/algo/research/intraday_15m_mis_bakeoff/{fno_200.csv,universe.py}`
- Reference 15m template: `backend/algo/strategy/templates/bull_momentum_15m_swing.json`
- AST schema: `backend/algo/strategy/ast.py`
- Backtest runner: `backend/algo/backtest/runner.py`
- Promotion workflow: `backend/algo/strategy/promotion.py` + CLAUDE.md §5.16
- Intraday MIS support tickets: ASETPLTFRM-386 (PR #219), ASETPLTFRM-400 (PR #221 — Slices 1-7), ASETPLTFRM-383 (daily-bar warmup), ASETPLTFRM-419/420 (cross-cadence daily overlay)
- Negative-result evidence: this session's 5 bake-off runs (H=4/16 direction, B' alpha, C v1/v2 volatility) — all failed Gate 4
