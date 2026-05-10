# Regime-Aware Multi-Factor System — Slice REGIME-6: Attribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Explainability layer — extend `signal_generated` event payload with attribution context (feature snapshot + regime + factor exposures); compute daily Brinson allocation/selection vs NIFTY 50; per-trade reason log joining entry/exit signal events; monthly OLS factor regression (mock factor returns for v3).

**Architecture:** `signal_generated` payload extension is additive (JSONB extra keys). New `backend/algo/attribution/` package: `brinson.py` (pure), `trade_log.py` (joins algo.events), `factor_regression.py` (numpy.linalg.lstsq — no new deps), `job.py` (daily + monthly orchestrators). Two new PG tables (`algo.attribution_daily`, `algo.factor_regression`). REST endpoints + frontend `AttributionPanel`.

**Tech Stack:** Python 3.12, numpy.linalg, pandas, Pydantic v2, FastAPI. Frontend: React 19, SWR. NO new deps (numpy.linalg.lstsq is in numpy).

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` §3.6 + §4.2 + §5.1 REGIME-6 + §6.1.
**Research:** §7 (attribution patterns).

**Branch:** `feature/regime-slice-6-attribution` (already created, tracking `origin`).

**Estimated SP:** 13

---

## Pre-flight (MUST DO before code)

- **Event writer:** `backend/algo/backtest/event_writer.py::event_row(*, session_id, user_id, strategy_id, mode, type_, payload)`. Payload is arbitrary dict serialized to `payload_json`.
- **Signal emit sites:**
  - `backend/algo/paper/runtime.py:292` — paper signal
  - `backend/algo/live/runtime.py:348` — live signal
  - Verify backtest signal emission with `grep -n 'type_="signal_generated"\|signal_generated"' backend/algo/backtest/`
- **Iceberg `algo.events` reader:** `query_iceberg_table("algo.events", "SELECT ...")`. Note: payload comes back as JSON string in column `payload_json`; parse with `json.loads`.
- **Trades source:** check `algo.trades` Iceberg table — `grep -n "_trades_table\|algo.trades" backend/algo/iceberg_init.py backend/algo/backtest/runs_repo.py`.
- **PG migration head:** `docker compose exec backend alembic current`. Chain new migrations off head.
- **JOB_EXECUTORS** for `@register_job` registration in `backend/jobs/executor.py`.
- **`@router` factory pattern** for `routes/attribution.py`.
- **Cache:** `from cache import get_cache, TTL_VOLATILE, TTL_STABLE` (sync API).
- **NIFTY 50 weights:** check whether the project has a NIFTY 50 weights table or has to use equal-weight as baseline. `grep -rn "nifty.*weight\|nifty50_weights" backend/ stocks/ | head -5`. If absent, use equal-weight (1/50) for the baseline.
- **scipy + numpy + pandas** in `requirements.txt` (verified). NO statsmodels — use `numpy.linalg.lstsq` for OLS.

If any name doesn't resolve, STOP.

---

## File Structure

**Backend — new:**
- `backend/algo/attribution/__init__.py`
- `backend/algo/attribution/brinson.py` — pure compute_brinson + Pydantic models
- `backend/algo/attribution/trade_log.py` — build_trade_reason_log
- `backend/algo/attribution/factor_regression.py` — fit_ols_regression
- `backend/algo/attribution/job.py` — daily_brinson_job + monthly_factor_regression_job orchestrators
- `backend/algo/routes/attribution.py` — `GET /v1/algo/attribution/{daily,trades,regression}`
- `backend/db/migrations/versions/2026_05_10_algo_attribution_tables.py` — both PG tables in one migration
- `backend/algo/tests/test_brinson.py`
- `backend/algo/tests/test_trade_log.py`
- `backend/algo/tests/test_factor_regression.py`
- `backend/algo/tests/test_attribution_routes.py`

**Backend — modified:**
- `backend/algo/paper/runtime.py` — extend signal_generated payload at line ~292
- `backend/algo/live/runtime.py` — same at line ~348
- `backend/algo/backtest/runner.py` — same at signal emission
- `backend/jobs/executor.py` — `@register_job("attribution_daily_brinson")` + `@register_job("attribution_monthly_regression")` wrappers
- `backend/algo/jobs/__init__.py` — import attribution.job for side-effect
- `backend/routes.py` — mount `create_attribution_router()`
- `backend/algo/routes/__init__.py` — export factory

**Frontend — new:**
- `frontend/hooks/useAttribution.ts` — SWR hooks for daily/trades/regression
- `frontend/components/algo-trading/AttributionPanel.tsx` — two-tab layout (Daily Brinson + Trade Reasons)

**Frontend — modified:**
- `frontend/components/algo-trading/PaperTab.tsx` — mount AttributionPanel below ActiveRunsPanel (or in a collapsible)
- `e2e/utils/selectors.ts` — testids
- `e2e/tests/frontend/algo-attribution.spec.ts` — E2E

---

## Task 1 — Payload extension at the 3 signal emit sites

Backward-compatible JSONB key addition. Pre-v3 events without these keys still parse — UI shows "—".

- [ ] **Step 1.1: Inspect current payload at paper:line 292**

```bash
sed -n "275,310p" backend/algo/paper/runtime.py
```

- [ ] **Step 1.2: Extend payload — paper, live, backtest**

In each emit site, augment the `payload=` dict with:

```python
payload={
    # ... existing keys ...
    "feature_snapshot": dict(features),  # the full features dict at decision
    "regime_label": features.get("regime_label"),
    "stress_prob": (
        float(features["stress_prob"])
        if features.get("stress_prob") is not None else None
    ),
    "factor_exposures": {
        k: float(features[k])
        for k in ("mom_12_1", "f_score", "realized_vol_60d",
                  "adx_14", "rs_vs_nifty_3m")
        if k in features and features[k] is not None
    },
}
```

Adapt to actual variable names — the runtime's `features` dict has `Decimal` values so coerce to float when serializing. If the existing call uses kwargs differently, mirror its style.

- [ ] **Step 1.3: Test backward compat parsing**

Add a test that emits a signal event WITHOUT the new keys and verifies parsing doesn't raise.

- [ ] **Step 1.4: Commit**

```bash
git add backend/algo/paper/runtime.py backend/algo/live/runtime.py backend/algo/backtest/runner.py
git commit -m "feat(algo): stamp signal_generated events with regime+factor context (REGIME-6)"
```

---

## Task 2 — Brinson pure function

**Files:**
- Create: `backend/algo/attribution/__init__.py`, `backend/algo/attribution/brinson.py`, `backend/algo/tests/test_brinson.py`.

Per spec §3.6, sector-level Brinson decomposition:
```
allocation[s] = (w_p[s] - w_b[s]) * (r_b[s] - r_b_total)
selection[s]  = w_b[s] * (r_p[s] - r_b[s])
interaction[s]= (w_p[s] - w_b[s]) * (r_p[s] - r_b[s])

active_return = sum_s (allocation[s] + selection[s] + interaction[s])
              = sum_s w_p[s] * r_p[s] - sum_s w_b[s] * r_b[s]
```

- [ ] **Step 2.1: Test**

```python
"""Brinson decomposition tests — algebraic identity."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.attribution.brinson import (
    BrinsonComponents,
    compute_brinson,
)


def test_decomposition_sums_to_active_return() -> None:
    """allocation + selection + interaction == active_return."""
    portfolio_weights = {"IT": 0.5, "Banks": 0.3, "Auto": 0.2}
    benchmark_weights = {"IT": 0.3, "Banks": 0.4, "Auto": 0.3}
    portfolio_returns = {"IT": 0.05, "Banks": 0.02, "Auto": -0.01}
    benchmark_returns = {"IT": 0.04, "Banks": 0.03, "Auto": 0.00}

    out = compute_brinson(
        portfolio_weights, benchmark_weights,
        portfolio_returns, benchmark_returns,
    )

    total_alloc = sum(c.allocation for c in out.values())
    total_sel = sum(c.selection for c in out.values())
    total_inter = sum(c.interaction for c in out.values())

    # Manual active return
    p_total = sum(
        portfolio_weights[s] * portfolio_returns[s]
        for s in portfolio_weights
    )
    b_total = sum(
        benchmark_weights[s] * benchmark_returns[s]
        for s in benchmark_weights
    )
    active = p_total - b_total

    assert abs(
        (total_alloc + total_sel + total_inter) - active
    ) < 1e-9


def test_zero_active_when_identical_portfolios() -> None:
    weights = {"IT": 0.5, "Banks": 0.5}
    returns = {"IT": 0.03, "Banks": -0.01}
    out = compute_brinson(weights, weights, returns, returns)
    for c in out.values():
        assert abs(c.allocation) < 1e-9
        assert abs(c.selection) < 1e-9
        assert abs(c.interaction) < 1e-9


def test_handles_sector_in_portfolio_only() -> None:
    """Defensive: if a sector exists in portfolio but not in
    benchmark, treat benchmark weight + return as 0."""
    pw = {"IT": 0.6, "EmergingTech": 0.4}
    bw = {"IT": 0.5, "Banks": 0.5}
    pr = {"IT": 0.05, "EmergingTech": 0.10}
    br = {"IT": 0.04, "Banks": 0.02}
    out = compute_brinson(pw, bw, pr, br)
    assert "EmergingTech" in out
    assert "Banks" in out


def test_empty_inputs_return_empty() -> None:
    assert compute_brinson({}, {}, {}, {}) == {}
```

- [ ] **Step 2.2: Implement**

```python
"""Brinson allocation/selection/interaction decomposition.

Per spec §3.6, sector-level model. Returns BrinsonComponents per
sector; sum across sectors == active return = (R_p - R_b).

Edge case: sectors present in portfolio but not benchmark (and
vice versa) — treat the missing side as weight 0, return 0.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrinsonComponents:
    sector: str
    allocation: float
    selection: float
    interaction: float

    @property
    def total(self) -> float:
        return self.allocation + self.selection + self.interaction


def compute_brinson(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
) -> dict[str, BrinsonComponents]:
    """Return per-sector decomposition."""
    sectors = sorted(
        set(portfolio_weights) | set(benchmark_weights)
    )
    if not sectors:
        return {}
    # Benchmark total return — needed for allocation effect
    rb_total = sum(
        benchmark_weights.get(s, 0.0)
        * benchmark_returns.get(s, 0.0)
        for s in sectors
    )
    out: dict[str, BrinsonComponents] = {}
    for s in sectors:
        wp = float(portfolio_weights.get(s, 0.0))
        wb = float(benchmark_weights.get(s, 0.0))
        rp = float(portfolio_returns.get(s, 0.0))
        rb = float(benchmark_returns.get(s, 0.0))
        out[s] = BrinsonComponents(
            sector=s,
            allocation=(wp - wb) * (rb - rb_total),
            selection=wb * (rp - rb),
            interaction=(wp - wb) * (rp - rb),
        )
    return out
```

- [ ] **Step 2.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_brinson.py -v
git add backend/algo/attribution/__init__.py backend/algo/attribution/brinson.py backend/algo/tests/test_brinson.py
git commit -m "feat(algo): Brinson decomposition pure function (REGIME-6)"
```

---

## Task 3 — PG migrations: attribution_daily + factor_regression

**Files:**
- Create: `backend/db/migrations/versions/2026_05_10_algo_attribution_tables.py` (one migration, two tables).

Find current alembic head: `docker compose exec backend alembic current`. Chain off whichever revision is current.

- [ ] **Step 3.1: Migration**

```python
"""algo: add attribution_daily + factor_regression tables (REGIME-6).

Both keyed on (user_id, strategy_id) plus date dimension. JSONB
columns hold sector breakdowns (Brinson) and per-factor betas.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# CHAIN OFF THE CURRENT HEAD — set this to the result of `alembic current`
revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "<SET TO ALEMBIC HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attribution_daily",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("bar_date", sa.Date(), primary_key=True),
        sa.Column("brinson_alloc", postgresql.JSONB(),
                  nullable=False),
        sa.Column("brinson_select", postgresql.JSONB(),
                  nullable=False),
        sa.Column("brinson_interaction", postgresql.JSONB(),
                  nullable=False),
        sa.Column("total_active_return", sa.Numeric(),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        schema="algo",
    )
    op.create_table(
        "factor_regression",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column("period_end", sa.Date(), primary_key=True),
        sa.Column("alpha", sa.Numeric(), nullable=False),
        sa.Column("betas", postgresql.JSONB(), nullable=False),
        sa.Column("r_squared", sa.Numeric(), nullable=False),
        sa.Column("n_observations", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_table("factor_regression", schema="algo")
    op.drop_table("attribution_daily", schema="algo")
```

- [ ] **Step 3.2: Apply + commit**

```bash
docker compose exec -T backend alembic upgrade head
git add backend/db/migrations/versions/2026_05_10_algo_attribution_tables.py
git commit -m "feat(algo): PG attribution_daily + factor_regression tables (REGIME-6)"
```

---

## Task 4 — Trade reason log builder

**Files:**
- Create: `backend/algo/attribution/trade_log.py`, `backend/algo/tests/test_trade_log.py`.

Joins `algo.trades` (closed trades) with `algo.events` (`signal_generated`) via `entry_signal_id` + `exit_signal_id`. Composes a human-readable reason string from the entry event's payload.

- [ ] **Step 4.1: Test**

```python
"""Trade-log builder tests — entry/exit signal join + reason text."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from backend.algo.attribution.trade_log import (
    TradeReason,
    build_trade_reason,
)


def test_compose_reason_with_full_context() -> None:
    entry_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "BUY", "qty": 10, "price": 1234.5, '
            '"regime_label": "BULL", "stress_prob": 0.18, '
            '"factor_exposures": {"mom_12_1": 0.85}, '
            '"feature_snapshot": {"rsi": 62}}'
        ),
    }
    exit_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "SELL", "qty": 10, "price": 1456.0, '
            '"reason": "trailing_stop"}'
        ),
    }
    trade = {
        "ticker": "RELIANCE.NS",
        "opened_at": date(2026, 5, 1),
        "closed_at": date(2026, 5, 9),
        "qty": 10,
        "avg_entry_price": 1234.5,
        "avg_exit_price": 1456.0,
        "realised_pnl_inr": 2215.0,
    }
    reason = build_trade_reason(trade, entry_event, exit_event)
    assert reason.ticker == "RELIANCE.NS"
    assert reason.entry_regime == "BULL"
    assert reason.entry_factor_exposures == {"mom_12_1": 0.85}
    assert reason.pnl_pct == pytest.approx(
        (1456.0 - 1234.5) / 1234.5 * 100, rel=1e-3,
    )
    assert "BULL" in reason.reason_text
    assert "trailing_stop" in reason.reason_text


def test_compose_reason_legacy_event_no_attribution_context() -> None:
    """Pre-REGIME-6 entry event without regime/factor keys still
    produces a TradeReason with None defaults."""
    entry_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "BUY", "qty": 5, "price": 100.0}'
        ),
    }
    exit_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "SELL", "qty": 5, "price": 110.0, '
            '"reason": "manual"}'
        ),
    }
    trade = {
        "ticker": "INFY.NS",
        "opened_at": date(2026, 5, 1),
        "closed_at": date(2026, 5, 5),
        "qty": 5,
        "avg_entry_price": 100.0,
        "avg_exit_price": 110.0,
        "realised_pnl_inr": 50.0,
    }
    reason = build_trade_reason(trade, entry_event, exit_event)
    assert reason.entry_regime is None
    assert reason.entry_factor_exposures == {}
    assert reason.pnl_pct == pytest.approx(10.0, rel=1e-3)
```

- [ ] **Step 4.2: Implement**

```python
"""Trade reason log — joins closed trades with their entry+exit
signal_generated events; renders a human-readable reason text."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class TradeReason:
    ticker: str
    opened_at: date
    closed_at: date
    qty: int
    entry_price: float
    exit_price: float
    pnl_inr: float
    pnl_pct: float
    entry_regime: str | None
    stress_prob: float | None
    entry_factor_exposures: dict[str, float] = field(
        default_factory=dict,
    )
    exit_reason: str | None = None
    reason_text: str = ""


def _parse_payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def build_trade_reason(
    trade: dict[str, Any],
    entry_event: dict[str, Any] | None,
    exit_event: dict[str, Any] | None,
) -> TradeReason:
    entry_payload = _parse_payload(entry_event or {})
    exit_payload = _parse_payload(exit_event or {})

    entry_price = float(trade["avg_entry_price"])
    exit_price = float(trade["avg_exit_price"])
    pnl_pct = (
        (exit_price - entry_price) / entry_price * 100.0
        if entry_price > 0 else 0.0
    )

    regime = entry_payload.get("regime_label")
    stress = entry_payload.get("stress_prob")
    exposures = dict(entry_payload.get("factor_exposures") or {})
    exit_reason = exit_payload.get("reason") or exit_payload.get(
        "exit_reason",
    )

    parts: list[str] = []
    parts.append(
        f"BUY @ {entry_price:.2f}"
        + (f" because regime={regime}" if regime else "")
    )
    if exposures:
        top = sorted(
            exposures.items(), key=lambda kv: abs(kv[1]),
            reverse=True,
        )[:3]
        parts.append(
            "; factors: " + ", ".join(
                f"{k}={v:.2f}" for k, v in top
            )
        )
    parts.append(
        f". Exited @ {exit_price:.2f} ({pnl_pct:+.1f}%)"
        + (f" via {exit_reason}" if exit_reason else "")
    )

    return TradeReason(
        ticker=trade["ticker"],
        opened_at=trade["opened_at"],
        closed_at=trade["closed_at"],
        qty=int(trade["qty"]),
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_inr=float(trade["realised_pnl_inr"]),
        pnl_pct=pnl_pct,
        entry_regime=regime,
        stress_prob=(
            float(stress) if stress is not None else None
        ),
        entry_factor_exposures=exposures,
        exit_reason=exit_reason,
        reason_text="".join(parts),
    )
```

- [ ] **Step 4.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_trade_log.py -v
git add backend/algo/attribution/trade_log.py backend/algo/tests/test_trade_log.py
git commit -m "feat(algo): trade reason log builder (REGIME-6)"
```

---

## Task 5 — Factor regression (numpy lstsq, no statsmodels)

**Files:**
- Create: `backend/algo/attribution/factor_regression.py`, `backend/algo/tests/test_factor_regression.py`.

OLS regression: `R_strategy = α + β1·MKT + β2·SMB + β3·HML + ε`. `numpy.linalg.lstsq` with intercept column.

- [ ] **Step 5.1: Test**

```python
"""Factor regression tests — synthetic data with known α + β."""
from __future__ import annotations

import numpy as np
import pytest

from backend.algo.attribution.factor_regression import (
    FactorRegressionResult,
    fit_ols_regression,
)


def test_extracts_known_alpha_and_betas() -> None:
    rng = np.random.default_rng(42)
    n = 252
    mkt = rng.normal(0.0005, 0.01, n)
    smb = rng.normal(0.0001, 0.005, n)
    hml = rng.normal(0.0002, 0.005, n)
    # True: alpha=0.0003, beta_mkt=0.8, beta_smb=0.3, beta_hml=-0.1
    eps = rng.normal(0, 0.001, n)
    strategy = 0.0003 + 0.8 * mkt + 0.3 * smb + -0.1 * hml + eps
    factors = {"MKT": mkt, "SMB": smb, "HML": hml}

    out = fit_ols_regression(strategy, factors)
    assert isinstance(out, FactorRegressionResult)
    assert out.alpha == pytest.approx(0.0003, abs=0.0002)
    assert out.betas["MKT"] == pytest.approx(0.8, abs=0.02)
    assert out.betas["SMB"] == pytest.approx(0.3, abs=0.05)
    assert out.betas["HML"] == pytest.approx(-0.1, abs=0.05)
    assert 0.95 <= out.r_squared <= 1.0
    assert out.n_observations == n


def test_short_history_returns_nan() -> None:
    out = fit_ols_regression(
        np.array([0.01, -0.01, 0.02]),
        {"MKT": np.array([0.005, -0.003, 0.01])},
    )
    # n=3 with 2 columns (alpha + 1 beta) → only 1 dof; refuse
    assert np.isnan(out.alpha) or out.n_observations < 30
```

- [ ] **Step 5.2: Implement**

```python
"""OLS factor regression via numpy.linalg.lstsq.

Outputs alpha + per-factor betas + R². No statsmodels dep —
keeps the requirements light. For Indian Fama-French inputs,
the project doesn't yet have a clean source; use mock factor
returns for v3 and document the wiring follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_OBSERVATIONS = 30


@dataclass
class FactorRegressionResult:
    alpha: float
    betas: dict[str, float] = field(default_factory=dict)
    r_squared: float = 0.0
    n_observations: int = 0


def fit_ols_regression(
    strategy_returns: np.ndarray,
    factor_returns: dict[str, np.ndarray],
) -> FactorRegressionResult:
    """Fit y = α + Σ βi · xi via OLS (numpy lstsq).

    All input arrays must have equal length; rows with NaN in
    any column are dropped before the fit.
    """
    factor_keys = list(factor_returns.keys())
    if not factor_keys:
        return FactorRegressionResult(
            alpha=float("nan"), n_observations=0,
        )
    y = np.asarray(strategy_returns, dtype=float)
    cols = [np.asarray(factor_returns[k], dtype=float) for k in factor_keys]
    if any(len(c) != len(y) for c in cols):
        return FactorRegressionResult(
            alpha=float("nan"), n_observations=0,
        )
    # Drop NaN rows
    stack = np.column_stack([y] + cols)
    mask = ~np.isnan(stack).any(axis=1)
    stack = stack[mask]
    if stack.shape[0] < MIN_OBSERVATIONS:
        return FactorRegressionResult(
            alpha=float("nan"),
            n_observations=int(stack.shape[0]),
        )
    y_clean = stack[:, 0]
    X = np.column_stack(
        [np.ones(stack.shape[0])] + [stack[:, i + 1]
                                     for i in range(len(cols))],
    )
    coefs, residuals, _, _ = np.linalg.lstsq(X, y_clean, rcond=None)
    alpha = float(coefs[0])
    betas = {k: float(coefs[i + 1]) for i, k in enumerate(factor_keys)}
    # R² = 1 - SS_res / SS_tot
    y_mean = y_clean.mean()
    ss_tot = float(((y_clean - y_mean) ** 2).sum())
    y_pred = X @ coefs
    ss_res = float(((y_clean - y_pred) ** 2).sum())
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return FactorRegressionResult(
        alpha=alpha,
        betas=betas,
        r_squared=r_squared,
        n_observations=int(stack.shape[0]),
    )
```

- [ ] **Step 5.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_factor_regression.py -v
git add backend/algo/attribution/factor_regression.py backend/algo/tests/test_factor_regression.py
git commit -m "feat(algo): OLS factor regression via numpy lstsq (REGIME-6)"
```

---

## Task 6 — Daily + monthly job orchestrators + register

**Files:**
- Create: `backend/algo/attribution/job.py`.
- Modify: `backend/algo/jobs/__init__.py`, `backend/jobs/executor.py`.
- Test: `backend/algo/tests/test_attribution_jobs.py`.

Both jobs are pragmatic for v3:
- **Daily Brinson**: pull today's closed trades; compute portfolio sector weights; mock NIFTY 50 sector weights (equal weight in V3 — wire real index data in v3.1); compute brinson; persist.
- **Monthly factor regression**: pull last 30 days of strategy daily P&L from `algo.runs.equity_curve`; compute strategy daily returns; use mock factor returns (random.normal seeded by user/strategy/period for repeatability) — flagged as `mock_data: True` in the persisted row. Real factor data comes in v3.1.

Persist via `_pg_session()` (from `backend.db.engine`) per CLAUDE.md §5.1.

Test focuses on orchestrator skeleton — no end-to-end DB write.

- [ ] **Step 6.1: Implement** — straightforward following the patterns above. ~150 lines total.

- [ ] **Step 6.2: Register**

In `backend/algo/jobs/__init__.py`:
```python
from backend.algo.attribution import job as attribution_job  # noqa: F401
```

In `backend/jobs/executor.py` (after REGIME-5 wrapper):
```python
@register_job("attribution_daily_brinson")
def _attribution_daily_brinson(payload: dict) -> dict:
    from backend.algo.attribution.job import daily_brinson_job
    return daily_brinson_job(payload)


@register_job("attribution_monthly_regression")
def _attribution_monthly_regression(payload: dict) -> dict:
    from backend.algo.attribution.job import (
        monthly_factor_regression_job,
    )
    return monthly_factor_regression_job(payload)
```

- [ ] **Step 6.3: Run + commit**

---

## Task 7 — Routes: GET /v1/algo/attribution/{daily,trades,regression}

**Files:**
- Create: `backend/algo/routes/attribution.py`.
- Modify: `backend/routes.py`, `backend/algo/routes/__init__.py`.
- Test: `backend/algo/tests/test_attribution_routes.py`.

Three endpoints; all paginated, filterable by user/strategy. Cache: TTL_VOLATILE for daily (60s), TTL_STABLE for trades + regression (300s).

- [ ] **Step 7.1: Implement** — mirror REGIME-5's `gates` route pattern. Auth via `pro_or_superuser`. Read PG via `_pg_session()`.

- [ ] **Step 7.2: Mount** — same pattern as REGIME-2b (`create_attribution_router()` factory + `app.include_router` in `routes.py`).

- [ ] **Step 7.3: Run + commit**

---

## Task 8 — Frontend: useAttribution hook + AttributionPanel + mount

**Files:**
- Create: `frontend/hooks/useAttribution.ts`, `frontend/components/algo-trading/AttributionPanel.tsx`.
- Modify: `frontend/components/algo-trading/PaperTab.tsx` (mount below ActiveRunsPanel as a collapsible).
- Modify: `e2e/utils/selectors.ts` (testids).

Two-tab panel: "Daily Brinson" (table) + "Trade Reasons" (table). Strategy filter is "current selected live strategy" (use `useStrategies()` + a selector — mirror existing patterns in PaperTab).

Both tables use the existing tabular pattern from REGIME-2b's FactorScoresTab — much simpler since the data is small.

- [ ] **Step 8.1: Implement** — full code inline in the slice; ~250 lines for the panel + 50 for the hook.

- [ ] **Step 8.2: Test (lint)** — `npx eslint`.

- [ ] **Step 8.3: Hot-reload + commit**

---

## Task 9 — E2E + push

**Files:**
- Create: `e2e/tests/frontend/algo-attribution.spec.ts`.

Permissive: navigate Trading tab → assert AttributionPanel testid mounts. Mirror REGIME-2b's pattern.

- [ ] **Step 9.1: E2E + push**

---

## Acceptance Checklist

- [ ] Signal payload extension landed at all 3 emit sites (paper/live/backtest)
- [ ] Brinson decomposition algebraic identity test passes (alloc + sel + inter == active return)
- [ ] PG migrations apply cleanly; tables created
- [ ] Trade reason log joins entry/exit events correctly; backward compat with pre-v3 events
- [ ] Factor regression extracts known α + βs from synthetic data
- [ ] Both jobs registered in `JOB_EXECUTORS`
- [ ] Routes return 200 with proper shape
- [ ] AttributionPanel mounts in PaperTab
- [ ] E2E spec passes
- [ ] Branch pushed

---

## Out of Scope for REGIME-6

- **Real Fama-French factor data ingestion** — mock data for now; real NSE-specific FF factor returns wired in v3.1.
- **Multi-strategy portfolio Brinson** — single-strategy single-user constraint per spec §1.
- **Per-sector regime overlay** — Brinson uses global NIFTY 50 universe; per-sector regime is v4.
- **Historical backfill of factor regression** — job runs monthly going forward; no retroactive fill.
- **NIFTY 50 actual constituent weights** — using equal-weight (1/50) baseline; real weights wired in v3.1 (requires NSE API integration).
- **Weekly attribution** — daily + monthly only.
- **Cross-strategy attribution** — single strategy per user.
