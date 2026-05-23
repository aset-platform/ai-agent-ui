# RSI(2) Connors Daily v1 — Strategy Design

| | |
|---|---|
| Date | 2026-05-22 |
| Author | Abhay Kumar Singh |
| Status | Draft (design only — no code) |
| Scope | Long-only daily-cadence CNC swing strategy on the broad NSE stock universe, implementing the canonical Connors RSI(2) mean-reversion rule with a `stress_prob` regime kill-switch |
| Non-goals | Short side, walk-forward / parameter sweep, vol-targeted sizing, sector caps, hard N-day timeout, F&O 200 restriction |
| Reference baseline | `bull_momentum_daily_swing_v4` at 53.6% win rate (this strategy targets 60-70%) |
| Companion specs | `2026-05-22-rule-based-intraday-mis-long-v1-design.md` (MIS v1 — different cadence, same framework patterns) |

## 1. Motivation

### 1.1 Why Connors RSI(2)

This is **the most-replicated retail quant strategy with documented edge** on liquid daily-cadence equities. Larry Connors' 2008 publication ("Short Term Trading Strategies That Work", RSI(2) chapter) and follow-on academic + industry replications consistently report 65-75% win rate, 2-3 day average hold, 8-12% CAGR over multi-year windows in the US large-cap universe. The strategy is well-known precisely because the edge persists despite being public.

The rules are minimal:

- Buy when 2-bar Wilder RSI ≤ 5 AND price > 200-bar SMA
- Sell when close > 5-bar SMA
- Long-only, ~2-3 day average hold

The economic intuition is short-term overshoot: in a long-term uptrend, an extreme 2-day oversold reading is a transient liquidity event (forced sellers, margin calls, options-related selling) that mean-reverts within days as patient buyers re-enter.

### 1.2 Why this strategy now, on this stack

Two reasons:

1. **The bake-off (`2026-05-21-intraday-15m-mis-research-design.md` + 5 follow-up runs) empirically established that `stress_prob` is the single most-stable signal in your feature set.** It appeared in the 5-seed-stable top-K in every one of 5 hypothesis-class experiments (direction, alpha-vs-Nifty, volatility). The strategy below uses it as a regime kill-switch — applying the most-validated finding from this session's research to a strategy shape that historically has its biggest failure mode in regime shifts.

2. **MIS v1 (`2026-05-22-rule-based-intraday-mis-long-v1-design.md`, PR #230) was a clean negative result** — three acceptance gates failed simultaneously, confirming that 15-min individual-stock direction prediction is not viable on this data. Connors RSI(2) operates at a fundamentally different cadence (daily) and different mechanism (multi-day mean reversion, not intraday direction). The negative result rules out a sibling strategy but says nothing about this one — they test different hypotheses on different data.

### 1.3 What this spec ships

A single new daily-cadence strategy template plus minimal feature-engine additions (three lines emitting `rsi_2`, `sma_5`, `distance_from_sma5`). The backtest runner, paper runtime, live runtime, AST framework, promotion workflow all consume the new template as-is.

## 2. Scope decisions (locked during brainstorm)

| Question | Decision | Why |
|---|---|---|
| Universe | Broad NSE stock registry (~800 names, approximates Nifty 500) | Wider net for trade signals; Connors literature uses broad large-cap universes |
| Entry threshold | `rsi_2 <= 5` (Connors' strict canonical threshold) | Sharper signal, higher per-trade win rate |
| Regime gate | `stress_prob < 0.5` (Variant B from brainstorm) | Addresses Connors' best-known failure mode using your most-validated signal |
| Exit rule | `close > sma_5` (canonical Connors exit) | No N-day timeout — framework can't track per-position state; SMA(5) cross-up + 5% stop_loss are the two exits |
| Sizing | 5 positions × 20% equal weight | Connors original; fully invested when all 5 signals fire |
| Side | Long-only | Connors mean-rev shorts on Indian equities are documented to underperform (downside momentum persists) |
| Tuning | Sensible defaults + single backtest + manual iteration | Ship fast; the rules are literature-validated, no tuning needed pre-backtest |

## 3. Architecture

### 3.1 What changes

```
backend/algo/
├── features/
│   └── daily_engine.py
│       — emit rsi_2 = wilder_rsi(closes, 2)
│       — emit sma_5  = sma(closes, 5)
│       — emit distance_from_sma5 = (close - sma_5) / sma_5
└── strategy/
    └── templates/
        └── rsi2_connors_daily_v1.json     ← NEW AST
```

Plus tests:

```
backend/algo/features/tests/test_daily_engine.py
    — 3 tests asserting rsi_2 + sma_5 + distance_from_sma5 emitted
backend/algo/strategy/tests/
    test_template_rsi2_connors_daily_v1.py  ← NEW
    — 4 sanity tests (parse, threshold values, features-used, risk-caps)
```

### 3.2 What stays untouched

- Backtest runner, sim broker, positions tracker, paper runtime, live runtime — all consume the AST as-is via the existing `strategy_adapter.validate_python()` path
- AST framework — uses only existing nodes (`if`, `and`, `compare`, `set_target_weight`, `exit`, `hold`)
- Feature-engine primitives — `wilder_rsi(closes, N)` and `sma(closes, N)` already exist in `backend/algo/features/primitives.py`; the daily engine just needs to call them with `N=2` and `N=5`
- Promotion workflow — standard draft → paper → live applies
- F&O 200 caps convention — irrelevant here (this strategy uses the broad universe; `is_fno: false`)

### 3.3 Where the template lives at runtime

Registered like every other JSON in `templates/` (auto-discovered by the loader) and surfaced in the Strategies admin tab via the existing template registry. No new UI elements.

## 4. AST template

`backend/algo/strategy/templates/rsi2_connors_daily_v1.json`:

```json
{
  "id": "00000000-0000-0000-0000-000000000040",
  "name": "RSI(2) Connors Daily v1 — Long-only mean reversion",
  "universe": {
    "type": "scope",
    "scope": "discovery",
    "filter": {
      "ticker_type": ["stock"],
      "market": "india"
    }
  },
  "schedule": {
    "type": "bar_close",
    "interval": "1d",
    "time": "15:25 IST"
  },
  "rebalance": {
    "type": "daily",
    "max_positions": 5
  },
  "product": "CNC",
  "root": {
    "type": "if",
    "cond": {
      "type": "and",
      "operands": [
        {"type": "compare",
         "left": {"feature": "rsi_2"},
         "op": "<=", "right": {"literal": 5}},
        {"type": "compare",
         "left": {"feature": "distance_from_sma200"},
         "op": ">", "right": {"literal": 0.0}},
        {"type": "compare",
         "left": {"feature": "stress_prob"},
         "op": "<", "right": {"literal": 0.5}}
      ]
    },
    "then": {"type": "set_target_weight", "weight": 0.20},
    "else": {
      "type": "if",
      "cond": {
        "type": "compare",
        "left": {"feature": "distance_from_sma5"},
        "op": ">", "right": {"literal": 0.0}
      },
      "then": {"type": "exit", "scope": "this_symbol"},
      "else": {"type": "hold"}
    }
  },
  "risk": {
    "per_trade": {"stop_loss_pct": 5.0, "max_qty": 10000},
    "portfolio": {"max_exposure_pct": 100.0, "max_concentration_pct": 25.0},
    "daily": {"max_loss_pct": 5.0, "max_open_positions": 5}
  }
}
```

### 4.1 Parameter rationale

| Parameter | Value | Why |
|---|---|---|
| `interval` | `1d` | Daily cadence — the only sensible cadence for Connors RSI(2) |
| `product` | `CNC` | Delivery, multi-day holds (MIS would force same-day square-off, defeating mean-reversion) |
| `time` | `15:25 IST` | Last 5-min before market close — evaluated against today's close |
| `max_positions` | 5 | Connors original; equal-weight 20% each |
| `rsi_2 <= 5` | strict entry | Classic Connors threshold — sharp oversold signal |
| `distance_from_sma200 > 0` | quality filter | Only buy stocks in long-term uptrend — avoids catching falling knives |
| `stress_prob < 0.5` | regime kill-switch | Skip entries during market stress regimes (most-validated signal from session research) |
| `distance_from_sma5 > 0` | exit trigger | `close > SMA(5)` — Connors' canonical exit |
| `set_target_weight: 0.20` | sizing | Equal-weight 5 × 20% = 100% fully invested when all signals fire |
| `stop_loss_pct: 5.0` | per-trade kill | Wider than MIS v1 (5% vs 2%) — daily mean-rev needs room |
| `max_exposure_pct: 100.0` | portfolio cap | Fully invested permitted; Connors is a swing strategy |
| `max_concentration_pct: 25.0` | per-name cap | One name max 25% — covers mid-trade size-adjustment edge cases |
| `max_loss_pct: 5.0` daily | portfolio kill | Daily kill switch — looser than MIS v1's 3% because multi-day strategy expects bigger daily swings |

### 4.2 Three-state holding model

The nested `if/elif/else` structure expresses three states per bar per ticker:

1. **Entry/hold-on-signal** (outer `then`): if all three entry conditions still hold (`rsi_2 ≤ 5`, in uptrend, calm regime), target 20% weight. If currently in position, this just maintains it. If not in position, opens at 20%.

2. **Exit-on-cross-up** (inner `then`): if entry conditions fail BUT `close > sma_5` (price has recovered above 5-day MA), exit `this_symbol`. This is the classic Connors exit signal.

3. **Limbo / hold** (inner `else`): entry conditions failed AND exit not triggered yet — typically `rsi_2 > 5` but `close < sma_5`. `HoldNode` means "no action", which preserves any existing position. This is the multi-day-hold window between entry and exit.

Without a `position_age` feature the framework can't enforce a hard N-day timeout. Per Connors' original spec, the SMA(5) exit fires within 1-5 days on 95%+ of trades; `stop_loss_pct: 5%` is the framework-enforced backstop for the rest.

### 4.3 Behavior summary in English

Every trading day at 15:25 IST, for each stock in the registry:

- If `rsi_2 ≤ 5` AND price is above 200-day SMA AND market stress is below 0.5 → buy 20% of NAV (or maintain if already held — up to 5 concurrent positions)
- Else if close has crossed above the 5-day SMA → sell the position
- Otherwise hold whatever's already held

Per-position stop loss at −5%. Daily kill at −5% NAV. No square-off (CNC), so positions can carry overnight.

### 4.4 What's deliberately NOT in the AST

- No short side — Connors mean-rev shorts on Indian equities documented to underperform
- No sector caps — Connors signals are sparse enough on a broad universe that natural diversification handles it
- No volume confirmation — historical literature shows volume filters reduce Connors win rate
- No earnings/news avoidance — could be a v2 enhancement
- No vol-targeted sizing — equal-weight 20%; `BuyQtyVolTarget` is a follow-up
- No hard N-day timeout — relies on SMA(5) cross-up + 5% stop loss
- No F&O universe restriction — broader universe; the `is_fno` field stays at its default `False`

## 5. Daily-engine feature additions

Three new lines in `backend/algo/features/daily_engine.py`. All use existing primitives in `backend/algo/features/primitives.py`.

| Feature | Formula | Primitive |
|---|---|---|
| `rsi_2` | 2-bar Wilder RSI on close | `p.wilder_rsi(closes, 2)` |
| `sma_5` | 5-bar simple moving average | `p.sma(closes, 5)` |
| `distance_from_sma5` | `(close - sma_5) / sma_5` | One-liner derived; mirrors existing `distance_from_sma200` |

`distance_from_sma200` and `stress_prob` are already emitted today (v4 references `distance_from_sma200`; the regime classifier emits `stress_prob` daily into `stocks.regime_history`, available via the cross-cadence overlay or directly for daily-cadence ASTs).

The implementation plan adds tests confirming all three new features land in the per-bar feature dict with correct values on a hand-built fixture.

## 6. Backtest plan

### 6.1 Single backtest config

```bash
PYTHONPATH=.:backend python -m \
    backend.algo.backtest.runner \
    --strategy-template rsi2_connors_daily_v1 \
    --start 2022-01-01 \
    --end   2026-05-21 \
    --interval 1d \
    --product CNC \
    --nav 1000000 \
    --n-jobs 8 \
    --tag "rsi2-connors-daily-baseline"
```

| Parameter | Value | Why |
|---|---|---|
| Window | 2022-01-01 → 2026-05-21 (~4 yr) | Long window includes COVID rebound, 2022 selloff, 2023-24 bull, 2025-26 sideways/bear — tests stress_prob gate across multiple regimes |
| Cadence | `1d` | Daily bar close evaluation |
| Product | CNC | Delivery, no MIS square-off |
| NAV | ₹10L | Same as MIS v1 for direct comparison |
| Universe | broad stock registry (~800 names) | `is_fno: false` so no F&O intersect |
| Fees | Existing CNC fee model | STT 0.1% buy/sell + brokerage + GST |
| Slippage | Existing daily slippage | Conservative; daily bars use close-to-close fills |

Expected runtime: 5-10 min (daily is ~50× faster than 15m intraday). No 5-seed runs — deterministic rule-based strategy, no model variance.

### 6.2 Metrics extracted

| Metric | Notes |
|---|---|
| Net return % | `(final_nav / 1_000_000 - 1) * 100` |
| **CAGR** | `(final_nav / 1_000_000)^(1/years) - 1` — annualized, key for daily strategy |
| Sharpe (annualized) | `√252` annualization; ~1000 trading days makes this meaningful |
| Win rate (non-stop) | wins / (closes excluding stop-loss exits) |
| Total trades | count of POSITION_OPEN events |
| Avg holding (days) | mean of close.bar_date − open.bar_date |
| Max drawdown % | NAV peak-to-trough |
| Per-regime breakdown | join with `stocks.regime_history.regime_label` — BULL / SIDEWAYS / BEAR |
| % of days skipped by stress_prob gate | measures the cost of the regime gate |
| Per-ticker concentration | `sum(realized_pnl) by ticker` — single-name dominance check |

### 6.3 Acceptance criteria — ship to paper

ALL five must hold for v1 to graduate from `draft` to `paper`:

| Gate | Threshold | Rationale |
|---|---|---|
| **G1: Trade count** | ≥ 200 trades over 4 years | Statistical power floor |
| **G2: CAGR** | ≥ 8% net of fees | Beats cash by a meaningful margin |
| **G3: Win rate (ex-stops)** | **≥ 60%** | The whole point of this strategy; literature is 65-75%, gated at 60% allowing for slippage drag |
| **G4: Max drawdown** | ≤ 15% | More lenient than MIS v1 — daily mean-rev expects bigger DD |
| **G5: Concentration** | No single ticker > 20% of total realized P&L | Same as MIS v1 |

### 6.4 If a gate fails

- **G1 fail** (< 200 trades): regime gate too aggressive. Loosen `stress_prob < 0.5` to `< 0.6`. Re-run. Max one iteration.
- **G2 fail** (CAGR < 8%): do NOT tune; proceed to G3 first.
- **G3 fail** (win rate < 60%): the strategy's central claim is unsupported. Tighten `rsi_2 <= 5` to `<= 3` (Connors' deepest oversold). Max one iteration. If still < 60% → **abandon — Connors does not work in Indian daily equities**.
- **G4 fail** (DD > 15%): tighten `stop_loss_pct` from 5% to 3%, or reduce `weight` from 0.20 to 0.15. Max one iteration.
- **G5 fail**: investigate the offending ticker; typically a corporate-action artifact. Document, possibly exclude.
- **Two or more fail**: do NOT tune. Document negative result. Pivot research direction.

### 6.5 Comparison to v4 daily baseline

| | v4 (existing) | Connors RSI(2) (this spec) |
|---|---|---|
| Approach | Momentum continuation | Mean reversion |
| Direction trigger | RS vs Nifty > 5% | RSI(2) ≤ 5 |
| Regime gate | BULL only | not-BEAR (stress < 0.5) |
| Holding period | longer (days-weeks) | shorter (1-5 days) |
| Win rate target | 53.6% achieved | 60-70% hypothesized |

They compose well as a portfolio — v4 makes money in trending months, Connors makes money in chop months. Different risk profiles, complementary edge. If Connors v1 ships to paper, ultimate composition is v4 + Connors v1 = robust portfolio.

## 7. Promotion path & kill conditions

### 7.1 Lifecycle (per CLAUDE.md §5.16)

```
draft → paper → live
```

| Stage | Gate to enter | Duration | What runs |
|---|---|---|---|
| draft | Spec + AST + feature emissions land + backtest G1-G5 pass | — | Nothing |
| paper | Manual promotion via `algo.strategy.promotion.promote(strategy_id, "paper", reason)` | **45 days minimum** of trading days | Paper runtime emits PAPER_FILL events into `algo.events`; no real Kite orders |
| live | Paper-stage acceptance gates pass + typed-name confirmation per §5.16 | Indefinite | Real Kite CNC orders, multi-day holds |

AST edits between paper and live auto-demote to draft. Same rules as MIS v1.

### 7.2 Paper-stage monitoring (45 days)

Daily check via existing `algo.attribution` endpoints:

- Trade count tracking: paper trades/day ∈ [0.5×, 1.5×] of backtest avg
- Per-trade P&L tracking: within ±50% of backtest at the same elapsed-percentile
- Holding period: ~2-3 days average matches backtest
- Stress-prob skip days: paper observes roughly the same % of days where the regime gate blocks entries
- Order rejection rate: ~0 (CNC on liquid stocks)

### 7.3 Paper → live acceptance gates (45-day window)

| Gate | Threshold |
|---|---|
| P1: Trade count realism | Paper trades/day ∈ [0.5×, 1.5×] of backtest |
| P2: Win rate tracking | Paper win rate ≥ 55% (10pp below backtest target — allows for paper-vs-real divergence) |
| P3: Single-day loss cap | No day-level loss > 2% NAV |
| P4: Order rejection rate | < 1% |
| P5: Trade-count floor | ≥ 30 closed trades over the 45-day window |

### 7.4 Live kill conditions (auto-demote to paper)

- Cumulative live P&L < −10% NAV at any point (looser than MIS v1's −5% — daily strategy has wider expected swings)
- Single-day loss > 5% NAV
- 3 consecutive `max_loss_pct` daily-kill triggers in 30 days
- Order rejection rate > 5% over rolling 14-day window
- AST edit on live strategy → forced demotion to draft

Demotion writes `algo.strategy_mode_transitions` row with `reason`.

## 8. Non-goals (explicit out-of-scope)

- Short side — Connors mean-rev shorts on Indian equities documented to underperform
- Walk-forward / hyperparameter sweep — follow-up if v1 ships to paper
- Earnings/dividend ex-date avoidance — v2 candidate
- Vol-targeted sizing — equal-weight 20%; vol-target is a follow-up
- Sector caps — single-name 25% is the only concentration limit
- Hard N-day timeout — framework doesn't track `days_held`
- F&O 200 restriction — broader universe per scoping
- Portfolio composition with v4 — deployment decision, not spec decision

## 9. References

- Bake-off design + plan + PR #229 (where `stress_prob`'s stability was empirically confirmed across 5 runs)
- MIS v1 spec + PR #230 (sibling spec; shares framework patterns and operator-driven promotion workflow)
- v4 daily template `backend/algo/strategy/templates/bull_momentum_daily_swing_v4.json` (53.6% baseline this strategy aims to beat by 6-15 pp)
- Connors & Alvarez (2008), "Short Term Trading Strategies That Work" — RSI(2) chapter
- Daily feature engine: `backend/algo/features/daily_engine.py`
- Backtest runner: `backend/algo/backtest/runner.py`
- Promotion workflow: `backend/algo/strategy/promotion.py` + CLAUDE.md §5.16
