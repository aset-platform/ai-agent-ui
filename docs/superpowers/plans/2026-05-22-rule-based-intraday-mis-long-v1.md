# MIS Intraday MR Long v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a long-only 15m MIS mean-reversion strategy AST built directly from the bake-off's stable-feature set, plus the smallest framework extension required to run it on the F&O 200 universe.

**Architecture:** Single new strategy template `mis_intraday_meanrev_long_v1.json` + `UniverseFilter.is_fno: bool` field + F&O 200 whitelist filter in the backtest universe resolver. Paper + live runtimes deliberately untouched — they use `caps.allowed_tickers` populated by the operator at promotion time per CLAUDE.md §5.16.

**Tech Stack:** Python 3.12 · Pydantic v2 (AST schema) · existing backtest runner (`backend/algo/backtest/runner.py`) · pytest. Spec: `docs/superpowers/specs/2026-05-22-rule-based-intraday-mis-long-v1-design.md`.

---

## File Structure

| Path | Action | Purpose | LOC |
|---|---|---|---|
| `backend/algo/strategy/ast.py` | modify | Add `is_fno: bool = False` to `UniverseFilter` | +1 |
| `backend/algo/strategy/tests/test_ast.py` | modify | 3 tests covering default, true, false | +30 |
| `backend/algo/backtest/universe.py` | modify | Apply F&O whitelist after the market+ticker_type filter when `filter.is_fno` is True | +35 |
| `backend/algo/backtest/tests/test_universe.py` | modify or create | 2 tests covering is_fno=True intersect + is_fno=False unchanged | +60 |
| `backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json` | create | The AST template | ~55 lines JSON |
| `backend/algo/strategy/tests/test_template_mis_intraday_meanrev_long_v1.py` | create | Schema + sanity tests | +50 |
| `backend/algo/backtest/tests/test_daily_overlay_for_15m_ast.py` | create | Integration test: 15m AST receives daily-overlay value for `market_breadth_pct_above_sma200` + `stress_prob` | +90 |
| `backend/algo/strategy/promotion.py` and/or `backend/algo/live/caps_repo.py` | NO CHANGE | Operator-driven; documented in README | — |
| `backend/algo/strategy/templates/README.md` (or equivalent) | modify | One-paragraph note documenting the F&O 200 caps convention for MIS strategies | +10 |

Working branch: `strategy/intraday-mr-long-v1-spec` (spec already committed as `c29e85c`). All implementation tasks land additional commits; final PR squash-merges to `dev`.

---

## Task 1: Add `is_fno: bool = False` to `UniverseFilter` (TDD)

**Files:**
- Modify: `backend/algo/strategy/ast.py:249-253`
- Modify: `backend/algo/strategy/tests/test_ast.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/algo/strategy/tests/test_ast.py`:

```python
def test_universe_filter_defaults_is_fno_to_false():
    from backend.algo.strategy.ast import UniverseFilter
    uf = UniverseFilter(ticker_type=["stock"])
    assert uf.is_fno is False


def test_universe_filter_accepts_is_fno_true():
    from backend.algo.strategy.ast import UniverseFilter
    uf = UniverseFilter(ticker_type=["stock"], is_fno=True)
    assert uf.is_fno is True


def test_universe_filter_rejects_extra_fields():
    import pytest
    from pydantic import ValidationError
    from backend.algo.strategy.ast import UniverseFilter
    with pytest.raises(ValidationError):
        UniverseFilter(ticker_type=["stock"], unknown_field=42)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/strategy/tests/test_ast.py::test_universe_filter_defaults_is_fno_to_false \
    backend/algo/strategy/tests/test_ast.py::test_universe_filter_accepts_is_fno_true -v
```

Expected: 2 failures with `TypeError` or `ValidationError` (since `is_fno` doesn't exist yet).

- [ ] **Step 3: Add the field to `UniverseFilter`**

Edit `backend/algo/strategy/ast.py` — change:

```python
class UniverseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker_type: list[Literal["stock", "etf"]] = Field(min_length=1)
    market: Literal["india", "us", "all"] = "india"
```

to:

```python
class UniverseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker_type: list[Literal["stock", "etf"]] = Field(min_length=1)
    market: Literal["india", "us", "all"] = "india"
    # ASETPLTFRM — when True, the backtest universe resolver
    # intersects with the F&O 200 whitelist (live + paper rely
    # on caps.allowed_tickers, not on this field).
    is_fno: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/strategy/tests/test_ast.py -v
```

Expected: all existing tests still pass + 3 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/strategy/ast.py \
        backend/algo/strategy/tests/test_ast.py
git commit -m "feat(strategy): UniverseFilter.is_fno field for F&O 200 backtest filter"
```

---

## Task 2: F&O whitelist filter in `backend/algo/backtest/universe.py` (TDD)

**Files:**
- Modify: `backend/algo/backtest/universe.py`
- Modify or Create: `backend/algo/backtest/tests/test_universe.py`

- [ ] **Step 1: Write the failing tests**

Append to or create `backend/algo/backtest/tests/test_universe.py`:

```python
"""Tests for resolve_universe is_fno post-filter."""

from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from backend.algo.backtest import universe as uni


class _StrategyStub:
    """Minimal stand-in for parsed Strategy with universe.filter."""

    def __init__(self, *, is_fno: bool):
        self.universe = SimpleNamespace(
            scope="discovery",
            filter=SimpleNamespace(
                ticker_type=["stock"],
                market="india",
                is_fno=is_fno,
            ),
        )


@pytest.mark.asyncio
async def test_resolve_universe_filters_to_fno_when_is_fno_true(
    monkeypatch,
):
    """is_fno=True should intersect candidates with fno_200.csv."""
    candidates = [
        "RELIANCE.NS", "HDFCBANK.NS", "INFY.NS",
        "OBSCURE.NS",   # NOT in F&O list
    ]

    async def fake_scoped(*, user, scope):
        return candidates

    monkeypatch.setattr(uni, "_scoped_tickers", fake_scoped)

    def fake_fno_universe():
        return ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS"]

    monkeypatch.setattr(
        "backend.algo.research.intraday_15m_mis_bakeoff.universe."
        "load_fno_universe",
        fake_fno_universe,
    )

    out = await uni.resolve_universe(
        user=SimpleNamespace(),
        strategy=_StrategyStub(is_fno=True),
    )
    assert set(out) == {"RELIANCE.NS", "HDFCBANK.NS", "INFY.NS"}
    assert "OBSCURE.NS" not in out


@pytest.mark.asyncio
async def test_resolve_universe_unchanged_when_is_fno_false(
    monkeypatch,
):
    """is_fno=False preserves the existing behaviour exactly."""
    candidates = ["RELIANCE.NS", "OBSCURE.NS"]

    async def fake_scoped(*, user, scope):
        return candidates

    monkeypatch.setattr(uni, "_scoped_tickers", fake_scoped)

    out = await uni.resolve_universe(
        user=SimpleNamespace(),
        strategy=_StrategyStub(is_fno=False),
    )
    assert set(out) == set(candidates)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_universe.py -v
```

Expected: `test_resolve_universe_filters_to_fno_when_is_fno_true` fails because the resolver doesn't yet apply the F&O filter.

- [ ] **Step 3: Add the F&O post-filter in `resolve_universe`**

Edit `backend/algo/backtest/universe.py`. After the existing `_apply_filter(...)` call but before the `_logger.info(...)` line, add:

```python
    is_fno = bool(getattr(filter_obj, "is_fno", False))
    if is_fno:
        from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
            load_fno_universe,
        )
        fno_set = set(load_fno_universe())
        before = len(filtered)
        filtered = [t for t in filtered if t in fno_set]
        _logger.info(
            "resolve_universe is_fno=True: %d -> %d after F&O intersect",
            before, len(filtered),
        )
```

The existing `_logger.info(...)` line that follows still fires for backwards-compat logging.

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_universe.py -v
```

Expected: both new tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/universe.py \
        backend/algo/backtest/tests/test_universe.py
git commit -m "feat(backtest): F&O 200 intersect in resolve_universe when is_fno=True"
```

---

## Task 3: Create the AST template JSON

**Files:**
- Create: `backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json`

- [ ] **Step 1: Create the template file**

```bash
cat > backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json << 'EOF'
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
EOF
```

- [ ] **Step 2: Verify JSON is valid**

```bash
docker compose exec -T backend python -c \
  "import json; \
   json.load(open('backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json'))" \
  && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Verify the existing Strategy parser accepts it**

```bash
docker compose exec -T backend python -c \
  "import json; \
   from backend.algo.strategy.ast import parse_strategy; \
   s = parse_strategy(json.load(open( \
       'backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json'))); \
   print(s.name, s.product, s.schedule.interval, s.universe.filter.is_fno)"
```

Expected output (one line):
```
MIS Intraday MR v1 — Long-only F&O MIS 15m True
```

If parse fails with `ValidationError`, read the message — most likely cause is a typo in a node `type` field. Fix and re-run.

- [ ] **Step 4: Commit**

```bash
git add backend/algo/strategy/templates/mis_intraday_meanrev_long_v1.json
git commit -m "feat(strategy): MIS intraday mean-rev long v1 template"
```

---

## Task 4: Template-loading tests

**Files:**
- Create: `backend/algo/strategy/tests/test_template_mis_intraday_meanrev_long_v1.py`

- [ ] **Step 1: Write the tests**

```python
"""Sanity tests for mis_intraday_meanrev_long_v1.json."""

import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates"
    / "mis_intraday_meanrev_long_v1.json"
)


@pytest.fixture
def template_dict() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses_cleanly(template_dict):
    s = parse_strategy(template_dict)
    assert s.product == "MIS"
    assert s.schedule.interval == "15m"
    assert s.universe.filter.is_fno is True
    assert s.universe.filter.market == "india"


def test_template_entry_cutoff_is_pinned(template_dict):
    s = parse_strategy(template_dict)
    # We set 13:45 explicitly to override the 60-min default.
    assert s.entry_cutoff_time == "13:45 IST"


def test_template_uses_only_stable_bakeoff_features(template_dict):
    """Spec §4: the AST must only reference the 5 stable features
    from the 2026-05-21 bake-off."""
    stable = {
        "market_breadth_pct_above_sma200",
        "stress_prob",
        "minutes_since_open",
        "rsi_5",
        "gap_pct",
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
    extra = used - stable
    assert not extra, f"AST references non-stable features: {extra}"
    # All 5 stable features should be present in v1.
    missing = stable - used
    assert not missing, f"AST is missing stable features: {missing}"


def test_template_risk_caps_are_conservative(template_dict):
    s = parse_strategy(template_dict)
    assert s.risk.per_trade.stop_loss_pct == 2.0
    assert s.risk.portfolio.max_exposure_pct == 40.0
    assert s.risk.daily.max_loss_pct == 3.0
    assert s.risk.daily.max_open_positions == 8
```

- [ ] **Step 2: Run the tests**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/strategy/tests/test_template_mis_intraday_meanrev_long_v1.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/algo/strategy/tests/test_template_mis_intraday_meanrev_long_v1.py
git commit -m "test(strategy): sanity tests for MIS MR long v1 template"
```

---

## Task 5: Cross-cadence daily-overlay integration test

**Files:**
- Create: `backend/algo/backtest/tests/test_daily_overlay_for_15m_ast.py`

**Rationale:** Spec §5.3 — `market_breadth_pct_above_sma200` and `stress_prob` are DAILY features referenced by a 15m AST. The runner's feature lookup must inject the most-recent daily value at each 15m bar evaluation. ASETPLTFRM-419 (FE-15b) wired this. If broken, the strategy silently fails to gate and emits orders on every bar.

This task does NOT re-implement the overlay — it verifies the existing FE-15b path correctly serves the new AST.

- [ ] **Step 1: Locate the existing overlay implementation**

```bash
grep -rn "daily_overlay\|FE-15b\|market_breadth_pct_above_sma200" \
    backend/algo/backtest/ backend/algo/features/ | head -20
```

Read what the runner does (`backend/algo/backtest/runner.py` line ~177 mentioned the FE-15b cross-cadence overlay in the spec text). Note the function name + module path that resolves daily features for a 15m bar — call this `<overlay_lookup>`.

- [ ] **Step 2: Write the test against a hand-built fixture**

```python
"""FE-15b integration: 15m AST resolves daily-overlay features."""

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest

# Test names a single concrete feature key for readability;
# the underlying mechanism is the same for both regime-overlay keys.
_KEY_BREADTH = "market_breadth_pct_above_sma200"
_KEY_STRESS = "stress_prob"


def _ts_for(d: date, hour: int, minute: int) -> int:
    """IST -> ns-since-epoch helper."""
    ist = timezone(timedelta(hours=5, minutes=30))
    dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=ist)
    return int(dt.timestamp() * 1_000_000_000)


def test_15m_bar_eval_sees_daily_overlay_for_breadth_and_stress(
    monkeypatch,
):
    """A 15m bar at 10:00 IST on 2026-01-05 must see the daily
    market_breadth_pct_above_sma200 and stress_prob values for
    2026-01-05, not None / NaN / missing-key."""
    from backend.algo.strategy.ast import parse_strategy
    from backend.algo.backtest.runner import (
        evaluate_strategy_at_bar,  # name TBD per Step 1 grep
    )

    strategy = parse_strategy({
        "id": "00000000-0000-0000-0000-000000000099",
        "name": "test-daily-overlay",
        "universe": {
            "type": "scope", "scope": "discovery",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {"type": "bar_close", "interval": "15m",
                     "time": "15:00 IST"},
        "rebalance": {"type": "daily", "max_positions": 1},
        "product": "MIS",
        "root": {
            "type": "and",
            "operands": [
                {"type": "compare",
                 "left": {"feature": _KEY_BREADTH},
                 "op": ">=", "right": {"literal": 0.50}},
                {"type": "compare",
                 "left": {"feature": _KEY_STRESS},
                 "op": "<=", "right": {"literal": 0.40}},
            ],
        },
        "risk": {
            "per_trade": {"stop_loss_pct": 2.0, "max_qty": 100},
            "portfolio": {"max_exposure_pct": 5.0,
                          "max_concentration_pct": 5.0},
            "daily": {"max_loss_pct": 3.0, "max_open_positions": 1},
        },
    })

    # Mock the daily-overlay source to return a known value
    # for bar_date 2026-01-05.
    fake_overlay = {
        date(2026, 1, 5): {
            _KEY_BREADTH: Decimal("0.62"),
            _KEY_STRESS: Decimal("0.18"),
        },
    }
    # Patch the runner's overlay reader to return our fake panel
    # (concrete path determined by Step 1 grep — substitute below).
    monkeypatch.setattr(
        "backend.algo.backtest.runner._load_daily_overlay_panel",
        lambda *a, **kw: fake_overlay,
    )

    # Build a minimal bar context at 2026-01-05 10:00 IST.
    bar_ts = _ts_for(date(2026, 1, 5), 10, 0)
    result = evaluate_strategy_at_bar(
        strategy=strategy,
        ticker="RELIANCE.NS",
        bar_open_ts_ns=bar_ts,
        per_bar_features={
            # No regime keys here — they MUST come from overlay.
            "rsi_5": 50.0,
        },
    )
    # Both gate conditions are satisfied (0.62 >= 0.50 AND
    # 0.18 <= 0.40), so the AND root evaluates True.
    assert result is True, (
        f"daily overlay did not flow into 15m eval — got {result!r}"
    )
```

**IMPORTANT — actual symbol names depend on Step 1's grep.** Replace `evaluate_strategy_at_bar` and `_load_daily_overlay_panel` with the real functions the runner exposes. If the runner does not expose a single-bar eval entry point, write the test against the lowest-level helper that DOES take an AST + per-bar feature dict + daily-overlay source. The test's intent is to confirm that the AST receives the overlay value for the bar_date, not to test any specific function signature.

If after grep you find the runner has no easy single-bar eval entry point and the integration would require setting up a whole backtest run, downgrade the test to a unit test on the overlay-lookup helper alone: assert that calling it with `(ticker, bar_open_ts_ns)` returns the expected daily-overlay dict. Keep the high-level intent ("verify FE-15b is wired for our strategy's features") in the docstring.

- [ ] **Step 3: Run the test**

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_daily_overlay_for_15m_ast.py -v
```

Expected: 1 passed. If the test crashes on import (real function names different from the template above), update imports per Step 1's grep and re-run.

- [ ] **Step 4: Commit**

```bash
git add backend/algo/backtest/tests/test_daily_overlay_for_15m_ast.py
git commit -m "test(backtest): FE-15b daily-overlay reaches 15m AST eval for MR v1 features"
```

---

## Task 6: Document the F&O 200 caps convention for paper + live

**Files:**
- Modify or create: `backend/algo/strategy/templates/README.md` (or `docs/algo/strategy-templates.md` if the templates dir has no README)

- [ ] **Step 1: Determine the right doc location**

```bash
ls backend/algo/strategy/templates/README.md 2>/dev/null && echo EXISTS \
  || ls docs/algo/strategy-templates.md 2>/dev/null && echo EXISTS \
  || echo NONE
```

If `EXISTS`, append the section in Step 2 to that file. If `NONE`, create `backend/algo/strategy/templates/README.md` and write Step 2's content as the whole file (it'll be a 30-line file).

- [ ] **Step 2: Add or write the convention section**

```markdown
## F&O 200 universe convention for MIS strategies

For MIS strategies that need to be restricted to liquid F&O underlyings:

- **Backtest**: set `universe.filter.is_fno = true` in the AST. The
  backtest universe resolver intersects with
  `backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv` automatically.
- **Paper and live**: the AST `is_fno` field is NOT honoured by the paper or
  live runtimes. Operators MUST pre-populate the strategy's
  `caps.allowed_tickers` row (PG, `backend/algo/live/caps_repo.py`) with the
  same F&O list at promotion time. The operator can copy the list out of the
  same CSV:

  ```python
  from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
      load_fno_universe,
  )
  tickers = load_fno_universe()
  # Then pass `allowed_tickers=tickers` to the caps upsert.
  ```

  Failure to do this means the live runtime will accept signals on any
  ticker the strategy emits, including illiquid names — for MIS this
  causes square-off slippage and risks broker rejections.

The F&O list is a static quarterly snapshot. Refresh it when NSE rebalances
the F&O list — see `backend/algo/research/intraday_15m_mis_bakeoff/README.md`.
```

- [ ] **Step 3: Commit**

```bash
git add backend/algo/strategy/templates/README.md   # or wherever Step 1 pointed
git commit -m "docs(strategy): F&O 200 caps convention for MIS strategies"
```

---

## Task 7: Run the baseline backtest + triage acceptance gates G1-G5

**This task does not produce code changes — it produces a triage report.** The implementer runs the backtest, queries `algo.runs` + `algo.events`, computes the 5 acceptance gates from spec §6.3, and commits a Markdown report.

**Files:**
- Create: `docs/research/2026-05-22-mis-mr-long-v1-baseline.md`

- [ ] **Step 1: Run the backtest**

```bash
docker compose exec -T backend python -m \
    backend.algo.backtest.runner \
    --strategy-template mis_intraday_meanrev_long_v1 \
    --start 2025-11-17 \
    --end 2026-05-21 \
    --interval 15m \
    --product MIS \
    --nav 1000000 \
    --tag "intraday-mr-v1-baseline" 2>&1 | tee /tmp/mr_v1_baseline.log
```

Expected: completes within ~30 min. Final line should include a `run_id` UUID and a summary JSON with `final_nav`, `total_trades`, `realized_pnl_total`.

If the runner errors out at startup with a CLI flag mismatch, run `python -m backend.algo.backtest.runner --help` and adjust the flag names to match. The flag names above are based on spec §6.1; the runner may use slightly different names (e.g. `--strategy` instead of `--strategy-template`).

If the runner fails because no template named `mis_intraday_meanrev_long_v1` is registered, register the new template — find the template registry (likely `backend/algo/strategy/templates/__init__.py` or a `loader.py` discovered via `grep -rn 'bull_momentum_15m_swing' backend/algo/strategy/`) and add an entry pointing at the new JSON.

- [ ] **Step 2: Enumerate the actual `exit_reason` enum values**

Spec §6.2 deferred this to plan time. Determine the values:

```bash
docker compose exec -T backend python -c "
from backend.db.duckdb_engine import query_iceberg_df
df = query_iceberg_df('algo.events',
    'SELECT DISTINCT exit_reason FROM events '
    \"WHERE event_type = 'POSITION_CLOSE' \"
    'LIMIT 50')
print(df)
"
```

Expected: a list of strings such as `square_off`, `stop_loss`, `signal_exit`, `entry_cutoff`, `daily_kill`, etc. Note these — Step 3 uses them.

- [ ] **Step 3: Extract the G1-G5 metrics and write the triage report**

```bash
docker compose exec -T backend python << 'PY' > /tmp/triage.json
import json
from backend.db.duckdb_engine import query_iceberg_df

TAG = "intraday-mr-v1-baseline"

# Identify the run row.
run = query_iceberg_df("algo.runs",
    f"SELECT * FROM runs WHERE tag = '{TAG}' "
    "ORDER BY started_at DESC LIMIT 1")
print(f"Run row count: {len(run)}")
assert len(run) == 1, "no run with that tag — did the backtest write to algo.runs?"
run_id = run.iloc[0]["run_id"]

# Pull events.
events = query_iceberg_df("algo.events",
    f"SELECT * FROM events WHERE run_id = '{run_id}'")
print(f"Events: {len(events)}")

opens = events[events["event_type"] == "POSITION_OPEN"]
closes = events[events["event_type"] == "POSITION_CLOSE"]

# G1: trade count
g1_trades = len(opens)
g1_pass = g1_trades >= 100

# G2: net return (run row holds final_nav)
final_nav = float(run.iloc[0].get("final_nav", 1_000_000))
net_return_pct = (final_nav / 1_000_000.0 - 1.0) * 100
g2_pass = net_return_pct > 0

# G3: win rate excluding stop-outs
# Use the exit_reason enum from Step 2 — replace the literal set below
# with whatever Step 2 grepped.
NON_STOP_REASONS = {
    "square_off", "signal_exit", "entry_cutoff", "mr_normalize",
}
non_stop = closes[closes["exit_reason"].isin(NON_STOP_REASONS)]
wins = non_stop[non_stop["realized_pnl"] > 0]
win_rate = (len(wins) / len(non_stop) * 100) if len(non_stop) else 0
g3_pass = win_rate >= 50

# G4: max drawdown
# Daily P&L from realized_pnl per close event grouped by bar_date.
import pandas as pd
closes["bar_date"] = pd.to_datetime(closes["bar_open_ts_ns"], unit="ns").dt.date
daily = closes.groupby("bar_date")["realized_pnl"].sum().sort_index()
nav_series = (1_000_000 + daily.cumsum())
peak = nav_series.cummax()
dd_pct = ((nav_series - peak) / peak).min() * 100
g4_pass = dd_pct >= -5.0

# G5: concentration
by_ticker = closes.groupby("ticker")["realized_pnl"].sum()
total_pnl = closes["realized_pnl"].sum()
top_pct = ((by_ticker.max() / total_pnl) * 100) if total_pnl != 0 else 0
g5_pass = abs(top_pct) <= 20.0

# Per-regime breakdown
regime = query_iceberg_df("stocks.regime_history",
    "SELECT bar_date, regime_label FROM regime_history "
    "WHERE bar_date BETWEEN DATE '2025-11-17' "
    "AND DATE '2026-05-21'")
closes = closes.merge(regime, on="bar_date", how="left")
by_regime = closes.groupby("regime_label").agg(
    n=("realized_pnl", "size"),
    pnl=("realized_pnl", "sum"),
    win_rate=("realized_pnl", lambda x: (x > 0).mean() * 100),
).reset_index()

result = {
    "run_id": str(run_id),
    "tag": TAG,
    "trades": int(g1_trades),
    "net_return_pct": round(net_return_pct, 3),
    "win_rate_pct": round(win_rate, 2),
    "max_drawdown_pct": round(dd_pct, 3),
    "top_ticker_share_pct": round(top_pct, 2),
    "gates": {
        "G1": "pass" if g1_pass else f"fail: {g1_trades} trades < 100",
        "G2": "pass" if g2_pass else f"fail: {net_return_pct:.2f}%",
        "G3": "pass" if g3_pass else f"fail: {win_rate:.2f}%",
        "G4": "pass" if g4_pass else f"fail: {dd_pct:.2f}%",
        "G5": "pass" if g5_pass else f"fail: {top_pct:.2f}% in one name",
    },
    "by_regime": by_regime.to_dict(orient="records"),
}
print(json.dumps(result, indent=2, default=str))
PY
```

The script writes to stdout. If a metric extraction fails because of a schema difference (column name, table name, missing field), debug iteratively — the script is short. If `events` table name is different (e.g. `algo.fills`), adjust.

- [ ] **Step 4: Write the triage report**

Create `docs/research/2026-05-22-mis-mr-long-v1-baseline.md`:

```markdown
# MIS MR Long v1 — Baseline Backtest Report

| | |
|---|---|
| Date | 2026-05-22 |
| Run tag | intraday-mr-v1-baseline |
| Window | 2025-11-17 → 2026-05-21 (6 mo) |
| Template | mis_intraday_meanrev_long_v1.json |
| NAV | ₹10L |

## Acceptance gates (spec §6.3)

| Gate | Threshold | Result | Pass |
|---|---|---|---|
| G1: Trade count | ≥ 100 | <fill from /tmp/triage.json> | ... |
| G2: Net return | > 0% | <fill> | ... |
| G3: Win rate (ex-stops) | ≥ 50% | <fill> | ... |
| G4: Max drawdown | ≤ 5% | <fill> | ... |
| G5: Concentration | ≤ 20% in one name | <fill> | ... |

## Per-regime breakdown

<paste the by_regime table from triage.json as a markdown table>

## Decision

<one of: ship to paper / hand-tune and re-run once / negative result, abandon>

## Rationale

<2-3 sentences>
```

Fill in the actual numbers from `/tmp/triage.json` produced by Step 3.

- [ ] **Step 5: Commit triage report (regardless of outcome)**

```bash
git add docs/research/2026-05-22-mis-mr-long-v1-baseline.md
git commit -m "docs(research): MIS MR long v1 baseline backtest triage"
```

- [ ] **Step 6: If any gate fails, apply spec §6.4 single-iteration tune**

Per spec §6.4:

- **G1 fail**: relax `market_breadth >= 0.50` to `0.40` in the template, OR extend `minutes_since_open` to `[15, 285]`. Pick ONE.
- **G2 / G3 fail**: tighten `rsi_5` from 25 to 20 in the template.
- **G4 fail**: tighten `stop_loss_pct` to 1.5 or `weight` to 0.04.
- **G5 fail**: identify the dominating ticker, document in the report. Likely a corporate-action artifact. Document — no template change required.
- **Two or more fail**: do NOT tune; the bake-off was right. Update the report's "Decision" to "Negative result; abandon v1, pivot research direction."

If you tune, commit the template change with message `tune(strategy): adjust <PARAM> per spec §6.4 G<N> fail` and re-run Steps 1-4. Stop after one iteration regardless of outcome.

---

## Task 8: Push branch + open PR

- [ ] **Step 1: Push**

```bash
git push -u origin strategy/intraday-mr-long-v1-spec
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev \
  --title "feat(strategy): MIS intraday mean-rev long v1 + UniverseFilter is_fno" \
  --body "$(cat <<'EOF'
## Summary
- Add `mis_intraday_meanrev_long_v1` strategy template — long-only 15m MIS mean-reversion built directly from the 2026-05-21 bake-off's 5-feature stable set
- Extend `UniverseFilter.is_fno: bool` and intersect with F&O 200 list in the backtest universe resolver
- Document the F&O 200 caps convention for paper + live (operator pre-populates `caps.allowed_tickers`)
- Spec: `docs/superpowers/specs/2026-05-22-rule-based-intraday-mis-long-v1-design.md`
- Plan: `docs/superpowers/plans/2026-05-22-rule-based-intraday-mis-long-v1.md`
- Baseline backtest triage: `docs/research/2026-05-22-mis-mr-long-v1-baseline.md`

## Test plan
- [x] `pytest backend/algo/strategy/tests/test_ast.py` green (3 new is_fno tests)
- [x] `pytest backend/algo/backtest/tests/test_universe.py` green (2 new is_fno tests)
- [x] `pytest backend/algo/strategy/tests/test_template_mis_intraday_meanrev_long_v1.py` green (4 tests)
- [x] `pytest backend/algo/backtest/tests/test_daily_overlay_for_15m_ast.py` green (1 test)
- [x] Full baseline backtest completed; gates G1-G5 triaged in research doc
- [ ] If gates pass: promote to paper via `algo.strategy.promotion.promote(...)`. Operator must populate `caps.allowed_tickers` with F&O list before paper run starts.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Address review, squash-merge per CLAUDE.md §4.4 #27**

---

## Spec-coverage self-review

| Spec section | Plan task |
|---|---|
| §3 Architecture | Task 1 (ast), Task 2 (universe.py), Task 3 (template), Task 6 (docs) |
| §4 AST template + parameter rationale | Task 3 + Task 4 |
| §5.1 UniverseFilter.is_fno | Task 1 |
| §5.2 Runtime whitelist hook | Task 2 (backtest only — Task 6 documents paper/live convention) |
| §5.3 Cross-cadence daily overlay | Task 5 |
| §6 Backtest plan + G1-G5 | Task 7 |
| §6.4 Single-iteration tune on gate fail | Task 7 Step 6 |
| §7 Promotion path | NOT implemented in code — operator-driven per existing workflow; Task 6 docs the F&O caps convention; PR description in Task 8 reminds operator |
| §8 Non-goals | Honored (no walk-forward, no short side, no vol-targeting, no sector cap) |

**Notable scope correction from spec discovery:** spec §5.2 implied parallel hook in LiveRuntime; plan downscopes this to docs (Task 6) because live + paper actually use `caps.allowed_tickers`, not `strategy.universe.filter`. The PR description and Task 6 doc clearly assign this responsibility to the operator at promotion time.

**Placeholder scan:** Task 5 contains "TBD per Step 1 grep" for `evaluate_strategy_at_bar` / `_load_daily_overlay_panel`. This is intentional — the runner's exact eval entry-point name is not visible from the spec, and asking the implementer to grep + adapt is a smaller cost than guessing wrong here. Task 5 Step 2 explicitly describes the discovery + fallback procedure (drop to lowest-level overlay-lookup helper if no high-level entry point exists).

**Type consistency:** `is_fno: bool = False` defined in Task 1, used in Task 2 test stubs (`SimpleNamespace(is_fno=is_fno)`) and in Task 3 JSON (`"is_fno": true`). `load_fno_universe()` defined in PR #229's `backend/algo/research/intraday_15m_mis_bakeoff/universe.py`, consumed in Task 2 + Task 6.
