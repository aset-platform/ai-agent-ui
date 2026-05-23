# RSI(2) Connors Daily v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a canonical Connors RSI(2) daily mean-reversion strategy on the broad NSE stock universe, with a `stress_prob` regime kill-switch sourced from the bake-off's most-validated feature.

**Architecture:** Single new AST template (`rsi2_connors_daily_v1.json`) plus three feature emissions in `backend/algo/features/daily_engine.py` — `rsi_2`, `sma_5`, `distance_from_sma5`. No harness changes — the backtest runner, paper runtime, and live runtime all consume the new template via the existing `strategy_adapter.validate_python()` + `assemble_per_bar_features` paths.

**Tech Stack:** Python 3.12 · Pydantic v2 (AST schema) · existing backtest runner (`backend/algo/backtest/runner.py`) · pytest. Spec: `docs/superpowers/specs/2026-05-22-rsi2-connors-daily-design.md`.

---

## File Structure

| Path | Action | Purpose | LOC |
|---|---|---|---|
| `backend/algo/features/daily_engine.py` | modify | Add `5` to `DEFAULT_DAILY_SMA_WINDOWS`; emit `rsi_2` + `distance_from_sma5` | +10 |
| `backend/algo/features/tests/test_daily_engine.py` | modify | 3 new tests for the 3 new features | +60 |
| `backend/algo/strategy/templates/rsi2_connors_daily_v1.json` | create | The AST template | ~55 lines JSON |
| `backend/algo/strategy/tests/test_template_rsi2_connors_daily_v1.py` | create | Sanity tests on the template (parse, threshold values, features-used, risk-caps) | +60 |
| `scripts/run_rsi2_connors_baseline.py` | create | Reproducible baseline runner (mirrors MIS v1's pattern) | ~150 |
| `docs/research/2026-05-22-rsi2-connors-daily-baseline.md` | create | G1-G5 triage report | ~100 |

Working branch: `strategy/rsi2-connors-daily-spec` (spec already committed at `32749e5`). All implementation tasks land additional commits; final PR squash-merges to `dev`.

---

## Task 1: Daily-engine feature emissions (TDD)

**Files:**
- Modify: `backend/algo/features/daily_engine.py`
- Modify: `backend/algo/features/tests/test_daily_engine.py`

### Step 1: Write the failing tests

Append to `backend/algo/features/tests/test_daily_engine.py`. The existing test pattern uses a hand-built bar series; mirror that style.

```python
def test_compute_daily_features_emits_rsi_2():
    """rsi_2 emitted with at least 2 prior closes available."""
    from decimal import Decimal
    from backend.algo.features.daily_engine import compute_daily_features
    from backend.algo.backtest.types import BarData
    from datetime import date

    # Build a 250-bar series so RSI / SMAs all settle.
    series = []
    closes = [100.0]
    # Alternating up/down so RSI is neither 0 nor 100.
    for i in range(1, 250):
        delta = 0.5 if i % 2 == 0 else -0.4
        closes.append(closes[-1] + delta)
    for i, c in enumerate(closes):
        series.append(BarData(
            bar_date=date(2025, 1, 1),
            open=Decimal(str(c)), high=Decimal(str(c + 0.1)),
            low=Decimal(str(c - 0.1)), close=Decimal(str(c)),
            volume=Decimal("1000"),
        ))
    out = compute_daily_features(series)
    # Last bar should have rsi_2 emitted.
    last_key = max(out.keys())
    feats = out[last_key]
    assert "rsi_2" in feats, f"rsi_2 missing; got keys {sorted(feats.keys())}"
    assert 0 <= float(feats["rsi_2"]) <= 100


def test_compute_daily_features_emits_sma_5():
    """sma_5 emitted once at least 5 closes are available."""
    from decimal import Decimal
    from backend.algo.features.daily_engine import compute_daily_features
    from backend.algo.backtest.types import BarData
    from datetime import date

    closes = [100.0 + i * 0.1 for i in range(250)]
    series = [
        BarData(
            bar_date=date(2025, 1, 1),
            open=Decimal(str(c)), high=Decimal(str(c + 0.1)),
            low=Decimal(str(c - 0.1)), close=Decimal(str(c)),
            volume=Decimal("1000"),
        )
        for c in closes
    ]
    out = compute_daily_features(series)
    last_key = max(out.keys())
    feats = out[last_key]
    assert "sma_5" in feats
    # sma_5 of monotonic series = average of last 5 closes.
    expected = sum(closes[-5:]) / 5
    assert abs(float(feats["sma_5"]) - expected) < 1e-6


def test_compute_daily_features_emits_distance_from_sma5():
    """distance_from_sma5 = (close - sma_5) / sma_5."""
    from decimal import Decimal
    from backend.algo.features.daily_engine import compute_daily_features
    from backend.algo.backtest.types import BarData
    from datetime import date

    # Build a clean ramp so close > sma_5 is unambiguous.
    closes = [100.0 + i for i in range(250)]
    series = [
        BarData(
            bar_date=date(2025, 1, 1),
            open=Decimal(str(c)), high=Decimal(str(c + 0.1)),
            low=Decimal(str(c - 0.1)), close=Decimal(str(c)),
            volume=Decimal("1000"),
        )
        for c in closes
    ]
    out = compute_daily_features(series)
    last_key = max(out.keys())
    feats = out[last_key]
    assert "distance_from_sma5" in feats
    sma5 = float(feats["sma_5"])
    close = float(closes[-1])
    expected = (close - sma5) / sma5
    assert abs(float(feats["distance_from_sma5"]) - expected) < 1e-6
    # Ramp guarantees close > sma_5 → positive distance.
    assert float(feats["distance_from_sma5"]) > 0
```

### Step 2: Run tests to verify they fail

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/features/tests/test_daily_engine.py::test_compute_daily_features_emits_rsi_2 \
    backend/algo/features/tests/test_daily_engine.py::test_compute_daily_features_emits_sma_5 \
    backend/algo/features/tests/test_daily_engine.py::test_compute_daily_features_emits_distance_from_sma5 -v
```

Expected: 3 failures with `AssertionError: rsi_2 missing` (and similar for the others).

### Step 3: Modify `daily_engine.py`

Three small changes:

**3a.** Change the SMA windows default to include `5`. Find:

```python
DEFAULT_DAILY_SMA_WINDOWS: tuple[int, ...] = (20, 50, 100, 200)
```

Change to:

```python
DEFAULT_DAILY_SMA_WINDOWS: tuple[int, ...] = (5, 20, 50, 100, 200)
```

This automatically gets `sma_5` emitted in the existing `for w in sma_windows_t: feats[f"sma_{w}"] = v` loop — no other change needed for `sma_5`.

**3b.** Compute `rsi_2`. Find this block (around line 81-82):

```python
    rsi_14 = p.wilder_rsi(closes, 14)
    rsi_5 = p.wilder_rsi(closes, 5)
```

Append:

```python
    rsi_2 = p.wilder_rsi(closes, 2)
```

Then in the emission loop (find the existing `rsi_5` emission around line 111-113):

```python
        rsi5_v = rsi_5[i]
        if rsi5_v is not None:
            feats["rsi_5"] = rsi5_v
```

Append:

```python
        rsi2_v = rsi_2[i]
        if rsi2_v is not None:
            feats["rsi_2"] = rsi2_v
```

**3c.** Compute `distance_from_sma5` per-bar. Inside the same per-bar loop, AFTER the `for w in sma_windows_t` block where `sma_5` lands in `feats`, add:

```python
        # distance_from_sma5 = (close - sma_5) / sma_5
        # Skip-emit if sma_5 not yet warm.
        sma5 = feats.get("sma_5")
        if sma5 is not None:
            feats["distance_from_sma5"] = (
                Decimal(str(bar.close)) - sma5
            ) / sma5
```

`bar` is the current bar from the loop variable `for i, bar in enumerate(series)` already present.

### Step 4: Run tests to verify pass

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/features/tests/test_daily_engine.py -v
```

Expected: all existing tests still pass + 3 new tests pass.

If an existing test breaks because the new SMA window (5) is in the default tuple and a parity test asserts the exact set of SMA windows, update that test's expected set to include `5`.

### Step 5: Commit

```bash
git add backend/algo/features/daily_engine.py \
        backend/algo/features/tests/test_daily_engine.py
git commit -m "feat(features): emit rsi_2 + sma_5 + distance_from_sma5 from daily engine"
```

---

## Task 2: Create the AST template JSON

**Files:**
- Create: `backend/algo/strategy/templates/rsi2_connors_daily_v1.json`

### Step 1: Create the template file

```bash
cat > backend/algo/strategy/templates/rsi2_connors_daily_v1.json << 'EOF'
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
EOF
```

### Step 2: Verify JSON is valid

```bash
docker compose exec -T backend python -c \
  "import json; \
   json.load(open('backend/algo/strategy/templates/rsi2_connors_daily_v1.json'))" \
  && echo OK
```

Expected: `OK`.

### Step 3: Verify the existing Strategy parser accepts it

```bash
docker compose exec -T backend python -c \
  "import json; \
   from backend.algo.strategy.ast import parse_strategy; \
   s = parse_strategy(json.load(open( \
       'backend/algo/strategy/templates/rsi2_connors_daily_v1.json'))); \
   print(s.name, s.product, s.schedule.interval)"
```

Expected output:
```
RSI(2) Connors Daily v1 — Long-only mean reversion CNC 1d
```

If parse fails with `ValidationError`, the most common cause is a typo in a node `type` field. Fix and re-run.

### Step 4: Verify UUID doesn't collide with other templates

```bash
grep -h '"id":' backend/algo/strategy/templates/*.json | sort -u
```

Confirm `00000000-0000-0000-0000-000000000040` appears exactly once (from the new file).

### Step 5: Commit

```bash
git add backend/algo/strategy/templates/rsi2_connors_daily_v1.json
git commit -m "feat(strategy): RSI(2) Connors daily v1 template"
```

---

## Task 3: Template-loading tests

**Files:**
- Create: `backend/algo/strategy/tests/test_template_rsi2_connors_daily_v1.py`

### Step 1: Write the tests

```python
"""Sanity tests for rsi2_connors_daily_v1.json."""

import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates"
    / "rsi2_connors_daily_v1.json"
)


@pytest.fixture
def template_dict() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses_cleanly(template_dict):
    s = parse_strategy(template_dict)
    assert s.product == "CNC"
    assert s.schedule.interval == "1d"
    assert s.universe.filter.market == "india"
    assert s.universe.filter.is_fno is False


def test_template_entry_thresholds_match_spec(template_dict):
    """The entry condition's three thresholds: rsi_2<=5,
    distance_from_sma200>0, stress_prob<0.5."""
    entry = template_dict["root"]["cond"]["operands"]
    thresholds = {
        op["left"]["feature"]: (op["op"], op["right"]["literal"])
        for op in entry
    }
    assert thresholds["rsi_2"] == ("<=", 5)
    assert thresholds["distance_from_sma200"] == (">", 0.0)
    assert thresholds["stress_prob"] == ("<", 0.5)


def test_template_exit_is_distance_from_sma5_cross_up(template_dict):
    """Exit branch fires when distance_from_sma5 > 0."""
    exit_branch = template_dict["root"]["else"]
    assert exit_branch["type"] == "if"
    cond = exit_branch["cond"]
    assert cond["left"]["feature"] == "distance_from_sma5"
    assert cond["op"] == ">"
    assert cond["right"]["literal"] == 0.0
    assert exit_branch["then"]["type"] == "exit"
    assert exit_branch["else"]["type"] == "hold"


def test_template_risk_caps_are_conservative(template_dict):
    s = parse_strategy(template_dict)
    assert s.risk.per_trade.stop_loss_pct == 5.0
    assert s.risk.portfolio.max_exposure_pct == 100.0
    assert s.risk.portfolio.max_concentration_pct == 25.0
    assert s.risk.daily.max_loss_pct == 5.0
    assert s.risk.daily.max_open_positions == 5


def test_template_uses_only_expected_features(template_dict):
    """The AST references exactly the 5 features in the spec:
    rsi_2, distance_from_sma200, stress_prob, distance_from_sma5."""
    expected = {
        "rsi_2", "distance_from_sma200", "stress_prob",
        "distance_from_sma5",
    }
    used = set()

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "feature" and isinstance(v, str):
                    used.add(v)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(template_dict["root"])
    extra = used - expected
    assert not extra, f"AST references unexpected features: {extra}"
    missing = expected - used
    assert not missing, f"AST missing expected features: {missing}"
```

### Step 2: Run the tests

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/strategy/tests/test_template_rsi2_connors_daily_v1.py -v
```

Expected: 5 passed.

### Step 3: Commit

```bash
git add backend/algo/strategy/tests/test_template_rsi2_connors_daily_v1.py
git commit -m "test(strategy): sanity tests for RSI(2) Connors daily v1 template"
```

---

## Task 4: Baseline backtest + G1-G5 triage

**This task does not produce code changes — it produces a triage report committed as Markdown plus a reproducible runner script.**

**Files:**
- Create: `scripts/run_rsi2_connors_baseline.py`
- Create: `docs/research/2026-05-22-rsi2-connors-daily-baseline.md`

### Step 1: Write the runner script

Mirror the MIS v1 pattern from `scripts/run_mis_mr_v1_baseline.py` (read it for reference) but with the parameters from spec §6.1:

```python
"""RSI(2) Connors Daily v1 — baseline backtest runner.

Reproducible single-shot backtest invocation. Calls
backend.algo.backtest.runner.run_backtest() directly because
the runner exposes a library entry point but no CLI binary.

Outputs:
    /tmp/rsi2_connors_baseline.log    — full log
    /tmp/rsi2_triage.json             — G1-G5 metrics for the report
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

# Honor 80% CPU cap (8 of 10 cores). Set BEFORE numpy/xgb imports.
os.environ.setdefault("OMP_NUM_THREADS", "8")

import pandas as pd

logger = logging.getLogger("rsi2_baseline")


PERIOD_START = date(2022, 1, 1)
PERIOD_END = date(2026, 5, 21)
NAV_INR = 1_000_000
TAG = "rsi2-connors-daily-baseline"
TEMPLATE_NAME = "rsi2_connors_daily_v1"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("Loading template %s", TEMPLATE_NAME)

    from backend.algo.strategy.ast import parse_strategy
    template_path = (
        Path(__file__).parent.parent
        / "backend/algo/strategy/templates"
        / f"{TEMPLATE_NAME}.json"
    )
    strategy = parse_strategy(json.loads(template_path.read_text()))

    logger.info("Resolving universe (broad NSE stock registry)")
    from backend.algo.backtest.universe import resolve_universe
    import asyncio

    class _UserStub:
        user_id = "system"

    tickers = asyncio.run(
        resolve_universe(user=_UserStub(), strategy=strategy)
    )
    logger.info("Universe size: %d tickers", len(tickers))

    logger.info("Invoking run_backtest %s -> %s NAV=%d",
                PERIOD_START, PERIOD_END, NAV_INR)
    from backend.algo.backtest.runner import run_backtest
    summary = run_backtest(
        strategy=strategy,
        tickers=tickers,
        start_date=PERIOD_START,
        end_date=PERIOD_END,
        starting_nav_inr=NAV_INR,
        tag=TAG,
    )

    # Triage gates per spec §6.3.
    trades = summary.trade_list
    n_trades = len(trades)
    final_nav = float(summary.final_nav_inr)
    net_return_pct = (final_nav / NAV_INR - 1) * 100

    days = (PERIOD_END - PERIOD_START).days
    years = days / 365.25
    cagr_pct = ((final_nav / NAV_INR) ** (1 / years) - 1) * 100 \
        if years > 0 else 0.0

    closes_df = pd.DataFrame([
        {
            "ticker": t.ticker,
            "exit_reason": t.exit_reason,
            "realized_pnl_inr": float(t.realised_pnl_inr),
            "bar_date": t.closed_at_date,
        }
        for t in trades
    ])
    non_stop = closes_df[
        closes_df["exit_reason"] != "stop_loss"
    ]
    if len(non_stop):
        win_rate = (
            (non_stop["realized_pnl_inr"] > 0).sum()
            / len(non_stop) * 100
        )
    else:
        win_rate = 0.0

    daily_pnl = closes_df.groupby("bar_date")[
        "realized_pnl_inr"
    ].sum().sort_index()
    nav_series = NAV_INR + daily_pnl.cumsum()
    peak = nav_series.cummax()
    dd_pct = ((nav_series - peak) / peak).min() * 100 \
        if len(nav_series) else 0.0

    by_ticker = closes_df.groupby("ticker")[
        "realized_pnl_inr"
    ].sum()
    total_pnl = closes_df["realized_pnl_inr"].sum()
    if total_pnl != 0 and len(by_ticker):
        top_pct = abs(
            by_ticker[
                by_ticker.abs().idxmax()
            ] / total_pnl
        ) * 100
    else:
        top_pct = 0.0

    result = {
        "tag": TAG,
        "trades": int(n_trades),
        "net_return_pct": round(net_return_pct, 3),
        "cagr_pct": round(cagr_pct, 3),
        "win_rate_pct": round(win_rate, 2),
        "max_drawdown_pct": round(dd_pct, 3),
        "top_ticker_share_pct": round(top_pct, 2),
        "gates": {
            "G1": "pass" if n_trades >= 200
                  else f"fail: {n_trades} trades < 200",
            "G2": "pass" if cagr_pct >= 8.0
                  else f"fail: CAGR {cagr_pct:.2f}% < 8%",
            "G3": "pass" if win_rate >= 60.0
                  else f"fail: win rate {win_rate:.2f}% < 60%",
            "G4": "pass" if dd_pct >= -15.0
                  else f"fail: DD {dd_pct:.2f}% < -15%",
            "G5": "pass" if abs(top_pct) <= 20.0
                  else f"fail: top ticker {top_pct:.2f}%",
        },
    }
    out_path = Path("/tmp/rsi2_triage.json")
    out_path.write_text(json.dumps(result, indent=2, default=str))
    logger.info("Triage written to %s", out_path)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
```

Note: the exact `BacktestSummary` field names (e.g. `trade_list`, `final_nav_inr`, `realised_pnl_inr`, `closed_at_date`) may differ slightly from the MIS v1 runner. Cross-check against `backend/algo/backtest/types.py` for the actual field names and adjust if needed. If the structure differs significantly, model the metric extraction off the MIS v1 runner at `scripts/run_mis_mr_v1_baseline.py`.

### Step 2: Run the backtest

```bash
docker compose exec -T backend python /app/scripts/run_rsi2_connors_baseline.py \
    2>&1 | tee /tmp/rsi2_baseline.log
```

Expected runtime: 5-10 minutes (daily cadence is ~50× faster than 15m). The script writes `/tmp/rsi2_triage.json` and prints the JSON to stdout.

If the universe-resolve step OOMs (broad stock registry of ~800 names), constrain the universe to Nifty 500 only by editing the script's universe step — but document this in the report's caveats. The plan's intent is broad-stock coverage; OOM is the only acceptable reason to cap.

### Step 3: Write the triage report

Create `docs/research/2026-05-22-rsi2-connors-daily-baseline.md`:

```markdown
# RSI(2) Connors Daily v1 — Baseline Backtest Report

| | |
|---|---|
| Date | 2026-05-22 |
| Run tag | rsi2-connors-daily-baseline |
| Window | 2022-01-01 → 2026-05-21 (~4 yr) |
| Template | rsi2_connors_daily_v1.json |
| NAV | ₹10L (1,000,000 INR) |
| Universe | broad NSE stock registry, ~800 names |
| Script | `scripts/run_rsi2_connors_baseline.py` |

## Acceptance gates (spec §6.3)

| Gate | Threshold | Result | Pass |
|---|---|---|---|
| G1: Trade count | ≥ 200 | <fill from /tmp/rsi2_triage.json> | ... |
| G2: CAGR | ≥ 8% | <fill> | ... |
| G3: Win rate (ex-stops) | ≥ 60% | <fill> | ... |
| G4: Max drawdown | ≤ 15% | <fill> | ... |
| G5: Concentration | ≤ 20% in one name | <fill> | ... |

## Per-regime breakdown

<paste from /tmp/rsi2_triage.json's by_regime table if present; else omit>

## Comparison to v4 baseline

| | v4 (existing) | Connors v1 (this run) |
|---|---|---|
| Win rate | 53.6% | <fill> |
| CAGR | <look up if known, else omit> | <fill> |
| Approach | Momentum | Mean reversion |

## Decision

<one of:
  ship to paper — all 5 gates pass
  hand-tune and re-run once — exactly one gate failed; apply spec §6.4 fix
  negative result, abandon — two or more gates failed
>

## Rationale

<2-3 sentences>
```

Fill in the actual numbers from `/tmp/rsi2_triage.json`. If a gate fails, look up spec §6.4's single-iteration tune rule and apply if exactly one gate failed.

### Step 4: Commit triage report (regardless of outcome)

```bash
git add scripts/run_rsi2_connors_baseline.py \
        docs/research/2026-05-22-rsi2-connors-daily-baseline.md
git commit -m "docs(research): RSI(2) Connors daily v1 baseline backtest triage"
```

### Step 5: If exactly ONE gate fails, apply spec §6.4 single-iteration tune

Per spec §6.4:

- **G1 fail** (< 200 trades): the regime gate is too aggressive. Edit `rsi2_connors_daily_v1.json` to change `stress_prob < 0.5` → `stress_prob < 0.6`. Re-run.
- **G3 fail** (win rate < 60%): tighten `rsi_2 <= 5` → `rsi_2 <= 3` (Connors' deepest oversold). Re-run.
- **G4 fail** (DD > 15%): tighten `stop_loss_pct: 5.0` → `stop_loss_pct: 3.0`, OR reduce `weight: 0.20` → `weight: 0.15`. Re-run.
- **G5 fail**: investigate the offending ticker. Likely a corporate-action artifact. Document in the report, possibly exclude that ticker. No template change.
- **Two or more fail**: do NOT tune. Update the Decision to "Negative result; abandon v1, pivot research direction."

If you tune, commit the template change with message `tune(strategy): adjust <PARAM> per spec §6.4 G<N> fail` and re-run Steps 2-4. Stop after one iteration regardless of outcome.

---

## Task 5: Push + PR

- [ ] **Step 1: Push**

```bash
git push -u origin strategy/rsi2-connors-daily-spec
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev \
  --title "feat(strategy): RSI(2) Connors daily v1 + daily-engine emissions" \
  --body "$(cat <<'EOF'
## Summary
- Add `rsi2_connors_daily_v1` strategy template — long-only daily-cadence mean reversion using canonical Connors RSI(2) rule + `stress_prob` regime kill-switch
- Emit `rsi_2`, `sma_5`, `distance_from_sma5` from `backend/algo/features/daily_engine.py` (existing primitives, 3 new lines)
- Spec: `docs/superpowers/specs/2026-05-22-rsi2-connors-daily-design.md`
- Plan: `docs/superpowers/plans/2026-05-22-rsi2-connors-daily.md`
- Baseline triage: `docs/research/2026-05-22-rsi2-connors-daily-baseline.md`

## Test plan
- [x] `pytest backend/algo/features/tests/test_daily_engine.py` green (3 new emission tests)
- [x] `pytest backend/algo/strategy/tests/test_template_rsi2_connors_daily_v1.py` green (5 tests)
- [x] Template parses through `parse_strategy()`
- [x] Baseline backtest completed, G1-G5 triaged in research doc
- [ ] If gates pass: promote to paper via `algo.strategy.promotion.promote(...)`. No caps.allowed_tickers pre-population needed — broad universe, default-permissive.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Address review, squash-merge per CLAUDE.md §4.4 #27**

---

## Spec-coverage self-review

| Spec section | Plan task |
|---|---|
| §3 Architecture | Task 1 (daily engine), Task 2 (template) |
| §4 AST template + parameter rationale | Task 2 + Task 3 |
| §5 Daily-engine feature additions | Task 1 |
| §6 Backtest plan + G1-G5 | Task 4 |
| §6.4 Single-iteration tune on gate fail | Task 4 Step 5 |
| §7 Promotion path | NOT implemented in code — operator-driven per existing workflow; PR description in Task 5 reminds operator |
| §8 Non-goals | Honored (no shorts, no walk-forward, no vol-targeted sizing, no sector cap) |

**Placeholder scan:** Task 4's report template contains `<fill from /tmp/rsi2_triage.json>` and `<one of: ...>` markers. These are intentional Markdown template fields the implementer fills WITH actual numbers — standard report-template pattern matching MIS v1.

**Type consistency:**
- `rsi_2`, `sma_5`, `distance_from_sma5` consistently spelled across daily_engine emission + test assertions + template JSON + template-test feature-walk
- `BacktestSummary.trade_list` / `final_nav_inr` / `realised_pnl_inr` / `closed_at_date` are speculative field names; Task 4 explicitly tells the implementer to cross-check against `backend/algo/backtest/types.py` and adjust — this is a known-unknown surfaced rather than hidden
- `template_dict` fixture pattern matches the MIS v1 template-test file exactly, so contributors familiar with that pattern recognize this immediately
