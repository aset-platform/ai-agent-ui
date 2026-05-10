# Regime-Aware Multi-Factor System — Slice REGIME-4: Volatility-Targeted Sizing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** 3-stage sizer (volatility-target → hard caps → drawdown throttle) wired into all 3 runtimes. New AST sizing modes `qty: {vol_target_pct: 1.5}` and `qty: {kelly_fraction: 0.25}`. Existing `{shares}`, `{notional_inr}`, `{all}` modes untouched.

**Architecture:** Pure functions in new `backend/algo/sizing/` package. AST extended via discriminated union (additive — backward compat). Sizer composition called from each runtime's signal-execution path AFTER qty resolution but BEFORE pre-trade checks. Reads:
- `realized_vol_60d` from REGIME-2a factor cache (`get_factors_window`)
- `expected_edge` from REGIME-3 strategy_metadata (Kelly only)
- NAV peak from `algo.runs.equity_curve` (V2-2; existing)
- Sector from REGIME-2a factor cache (`FactorRow.sector`)

**Tech Stack:** Python 3.12, Decimal, Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` §3.4 + §5.1 REGIME-4 row + §6.1.

**Branch:** `feature/regime-slice-4-vol-target-sizing` (already created, tracking `origin`).

**Estimated SP:** 8

---

## Pre-flight (MUST DO before code)

Per `feedback_subagent_grep_preflight`. Verify BEFORE each task:

- **AST qty union:** `BuyQtyShares`, `BuyQtyNotional`, `SellQtyShares`, `SellQtyAll` in `backend/algo/strategy/ast.py:120-150`. `BuyNode.qty: Union[BuyQtyShares, BuyQtyNotional]` at line ~146. The union is bare (no discriminator) — Pydantic uses smart-union by field shape. Adding new variants requires NO discriminator changes.
- **Backtest qty resolution:** `backend/algo/backtest/runner.py:466` — `qty = action["qty"].get("shares") or 0`. Dict-style access. Sizer plugs in HERE — replace bare lookup with a `_resolve_qty(action_qty_dict, ctx)` helper that handles all 5 modes.
- **Live qty path:** `backend/algo/live/runtime.py` — `signal.qty` is computed upstream by the evaluator. Sizer must wrap that too.
- **Paper qty path:** mirror live (paper runtime has the same signal model).
- **Equity curve:** `summary.equity_curve` is a list of `EquityCurvePoint(bar_date, equity)` per `backend/algo/tests/test_backtest_runner.py:175-177`. Live + paper write to `algo.runs.equity_curve` via `runs_repo`.
- **Factor cache reader:** `from backend.algo.factors.repo import get_factors_window` (REGIME-2a). Returns `FactorRow(ticker, bar_date, values, sector)`. `realized_vol_60d` is in `values["realized_vol_60d"]` if computed.
- **Strategy metadata:** `backend/algo/strategy/metadata_repo.py::get_metadata` (REGIME-3) returns `StrategyMetadata(applicable_regimes, expected_edge, description)`. Use for Kelly.
- **EvalContext:** `backend/algo/backtest/evaluator.py::EvalContext(ticker, bar_date, features, open_qty)`. Sizer needs ticker + features (for vol lookup).

If any name doesn't resolve, STOP.

---

## File Structure

**Backend — new:**
- `backend/algo/sizing/__init__.py` — exports public API.
- `backend/algo/sizing/vol_target.py` — `vol_target_qty()` pure function.
- `backend/algo/sizing/caps.py` — `PositionCaps` dataclass + `cap()` method.
- `backend/algo/sizing/drawdown_throttle.py` — `dd_multiplier()` pure function + `compute_dd_pct(equity_curve)` helper.
- `backend/algo/sizing/composer.py` — `compose_qty(base_qty, ctx) -> int` that runs the 3-stage pipeline.
- `backend/algo/sizing/tests/__init__.py`
- `backend/algo/sizing/tests/test_vol_target.py`
- `backend/algo/sizing/tests/test_caps.py`
- `backend/algo/sizing/tests/test_drawdown_throttle.py`
- `backend/algo/sizing/tests/test_composer.py`
- `backend/algo/sizing/tests/test_ast_new_modes.py`

**Backend — modified:**
- `backend/algo/strategy/ast.py` — add `BuyQtyVolTarget` + `BuyQtyKelly` Pydantic models; extend `BuyNode.qty` union.
- `backend/algo/backtest/runner.py` — replace bare `action["qty"].get("shares") or 0` with `_resolve_and_size_qty(action, ctx, sizing_ctx)`.
- `backend/algo/paper/runtime.py` — call sizer composition before submitting signal.
- `backend/algo/live/runtime.py` — same as paper.

**Frontend:** None.
**E2E:** None (existing backtest E2E covers indirect testing).

---

## Task 1 — Vol-target pure function

**Files:**
- Create: `backend/algo/sizing/__init__.py`, `backend/algo/sizing/vol_target.py`, `backend/algo/sizing/tests/__init__.py`, `backend/algo/sizing/tests/test_vol_target.py`.

Per spec §3.4:
```python
per_pos_vol_budget = target_portfolio_vol_pct / sqrt(n_positions_target)
notional = (per_pos_vol_budget * nav) / stock_realized_vol_annual
qty = floor(notional / stock_price)
```

Decimal precision throughout. Return 0 on NaN inputs / division-by-zero (sizer will treat as "skip").

- [ ] **Step 1.1: Failing test**

```python
"""Vol-target sizing tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.sizing.vol_target import vol_target_qty


def test_canonical_example() -> None:
    """Per spec: target=1.5%, nav=100k, price=1000, vol=30%, n=10
    → per_pos_vol = 1.5 / sqrt(10) = 0.4743%
    → notional = 0.004743 * 100000 / 0.30 = 1581
    → qty = floor(1581 / 1000) = 1
    """
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.5"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        stock_realized_vol_annual=Decimal("0.30"),
        n_positions_target=10,
    )
    assert qty == 1


def test_inverse_vol_scaling() -> None:
    """Higher vol → smaller qty for same target."""
    common = dict(
        target_portfolio_vol_pct=Decimal("2.0"),
        nav=Decimal("1000000"),
        stock_price=Decimal("100"),
        n_positions_target=10,
    )
    low = vol_target_qty(**common, stock_realized_vol_annual=Decimal("0.15"))
    high = vol_target_qty(**common, stock_realized_vol_annual=Decimal("0.45"))
    assert low > high
    assert low == pytest.approx(high * 3, abs=2)


def test_zero_vol_returns_zero() -> None:
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.0"),
        nav=Decimal("100000"),
        stock_price=Decimal("100"),
        stock_realized_vol_annual=Decimal("0"),
        n_positions_target=5,
    )
    assert qty == 0


def test_nan_vol_returns_zero() -> None:
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.0"),
        nav=Decimal("100000"),
        stock_price=Decimal("100"),
        stock_realized_vol_annual=Decimal("NaN"),
        n_positions_target=5,
    )
    assert qty == 0


def test_zero_price_returns_zero() -> None:
    qty = vol_target_qty(
        target_portfolio_vol_pct=Decimal("1.0"),
        nav=Decimal("100000"),
        stock_price=Decimal("0"),
        stock_realized_vol_annual=Decimal("0.30"),
        n_positions_target=5,
    )
    assert qty == 0
```

- [ ] **Step 1.2: Run to verify fail**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_vol_target.py -v
```

- [ ] **Step 1.3: Implement**

Create `backend/algo/sizing/__init__.py`:
```python
"""Sizing — 3-stage composer (vol-target → caps → DD throttle)."""
```

Create `backend/algo/sizing/vol_target.py`:

```python
"""Volatility-targeted position sizer.

Per spec §3.4 + research §5:
    per_pos_vol_budget = target_portfolio_vol_pct / sqrt(n_positions_target)
    notional           = (per_pos_vol_budget / 100) * nav
                         / stock_realized_vol_annual
    qty                = floor(notional / stock_price)
"""
from __future__ import annotations

from decimal import Decimal


def _is_invalid(d: Decimal) -> bool:
    return d.is_nan() or d <= 0


def vol_target_qty(
    target_portfolio_vol_pct: Decimal,
    nav: Decimal,
    stock_price: Decimal,
    stock_realized_vol_annual: Decimal,
    n_positions_target: int,
) -> int:
    """Return integer share qty.

    Inputs:
      * ``target_portfolio_vol_pct`` — e.g. ``Decimal("1.5")`` for 1.5%
      * ``nav`` — total portfolio NAV in INR
      * ``stock_price`` — current price
      * ``stock_realized_vol_annual`` — annualised realized vol e.g.
        ``Decimal("0.30")`` for 30%
      * ``n_positions_target`` — diversification target (≥ 1)

    Returns 0 on any invalid input (NaN, zero, negative) — sizer
    treats this as "skip the trade".
    """
    if (
        n_positions_target <= 0
        or _is_invalid(target_portfolio_vol_pct)
        or _is_invalid(nav)
        or _is_invalid(stock_price)
        or _is_invalid(stock_realized_vol_annual)
    ):
        return 0
    sqrt_n = Decimal(n_positions_target).sqrt()
    per_pos_vol_budget = target_portfolio_vol_pct / sqrt_n
    notional = (per_pos_vol_budget / Decimal("100") * nav) / stock_realized_vol_annual
    return int(notional / stock_price)
```

- [ ] **Step 1.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_vol_target.py -v
git add backend/algo/sizing/__init__.py backend/algo/sizing/vol_target.py backend/algo/sizing/tests/__init__.py backend/algo/sizing/tests/test_vol_target.py
git commit -m "feat(algo): vol_target_qty pure sizing function (REGIME-4)"
```

---

## Task 2 — Position caps

**Files:**
- Create: `backend/algo/sizing/caps.py`, `backend/algo/sizing/tests/test_caps.py`.

Per-position max % of NAV (default 12%), per-sector max % (default 30%), cash floor (default 5%). `cap()` truncates the intended qty to the most-restrictive limit.

- [ ] **Step 2.1: Test**

```python
"""Position caps tests — per-position + per-sector + cash floor."""
from __future__ import annotations

from decimal import Decimal

from backend.algo.sizing.caps import PositionCaps


def test_per_position_cap_truncates() -> None:
    """Intended 15% of NAV against 12% cap → truncate to 12%."""
    caps = PositionCaps()  # defaults: per_pos=12, per_sector=30, cash=5
    qty = caps.cap(
        intended_qty=15,
        intended_value=Decimal("15000"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
    )
    assert qty == 12


def test_per_sector_cap_truncates() -> None:
    """Sector already at 25%; new position would push to 35%; cap=30%
    → truncate so that final exposure ≤ 30%."""
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=10,
        intended_value=Decimal("10000"),  # 10%
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("25000"),  # 25%
    )
    # 5000 INR room → 5 shares
    assert qty == 5


def test_cash_floor_truncates() -> None:
    """Cash floor 5%: if intended_value would push cash below 5%,
    truncate. NAV=100k, current cash=10k (10%). New value=8k → cash
    drops to 2k (2%) → cap to 5k (cash 5%)."""
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=8,
        intended_value=Decimal("8000"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
        current_cash=Decimal("10000"),
    )
    # 10k cash - 5k floor = 5k available → 5 shares
    assert qty == 5


def test_no_cap_below_thresholds() -> None:
    """Intended 8 shares = 8% (within 12% per-pos and 30% sector)."""
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=8,
        intended_value=Decimal("8000"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
    )
    assert qty == 8


def test_zero_nav_returns_zero() -> None:
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=5,
        intended_value=Decimal("5000"),
        nav=Decimal("0"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
    )
    assert qty == 0
```

- [ ] **Step 2.2: Implement**

```python
"""Position + sector + cash caps. Truncates intended qty to most-
restrictive limit. Per spec §3.4."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PositionCaps:
    per_position_max_pct: Decimal = Decimal("12")
    per_sector_max_pct: Decimal = Decimal("30")
    cash_floor_pct: Decimal = Decimal("5")

    def cap(
        self,
        *,
        intended_qty: int,
        intended_value: Decimal,
        nav: Decimal,
        stock_price: Decimal,
        sector: str | None,
        current_sector_exposure: Decimal,
        current_cash: Decimal | None = None,
    ) -> int:
        if nav <= 0 or stock_price <= 0 or intended_qty <= 0:
            return 0

        # Per-position cap
        max_pos_value = nav * self.per_position_max_pct / Decimal("100")
        if intended_value > max_pos_value:
            intended_value = max_pos_value

        # Per-sector cap
        if sector:
            max_sector_value = nav * self.per_sector_max_pct / Decimal("100")
            sector_room = max_sector_value - current_sector_exposure
            if sector_room < intended_value:
                intended_value = max(Decimal("0"), sector_room)

        # Cash floor (only when we have cash context)
        if current_cash is not None:
            cash_floor_value = nav * self.cash_floor_pct / Decimal("100")
            cash_after = current_cash - intended_value
            if cash_after < cash_floor_value:
                room = current_cash - cash_floor_value
                intended_value = max(Decimal("0"), room)

        return int(intended_value / stock_price)
```

- [ ] **Step 2.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_caps.py -v
git add backend/algo/sizing/caps.py backend/algo/sizing/tests/test_caps.py
git commit -m "feat(algo): PositionCaps with per-position + per-sector + cash floor (REGIME-4)"
```

---

## Task 3 — Drawdown throttle ladder

**Files:**
- Create: `backend/algo/sizing/drawdown_throttle.py`, `backend/algo/sizing/tests/test_drawdown_throttle.py`.

5/10/15/20% DD → 0.75/0.5/0.25/0× multiplier. Below 5% DD → 1.0×.

`compute_dd_pct(equity_curve)` helper takes a list of `(date, equity)` pairs and returns current DD from peak.

- [ ] **Step 3.1: Test**

```python
"""Drawdown throttle ladder + DD computation."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.algo.sizing.drawdown_throttle import (
    compute_dd_pct,
    dd_multiplier,
)


@pytest.mark.parametrize(
    "dd_pct,expected",
    [
        (Decimal("0"), Decimal("1.0")),
        (Decimal("4.99"), Decimal("1.0")),
        (Decimal("5"), Decimal("0.75")),
        (Decimal("9.99"), Decimal("0.75")),
        (Decimal("10"), Decimal("0.5")),
        (Decimal("14.99"), Decimal("0.5")),
        (Decimal("15"), Decimal("0.25")),
        (Decimal("19.99"), Decimal("0.25")),
        (Decimal("20"), Decimal("0")),
        (Decimal("25"), Decimal("0")),
    ],
)
def test_dd_ladder(dd_pct, expected) -> None:
    assert dd_multiplier(dd_pct) == expected


def test_compute_dd_at_peak_is_zero() -> None:
    curve = [
        (date(2026, 5, 1), Decimal("100000")),
        (date(2026, 5, 2), Decimal("105000")),
    ]
    assert compute_dd_pct(curve) == Decimal("0")


def test_compute_dd_below_peak() -> None:
    curve = [
        (date(2026, 5, 1), Decimal("100000")),
        (date(2026, 5, 2), Decimal("110000")),  # peak
        (date(2026, 5, 3), Decimal("99000")),
    ]
    # DD = (110k - 99k) / 110k = 10%
    assert compute_dd_pct(curve) == Decimal("10")


def test_compute_dd_empty_returns_zero() -> None:
    assert compute_dd_pct([]) == Decimal("0")
```

- [ ] **Step 3.2: Implement**

```python
"""Drawdown throttle multiplier ladder + peak-NAV helper.

Per spec §3.4 + research §5:
    DD ≤  5%  → 1.00× (full size)
    DD ≤ 10%  → 0.75×
    DD ≤ 15%  → 0.50×
    DD ≤ 20%  → 0.25×
    DD >  20% → 0.00× (halt new entries)
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal


def dd_multiplier(dd_from_peak_pct: Decimal) -> Decimal:
    """Lookup the size multiplier given current DD percent."""
    if dd_from_peak_pct < Decimal("5"):
        return Decimal("1.0")
    if dd_from_peak_pct < Decimal("10"):
        return Decimal("0.75")
    if dd_from_peak_pct < Decimal("15"):
        return Decimal("0.5")
    if dd_from_peak_pct < Decimal("20"):
        return Decimal("0.25")
    return Decimal("0")


def compute_dd_pct(
    equity_curve: list[tuple[date, Decimal]],
) -> Decimal:
    """Current DD from running peak, expressed as a percent."""
    if not equity_curve:
        return Decimal("0")
    sorted_curve = sorted(equity_curve, key=lambda x: x[0])
    peak = sorted_curve[0][1]
    current = sorted_curve[-1][1]
    for _, equity in sorted_curve:
        if equity > peak:
            peak = equity
    if peak <= 0:
        return Decimal("0")
    if current >= peak:
        return Decimal("0")
    return (peak - current) / peak * Decimal("100")
```

- [ ] **Step 3.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_drawdown_throttle.py -v
git add backend/algo/sizing/drawdown_throttle.py backend/algo/sizing/tests/test_drawdown_throttle.py
git commit -m "feat(algo): drawdown throttle 5/10/15/20% ladder + compute_dd_pct (REGIME-4)"
```

---

## Task 4 — AST extension: `vol_target_pct` + `kelly_fraction` modes

**Files:**
- Modify: `backend/algo/strategy/ast.py`.
- Test: `backend/algo/sizing/tests/test_ast_new_modes.py`.

Add 2 new Pydantic models, extend `BuyNode.qty` union. Backward compat: existing modes still parse first (Pydantic smart-union picks the matching variant by field name).

- [ ] **Step 4.1: Test**

```python
"""AST parses new sizing modes alongside legacy modes."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.algo.strategy.ast import BuyNode


def test_legacy_shares_still_parses() -> None:
    n = BuyNode.model_validate({"type": "buy", "qty": {"shares": 10}})
    assert n.qty.shares == 10


def test_legacy_notional_still_parses() -> None:
    n = BuyNode.model_validate(
        {"type": "buy", "qty": {"notional_inr": 50000.0}}
    )
    assert n.qty.notional_inr == 50000.0


def test_vol_target_parses() -> None:
    n = BuyNode.model_validate(
        {"type": "buy", "qty": {"vol_target_pct": 1.5}}
    )
    assert float(n.qty.vol_target_pct) == 1.5


def test_kelly_parses() -> None:
    n = BuyNode.model_validate(
        {"type": "buy", "qty": {"kelly_fraction": 0.25}}
    )
    assert float(n.qty.kelly_fraction) == 0.25


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        BuyNode.model_validate(
            {"type": "buy", "qty": {"chocolate": 5}}
        )


def test_vol_target_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        BuyNode.model_validate(
            {"type": "buy", "qty": {"vol_target_pct": 0}}
        )
```

- [ ] **Step 4.2: Extend AST**

In `backend/algo/strategy/ast.py`, after `BuyQtyNotional`:

```python
class BuyQtyVolTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vol_target_pct: float = Field(gt=0)


class BuyQtyKelly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kelly_fraction: float = Field(gt=0, le=1)
```

Update `BuyNode.qty` union:
```python
class BuyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["buy"] = "buy"
    qty: Union[BuyQtyShares, BuyQtyNotional, BuyQtyVolTarget, BuyQtyKelly]
```

(Don't modify `SellNode` — sell qty is shares-or-all, vol-target/Kelly only applies to entries.)

- [ ] **Step 4.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_ast_new_modes.py backend/algo/tests/ -k "ast or strategy" -q --no-header 2>&1 | tail -10
git add backend/algo/strategy/ast.py backend/algo/sizing/tests/test_ast_new_modes.py
git commit -m "feat(algo): AST BuyNode.qty extended with vol_target_pct + kelly_fraction (REGIME-4)"
```

---

## Task 5 — Composer (3-stage pipeline)

**Files:**
- Create: `backend/algo/sizing/composer.py`, `backend/algo/sizing/tests/test_composer.py`.

`compose_qty()` orchestrates: resolve base qty (per AST mode) → cap → DD throttle. Pure function — takes a `SizingContext` dataclass with all the inputs.

- [ ] **Step 5.1: Test**

```python
"""3-stage composer: vol-target → caps → DD throttle."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.sizing.composer import (
    SizingContext, compose_qty,
)


def _ctx(**overrides) -> SizingContext:
    base = dict(
        ticker="TEST.NS",
        bar_date=date(2026, 5, 10),
        nav=Decimal("100000"),
        cash=Decimal("100000"),
        stock_price=Decimal("1000"),
        realized_vol_annual=Decimal("0.30"),
        sector="IT",
        sector_exposure=Decimal("0"),
        equity_curve=[(date(2026, 5, 1), Decimal("100000"))],
        n_positions_target=10,
        expected_edge=None,
    )
    base.update(overrides)
    return SizingContext(**base)


def test_shares_mode_passthrough_no_throttle() -> None:
    """Legacy shares mode at low DD → no caps triggered → qty
    returned as-is (clamped only if exceeds per-position cap)."""
    ctx = _ctx()
    # 5 shares × 1000 = 5k = 5% NAV (under 12% cap)
    assert compose_qty({"shares": 5}, ctx) == 5


def test_per_position_cap_applied() -> None:
    ctx = _ctx()
    # 50 shares × 1000 = 50k = 50% NAV → cap to 12%
    assert compose_qty({"shares": 50}, ctx) == 12


def test_vol_target_mode() -> None:
    """vol_target_pct=2.0, vol=30%, nav=100k, n=10
    per_pos = 2 / sqrt(10) = 0.632%
    notional = 0.00632 * 100k / 0.30 = 2107
    qty = 2107 / 1000 = 2"""
    ctx = _ctx()
    qty = compose_qty({"vol_target_pct": 2.0}, ctx)
    assert qty == 2


def test_dd_throttle_applied() -> None:
    """Equity curve shows 10% DD → 0.5× multiplier.
    base 10 shares → throttled to 5."""
    ctx = _ctx(
        equity_curve=[
            (date(2026, 5, 1), Decimal("100000")),
            (date(2026, 5, 2), Decimal("110000")),  # peak
            (date(2026, 5, 3), Decimal("99000")),   # 10% DD
        ],
    )
    qty = compose_qty({"shares": 10}, ctx)
    assert qty == 5


def test_dd_throttle_zero_at_high_dd() -> None:
    """25% DD → halt entries entirely."""
    ctx = _ctx(
        equity_curve=[
            (date(2026, 5, 1), Decimal("100000")),
            (date(2026, 5, 2), Decimal("100000")),
            (date(2026, 5, 3), Decimal("75000")),  # 25% DD
        ],
    )
    assert compose_qty({"shares": 10}, ctx) == 0


def test_kelly_requires_expected_edge() -> None:
    """Kelly mode without expected_edge metadata returns 0 +
    logs a warning (callers treat as skip)."""
    ctx = _ctx(expected_edge=None)
    assert compose_qty({"kelly_fraction": 0.25}, ctx) == 0


def test_kelly_with_edge() -> None:
    """Kelly: f* = edge / vol^2; qty = f * frac * nav / price.
    edge=0.10, vol=0.30, frac=0.25, nav=100k, price=1000
    f* = 0.10 / 0.09 = 1.111
    capital = 1.111 * 0.25 * 100000 = 27,778
    qty = 27 (then capped per per-position 12% = 12 shares)"""
    ctx = _ctx(expected_edge=Decimal("0.10"))
    qty = compose_qty({"kelly_fraction": 0.25}, ctx)
    # After per-position cap (12% of 100k = 12k → 12 shares)
    assert qty == 12


def test_unknown_mode_returns_zero() -> None:
    ctx = _ctx()
    assert compose_qty({"chocolate": 5}, ctx) == 0
```

- [ ] **Step 5.2: Implement**

```python
"""Sizing composer — orchestrates vol-target → caps → DD throttle.

Pure function. Pluggable by all three runtimes (backtest / paper /
live). Caller assembles a SizingContext with NAV, cash, factor cache
lookup, sector lookup, and the strategy's equity curve.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.algo.sizing.caps import PositionCaps
from backend.algo.sizing.drawdown_throttle import (
    compute_dd_pct, dd_multiplier,
)
from backend.algo.sizing.vol_target import vol_target_qty

_logger = logging.getLogger(__name__)


@dataclass
class SizingContext:
    ticker: str
    bar_date: date
    nav: Decimal
    cash: Decimal
    stock_price: Decimal
    realized_vol_annual: Decimal
    sector: str | None
    sector_exposure: Decimal
    equity_curve: list[tuple[date, Decimal]]
    n_positions_target: int = 10
    expected_edge: Decimal | None = None
    caps: PositionCaps = field(default_factory=PositionCaps)


def _resolve_base_qty(
    qty_spec: dict, ctx: SizingContext,
) -> int:
    """Resolve raw qty per AST mode. Returns 0 on unknown mode."""
    if "shares" in qty_spec:
        return int(qty_spec["shares"])
    if "notional_inr" in qty_spec:
        notional = Decimal(str(qty_spec["notional_inr"]))
        if ctx.stock_price <= 0:
            return 0
        return int(notional / ctx.stock_price)
    if "vol_target_pct" in qty_spec:
        return vol_target_qty(
            target_portfolio_vol_pct=Decimal(str(qty_spec["vol_target_pct"])),
            nav=ctx.nav,
            stock_price=ctx.stock_price,
            stock_realized_vol_annual=ctx.realized_vol_annual,
            n_positions_target=ctx.n_positions_target,
        )
    if "kelly_fraction" in qty_spec:
        if ctx.expected_edge is None:
            _logger.warning(
                "kelly_fraction sizing requested for %s but no "
                "expected_edge in strategy metadata — skipping",
                ctx.ticker,
            )
            return 0
        edge = Decimal(str(ctx.expected_edge))
        vol = ctx.realized_vol_annual
        if vol.is_nan() or vol <= 0:
            return 0
        f_star = edge / (vol * vol)
        capital = f_star * Decimal(str(qty_spec["kelly_fraction"])) * ctx.nav
        if ctx.stock_price <= 0 or capital <= 0:
            return 0
        return int(capital / ctx.stock_price)
    if "all" in qty_spec:
        # All-cash entry — bounded by per-position cap downstream.
        if ctx.stock_price <= 0:
            return 0
        return int(ctx.cash / ctx.stock_price)
    _logger.warning(
        "Unknown sizing mode %s for %s — skipping",
        list(qty_spec.keys()), ctx.ticker,
    )
    return 0


def compose_qty(qty_spec: dict, ctx: SizingContext) -> int:
    """3-stage pipeline: resolve → cap → DD throttle. Returns 0 to
    signal "skip" on any invalid input."""
    base = _resolve_base_qty(qty_spec, ctx)
    if base <= 0:
        return 0
    intended_value = Decimal(base) * ctx.stock_price
    capped = ctx.caps.cap(
        intended_qty=base,
        intended_value=intended_value,
        nav=ctx.nav,
        stock_price=ctx.stock_price,
        sector=ctx.sector,
        current_sector_exposure=ctx.sector_exposure,
        current_cash=ctx.cash,
    )
    if capped <= 0:
        return 0
    mult = dd_multiplier(compute_dd_pct(ctx.equity_curve))
    if mult == Decimal("0"):
        return 0
    return int(Decimal(capped) * mult)
```

- [ ] **Step 5.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_composer.py -v
git add backend/algo/sizing/composer.py backend/algo/sizing/tests/test_composer.py
git commit -m "feat(algo): SizingContext + compose_qty 3-stage pipeline (REGIME-4)"
```

---

## Task 6 — Wire composer into the 3 runtimes

**Files:**
- Modify: `backend/algo/backtest/runner.py`, `backend/algo/paper/runtime.py`, `backend/algo/live/runtime.py`.
- Test: `backend/algo/sizing/tests/test_runtime_uses_composer.py`.

Per `feedback_runtime_feature_three_runtimes`. Each runtime constructs a `SizingContext` from its existing data + the new factor cache and metadata sources, then calls `compose_qty()`.

**Backtest:** `runner.py:466` — replace `qty = action["qty"].get("shares") or 0` block with composer call. Sector + realized_vol come from `factors_by_key.get((ticker, bar_date))` (REGIME-2a wired this in already). Equity curve is built incrementally during the bar walk; pass the slice up to the current bar.

**Paper + live:** wrap the existing `signal.qty` path. The signal evaluator computes `signal.qty` from the AST; for new modes, `signal.qty` will be a placeholder — the actual sizing happens at this layer because vol-target/Kelly need NAV + cash + DD which the evaluator doesn't have.

Strategy: have the AST evaluator emit `signal.qty_spec: dict` (the original `action["qty"]`) ALONGSIDE the legacy `signal.qty` int. Runtime uses `qty_spec` if present + non-shares, else falls back to legacy `signal.qty`. Backwards compat: if `qty_spec` is shares/notional/all, the evaluator's already-computed `signal.qty` is used.

- [ ] **Step 6.1: Test (lightweight binding)**

```python
"""Verify each runtime imports the composer."""
from __future__ import annotations


def test_backtest_runner_imports_composer() -> None:
    from backend.algo.backtest import runner
    assert hasattr(runner, "compose_qty")


def test_paper_runtime_imports_composer() -> None:
    from backend.algo.paper import runtime
    assert hasattr(runtime, "compose_qty")


def test_live_runtime_imports_composer() -> None:
    from backend.algo.live import runtime
    assert hasattr(runtime, "compose_qty")
```

- [ ] **Step 6.2: Backtest integration**

In `backend/algo/backtest/runner.py`:
1. Add at top:
   ```python
   from backend.algo.sizing.composer import SizingContext, compose_qty
   ```
2. Find the qty resolution at line ~466 (search `action["qty"].get("shares")`). REPLACE the bare lookup block with:
   ```python
   # Build sizing context from already-loaded state. Factor cache
   # was pre-loaded earlier in run_backtest (REGIME-2a wiring).
   factor_row = factors_by_key.get((ticker, bar.date), {})
   realized_vol = factor_row.get(
       "realized_vol_60d", Decimal("NaN")
   ) if isinstance(factor_row, dict) else Decimal("NaN")
   sector = None  # backtest sector lookup is in factors_by_key keys
                  # — future REGIME-7 enrichment
   ctx = SizingContext(
       ticker=ticker,
       bar_date=bar.date,
       nav=current_nav,
       cash=current_cash,
       stock_price=Decimal(str(bar.close)),
       realized_vol_annual=realized_vol,
       sector=sector,
       sector_exposure=Decimal("0"),  # backtest single-sector
                                       # tracking deferred to REGIME-7
       equity_curve=[
           (p.bar_date, p.equity)
           for p in equity_curve_so_far
       ],
   )
   qty_spec = action["qty"]
   qty = compose_qty(qty_spec, ctx)
   if qty <= 0:
       continue
   ```
   Adapt to actual surrounding loop variables (`current_nav`, `current_cash`, `equity_curve_so_far`) — grep `nav` + `equity_curve` in runner.py for the right names.

3. **Backward compat**: legacy strategies using `{shares}` will route through `_resolve_base_qty` and get the same value. Test against `test_backtest_runner.py` to confirm zero regression.

- [ ] **Step 6.3: Paper + live integration**

Both runtimes build `SizingContext` from `self._market_regime` (existing) + `self._factor_cache` (REGIME-2a) + `self._positions.cash()` + `self._positions.nav()` + the strategy's recent equity curve (read from `algo.runs.equity_curve` via `runs_repo.get_equity_curve(run_id)`).

If the existing signal already has `signal.qty` set by the evaluator (legacy modes), pass `{"shares": signal.qty}` to `compose_qty` — that re-applies caps + DD throttle even on legacy paths (DESIRED — provides the safety net regardless of sizing mode). If the action's `qty_spec` is one of the new modes, the evaluator wouldn't have set a useful `signal.qty` — pass the raw `qty_spec` from the strategy AST.

Concrete approach: thread `qty_spec` from the AST through the signal model. Add `qty_spec: dict | None = None` field to the signal struct (grep where signals are constructed in `paper/runtime.py:351`); evaluator populates it.

- [ ] **Step 6.4: Run + regression check + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/sizing/tests/test_runtime_uses_composer.py -v
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_backtest_runner.py backend/algo/tests/test_backtest_equity_curve.py backend/algo/tests/test_backtest_risk_gating.py -q --no-header 2>&1 | tail -10
git add backend/algo/backtest/runner.py backend/algo/paper/runtime.py backend/algo/live/runtime.py backend/algo/sizing/tests/test_runtime_uses_composer.py
git commit -m "feat(algo): wire compose_qty into backtest+paper+live (REGIME-4)"
```

---

## Task 7 — Ship + push

```bash
git push origin feature/regime-slice-4-vol-target-sizing
```

---

## Acceptance Checklist

- [ ] `vol_target_qty` matches spec formula (test_canonical_example).
- [ ] Inverse vol scaling holds.
- [ ] `PositionCaps.cap()` enforces per-pos + per-sector + cash floor.
- [ ] `dd_multiplier` table-driven test passes all 5 bands.
- [ ] `compute_dd_pct()` correct for at-peak / below-peak / empty.
- [ ] AST `BuyQtyVolTarget` + `BuyQtyKelly` parse; legacy modes still parse.
- [ ] `compose_qty` 3-stage pipeline order verified.
- [ ] Kelly without `expected_edge` returns 0 + warns.
- [ ] All 3 runtimes import + use `compose_qty`.
- [ ] `backend/algo/tests/` (existing) — no regression.
- [ ] Branch pushed.

---

## Out of Scope for REGIME-4

- Kelly auto-promotion (manual flag only).
- Portfolio-level DD (strategy-level only here; v4).
- Restoration logic (manual ratchet up beyond ladder; v4).
- Per-user sizing caps (5-user scale doesn't justify).
- Regime-adaptive vol targets (static per AST; v4).
- REGIME-5 walkforward gate integration (different slice).
- Per-bar sector exposure tracking in backtest (REGIME-7).
