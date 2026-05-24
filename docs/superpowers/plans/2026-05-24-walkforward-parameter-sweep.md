# Walk-Forward Parameter Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a 1D parameter sweep on top of the existing walk-forward CV — user picks a saved strategy, one tunable field (curated whitelist), and a list of values; engine runs a full walk-forward per value and reports per-variant metrics plus a cross-variant PBO.

**Architecture:** Three-level row tree in `algo.runs` (sweep → walkforward → backtest). Serial execution; mutate strategy AST in memory only. Reuse existing `run_walkforward_job` per variant; aggregate persisted `summary_json` rows post-hoc to compute the cross-variant PBO via the existing `probability_of_backtest_overfitting()` in `metrics.py`.

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy 2.0 async, Alembic, pytest), Next.js 16 + React 19 (Vitest, ECharts), Playwright (E2E).

**Reference spec:** `docs/superpowers/specs/2026-05-24-walkforward-parameter-sweep-design.md`.

**Branch:** Work on `feature/walkforward-parameter-sweep` (already created off `dev`). Squash merge per CLAUDE.md §4.4 #27.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `backend/db/migrations/versions/<ts>_add_parent_sweep_id.py` | create | Alembic migration adding `parent_sweep_id` to `algo.runs` |
| `backend/algo/backtest/sweep_types.py` | create | Pydantic types `SweepConfig`, `SweepVariantSummary`, `SweepResult` |
| `backend/algo/backtest/sweep_whitelist.py` | create | `SweepableField` dataclass, `SWEEPABLE_FIELDS` dict, `validate_swept_values` |
| `backend/algo/backtest/sweep.py` | create | `run_sweep_job` orchestrator, `_mutate_ast` helper |
| `backend/algo/backtest/sweep_pbo.py` | create | Pure: `variant_equity_curve`, `build_returns_matrix`, `compute_sweep_pbo` |
| `backend/algo/backtest/runs_repo.py` | modify | Add `create_pending_sweep`, `list_children_of_sweep`, support `parent_sweep_id` kwarg in `create_pending` |
| `backend/algo/routes/sweep.py` | create | HTTP routes — POST /run, GET /runs/{id}, GET /runs, GET /fields |
| `backend/algo/routes/__init__.py` | modify | Mount sweep router |
| `backend/algo/backtest/tests/test_sweep_whitelist.py` | create | Unit tests for whitelist + validators |
| `backend/algo/backtest/tests/test_sweep_mutate_ast.py` | create | Unit tests for `_mutate_ast` |
| `backend/algo/backtest/tests/test_sweep_pbo.py` | create | Unit tests for PBO aggregation helpers |
| `backend/algo/backtest/tests/test_sweep_runner.py` | create | Integration tests for `run_sweep_job` |
| `backend/algo/tests/test_sweep_routes.py` | create | HTTP route tests |
| `frontend/lib/types/algoSweep.ts` | create | TypeScript shapes |
| `frontend/hooks/useSweepRuns.ts` | create | SWR hooks (mirrors `useWalkForwardRuns.ts`) |
| `frontend/hooks/useSweepableFields.ts` | create | SWR hook for `GET /v1/algo/sweep/fields` |
| `frontend/components/algo-trading/SweepSubTab.tsx` | create | Top-level container |
| `frontend/components/algo-trading/SweepForm.tsx` | create | Input form |
| `frontend/components/algo-trading/SweepProgressPanel.tsx` | create | Per-variant progress UI |
| `frontend/components/algo-trading/SweepResultsTable.tsx` | create | Per-variant table (Sharpe-ranked) |
| `frontend/components/algo-trading/SweepEquityCurves.tsx` | create | Overlaid equity curves |
| `frontend/components/algo-trading/SweepPboBadge.tsx` | create | PBO color band + verdict text |
| `frontend/components/algo-trading/SweepPromoteModal.tsx` | create | "Save winner as new strategy" modal |
| `frontend/components/algo-trading/StrategiesTab.tsx` | modify | Mount the new sub-tab |
| `frontend/components/algo-trading/__tests__/SweepForm.test.tsx` | create | Vitest |
| `frontend/components/algo-trading/__tests__/SweepResultsTable.test.tsx` | create | Vitest |
| `frontend/components/algo-trading/__tests__/SweepPboBadge.test.tsx` | create | Vitest |
| `e2e/pages/frontend/SweepPage.ts` | create | Playwright POM |
| `e2e/algo-trading/sweep.spec.ts` | create | E2E smoke test |
| `e2e/utils/selectors.ts` | modify | Add new sweep testids to the `FE` registry |
| `PROGRESS.md` | modify | Dated session entry |

---

## Task 1: PG migration + Pydantic types

**Files:**
- Create: `backend/db/migrations/versions/2026_05_24_add_parent_sweep_id.py`
- Create: `backend/algo/backtest/sweep_types.py`
- Test: `backend/algo/backtest/tests/test_sweep_types.py`

- [ ] **Step 1.1: Write the failing test for SweepConfig validation**

Create `backend/algo/backtest/tests/test_sweep_types.py`:

```python
"""Tests for sweep Pydantic types."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.backtest.sweep_types import (
    SweepConfig,
    SweepResult,
    SweepVariantSummary,
)


def test_sweep_config_minimal_valid():
    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 11, 23),
        period_end=date(2026, 5, 23),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14],
    )
    assert cfg.train_days == 60
    assert cfg.test_days == 30
    assert cfg.step_days == 30
    assert cfg.initial_capital_inr == Decimal("100000.00")
    assert cfg.regime_stratified is False
    assert cfg.interval_sec == 86400


def test_sweep_config_rejects_extra_fields():
    with pytest.raises(Exception):
        SweepConfig(
            base_strategy_id=uuid4(),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 6, 1),
            swept_field="cooldown_days",
            swept_values=[3, 7],
            bogus="extra",
        )


def test_sweep_variant_summary_completed():
    s = SweepVariantSummary(
        variant_index=0,
        swept_value=7,
        walkforward_run_id=uuid4(),
        avg_pnl_pct=Decimal("3.74"),
        avg_win_rate_pct=Decimal("63.9"),
        avg_max_drawdown_pct=Decimal("7.63"),
        sharpe=Decimal("0.648"),
        dsr=Decimal("0.62"),
        n_trades=83,
        status="completed",
    )
    assert s.status == "completed"
    assert s.error_text is None


def test_sweep_result_pending_state():
    r = SweepResult(
        run_id=uuid4(),
        base_strategy_id=uuid4(),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14],
        variants=[],
        cross_variant_pbo=None,
        returns_matrix_shape=(0, 0),
        winner_variant_index=None,
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        status="pending",
    )
    assert r.status == "pending"
    assert r.cross_variant_pbo is None
```

- [ ] **Step 1.2: Run test — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_types.py -v
```

Expected: `ImportError` on `backend.algo.backtest.sweep_types`.

- [ ] **Step 1.3: Create the sweep_types module**

Create `backend/algo/backtest/sweep_types.py`:

```python
"""Pydantic types shared by the sweep runner, routes,
and aggregator. Mirrors the shapes in
``backend/algo/backtest/types.py`` (single backtest)
and the walk-forward types — sweep types live one level
higher.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SweepConfig(BaseModel):
    """Request body for POST /v1/algo/sweep/run."""

    model_config = ConfigDict(extra="forbid")

    base_strategy_id: UUID
    period_start: date
    period_end: date
    train_days: int = Field(default=60, ge=1)
    test_days: int = Field(default=30, ge=1)
    step_days: int = Field(default=30, ge=1)
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"),
        ge=Decimal("1000.00"),
    )
    regime_stratified: bool = False
    swept_field: str  # short whitelist key
    swept_values: list[Any]  # validated per field meta
    interval_sec: int = 86400


class SweepVariantSummary(BaseModel):
    """One variant's aggregate, embedded in SweepResult."""

    model_config = ConfigDict(extra="forbid")

    variant_index: int
    swept_value: Any
    walkforward_run_id: UUID
    avg_pnl_pct: Decimal
    avg_win_rate_pct: Decimal
    avg_max_drawdown_pct: Decimal
    sharpe: Decimal
    dsr: Decimal
    n_trades: int
    status: Literal["completed", "failed", "skipped"]
    error_text: str | None = None


class SweepResult(BaseModel):
    """Sweep parent row's ``summary_json`` shape.

    ``swept_field`` is the short whitelist key (e.g.
    ``"cooldown_days"``) — NOT the dotted AST path. The
    path is derivable via SWEEPABLE_FIELDS at read time.
    Keeping the key (not the path) means a future rename
    of the underlying AST path doesn't orphan historical
    sweep rows.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    base_strategy_id: UUID
    swept_field: str
    swept_values: list[Any]
    variants: list[SweepVariantSummary] = Field(
        default_factory=list,
    )
    cross_variant_pbo: Decimal | None = None
    returns_matrix_shape: tuple[int, int] = (0, 0)
    winner_variant_index: int | None = None
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal[
        "pending", "running", "completed", "failed",
    ] = "pending"
```

- [ ] **Step 1.4: Run test — expect 4 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_types.py -v
```

Expected: 4 passed.

- [ ] **Step 1.5: Create the Alembic migration**

Find the current head revision:

```bash
docker compose exec backend alembic heads
```

Take the printed revision_id (e.g. `e7f8a9b0c1d2`) and use it as `down_revision`.

Create `backend/db/migrations/versions/2026_05_24_add_parent_sweep_id.py`:

```python
"""Add parent_sweep_id to algo.runs.

Revision ID: 2026_05_24_sweep
Revises: <CURRENT_HEAD>
Create Date: 2026-05-24

Adds the parent_sweep_id column on algo.runs to support
the walk-forward parameter sweep epic. The column is
nullable and references algo.runs(id) — sweep parent
rows have NULL; per-variant walkforward rows have the
sweep's id; per-window backtest rows have NULL (they
chain via parent_walkforward_id, which already exists).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_05_24_sweep"
down_revision = "<REPLACE_WITH_ALEMBIC_HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "parent_sweep_id",
            sa.UUID(),
            sa.ForeignKey(
                "algo.runs.id", ondelete="SET NULL",
            ),
            nullable=True,
        ),
        schema="algo",
    )
    op.create_index(
        "idx_runs_parent_sweep_id",
        "runs",
        ["parent_sweep_id"],
        schema="algo",
        postgresql_where=sa.text(
            "parent_sweep_id IS NOT NULL",
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_runs_parent_sweep_id",
        table_name="runs",
        schema="algo",
    )
    op.drop_column(
        "runs", "parent_sweep_id", schema="algo",
    )
```

Replace `<REPLACE_WITH_ALEMBIC_HEAD>` with the actual head string from the `alembic heads` output.

- [ ] **Step 1.6: Apply migration and verify**

```bash
docker compose exec backend alembic upgrade head
docker compose exec postgres psql -U app -d aiagent -c \
  "SELECT column_name FROM information_schema.columns \
   WHERE table_schema='algo' AND table_name='runs' \
   AND column_name='parent_sweep_id';"
```

Expected output: one row `parent_sweep_id`.

- [ ] **Step 1.7: Commit**

```bash
git add backend/algo/backtest/sweep_types.py \
        backend/algo/backtest/tests/test_sweep_types.py \
        backend/db/migrations/versions/2026_05_24_add_parent_sweep_id.py
git commit -m "$(cat <<'EOF'
feat(sweep): Pydantic types + algo.runs.parent_sweep_id

Foundational slice — Pydantic models (SweepConfig,
SweepVariantSummary, SweepResult) used by the sweep
orchestrator and HTTP routes, plus the Alembic migration
adding parent_sweep_id on algo.runs.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Whitelist + validators

**Files:**
- Create: `backend/algo/backtest/sweep_whitelist.py`
- Create: `backend/algo/backtest/tests/test_sweep_whitelist.py`

- [ ] **Step 2.1: Write failing tests**

Create `backend/algo/backtest/tests/test_sweep_whitelist.py`:

```python
"""Tests for the sweep field whitelist + validator."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.backtest.sweep_whitelist import (
    SWEEPABLE_FIELDS,
    SweepableField,
    validate_swept_values,
)


def test_whitelist_has_seven_fields():
    """Spec-locked count — change requires updating
    the docs/spec too."""
    assert len(SWEEPABLE_FIELDS) == 7
    assert "cooldown_days" in SWEEPABLE_FIELDS
    assert "stop_loss_pct" in SWEEPABLE_FIELDS
    assert "max_holding_days" in SWEEPABLE_FIELDS
    assert "max_qty" in SWEEPABLE_FIELDS
    assert "min_adtv_inr" in SWEEPABLE_FIELDS
    assert "daily_max_loss_pct" in SWEEPABLE_FIELDS
    assert "max_concentration_pct" in SWEEPABLE_FIELDS


def test_cooldown_field_metadata():
    f = SWEEPABLE_FIELDS["cooldown_days"]
    assert isinstance(f, SweepableField)
    assert f.path == (
        "risk.per_trade.cooldown_after_failed_exit_days"
    )
    assert f.field_type == "int"
    assert f.min_value == Decimal("0")
    assert f.max_value == Decimal("60")


def test_validate_accepts_valid_int_values():
    out = validate_swept_values(
        "cooldown_days", [3, 7, 14, 21],
    )
    assert out == [3, 7, 14, 21]
    assert all(isinstance(v, int) for v in out)


def test_validate_accepts_valid_decimal_values():
    out = validate_swept_values(
        "stop_loss_pct", ["1.0", "2.5", "5.0"],
    )
    assert out == [
        Decimal("1.0"), Decimal("2.5"), Decimal("5.0"),
    ]


def test_validate_rejects_unknown_field():
    with pytest.raises(ValueError, match="unknown field"):
        validate_swept_values("bogus_field", [1, 2, 3])


def test_validate_rejects_single_value():
    with pytest.raises(ValueError, match="at least 2"):
        validate_swept_values("cooldown_days", [7])


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="at least 2"):
        validate_swept_values("cooldown_days", [])


def test_validate_rejects_duplicates():
    with pytest.raises(ValueError, match="duplicate"):
        validate_swept_values(
            "cooldown_days", [7, 14, 7, 21],
        )


def test_validate_rejects_out_of_range_high():
    with pytest.raises(ValueError, match="out of range"):
        validate_swept_values(
            "cooldown_days", [7, 14, 999],
        )


def test_validate_rejects_out_of_range_low():
    with pytest.raises(ValueError, match="out of range"):
        validate_swept_values(
            "cooldown_days", [-1, 7],
        )


def test_validate_rejects_wrong_type_for_int_field():
    with pytest.raises(ValueError, match="not a valid int"):
        validate_swept_values(
            "cooldown_days", [7, "seven"],
        )
```

- [ ] **Step 2.2: Run tests — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_whitelist.py -v
```

Expected: `ImportError`.

- [ ] **Step 2.3: Implement the whitelist module**

Create `backend/algo/backtest/sweep_whitelist.py`:

```python
"""Curated whitelist of fields the v1 sweep UI exposes,
with per-field type + range metadata used for validation.

The whitelist is intentionally narrow — seven fields
covering ~90% of practical parameter exploration. Adding
a field is a one-line change here plus the corresponding
unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal


@dataclass(frozen=True)
class SweepableField:
    """One whitelist entry."""

    path: str        # dotted AST path
    label: str       # UI label
    field_type: Literal["int", "decimal"]
    min_value: Decimal
    max_value: Decimal


SWEEPABLE_FIELDS: dict[str, SweepableField] = {
    "cooldown_days": SweepableField(
        path=(
            "risk.per_trade."
            "cooldown_after_failed_exit_days"
        ),
        label="Cooldown (days)",
        field_type="int",
        min_value=Decimal("0"),
        max_value=Decimal("60"),
    ),
    "stop_loss_pct": SweepableField(
        path="risk.per_trade.stop_loss_pct",
        label="Stop loss %",
        field_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("20.0"),
    ),
    "max_holding_days": SweepableField(
        path="risk.per_trade.max_holding_days",
        label="Max holding days",
        field_type="int",
        min_value=Decimal("1"),
        max_value=Decimal("60"),
    ),
    "max_qty": SweepableField(
        path="risk.per_trade.max_qty",
        label="Max qty per fill",
        field_type="int",
        min_value=Decimal("1"),
        max_value=Decimal("100000"),
    ),
    "min_adtv_inr": SweepableField(
        path="universe.filter.min_adtv_inr",
        label="Min ADTV (₹)",
        field_type="decimal",
        min_value=Decimal("10000000"),
        max_value=Decimal("1000000000"),
    ),
    "daily_max_loss_pct": SweepableField(
        path="risk.daily.max_loss_pct",
        label="Daily max loss %",
        field_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("10.0"),
    ),
    "max_concentration_pct": SweepableField(
        path="risk.portfolio.max_concentration_pct",
        label="Max position concentration %",
        field_type="decimal",
        min_value=Decimal("5"),
        max_value=Decimal("50"),
    ),
}


def _coerce_one(
    raw: object, field: SweepableField,
) -> int | Decimal:
    if field.field_type == "int":
        if isinstance(raw, bool):
            raise ValueError(
                f"{raw!r} is not a valid int "
                f"(got bool)",
            )
        if isinstance(raw, int):
            v: int | Decimal = raw
        elif isinstance(raw, str):
            try:
                v = int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"{raw!r} is not a valid int",
                ) from exc
        else:
            raise ValueError(
                f"{raw!r} is not a valid int",
            )
        if not (
            field.min_value <= Decimal(v) <= field.max_value
        ):
            raise ValueError(
                f"{v} is out of range "
                f"[{field.min_value}, {field.max_value}] "
                f"for {field.label}",
            )
        return v
    # decimal
    try:
        v = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{raw!r} is not a valid decimal",
        ) from exc
    if not (
        field.min_value <= v <= field.max_value
    ):
        raise ValueError(
            f"{v} is out of range "
            f"[{field.min_value}, {field.max_value}] "
            f"for {field.label}",
        )
    return v


def validate_swept_values(
    field_key: str, values: list[object],
) -> list[int | Decimal]:
    """Validate + coerce. Raises ValueError on bad input.

    Enforces:
      - field_key ∈ SWEEPABLE_FIELDS
      - len(values) ≥ 2
      - each value parses to the field's type
      - each value within [min_value, max_value]
      - all values distinct (no duplicates)
    """
    if field_key not in SWEEPABLE_FIELDS:
        raise ValueError(
            f"unknown field {field_key!r}; "
            f"valid: {sorted(SWEEPABLE_FIELDS)}",
        )
    if len(values) < 2:
        raise ValueError(
            "sweep requires at least 2 values",
        )
    field = SWEEPABLE_FIELDS[field_key]
    coerced = [_coerce_one(v, field) for v in values]
    if len(set(coerced)) != len(coerced):
        raise ValueError(
            f"duplicate values in {coerced}",
        )
    return coerced
```

- [ ] **Step 2.4: Run tests — expect 11 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_whitelist.py -v
```

Expected: 11 passed.

- [ ] **Step 2.5: Commit**

```bash
git add backend/algo/backtest/sweep_whitelist.py \
        backend/algo/backtest/tests/test_sweep_whitelist.py
git commit -m "$(cat <<'EOF'
feat(sweep): curated whitelist of 7 sweepable fields

SweepableField dataclass + SWEEPABLE_FIELDS dict + the
validate_swept_values coercer. Seven fields chosen to
cover ~90% of practical parameter exploration; AST-path
escape hatch deferred to v2 per design decision.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: AST mutation helper

**Files:**
- Create: `backend/algo/backtest/tests/test_sweep_mutate_ast.py`

The mutation function will live in `backend/algo/backtest/sweep.py` (created in Task 4) but the test ships first so it can drive the API.

- [ ] **Step 3.1: Write failing tests**

Create `backend/algo/backtest/tests/test_sweep_mutate_ast.py`:

```python
"""Tests for the AST-mutation helper used by the sweep
orchestrator."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.algo.backtest.sweep import _mutate_ast
from backend.algo.strategy.ast import parse_strategy

# Load the v3 template once for path-resolution tests.
TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "strategy" / "templates"
    / "rsi2_connors_daily_v3.json"
)


def _load_v3():
    return parse_strategy(
        json.loads(TEMPLATE_PATH.read_text()),
    )


def test_mutate_cooldown_field_path():
    s = _load_v3()
    original = s.risk.per_trade.cooldown_after_failed_exit_days
    s2 = _mutate_ast(
        s,
        "risk.per_trade.cooldown_after_failed_exit_days",
        14,
    )
    # Mutated value visible on copy
    assert s2.risk.per_trade.cooldown_after_failed_exit_days == 14
    # Source untouched (deep copy)
    assert s.risk.per_trade.cooldown_after_failed_exit_days == original


def test_mutate_decimal_field_path():
    from decimal import Decimal
    s = _load_v3()
    s2 = _mutate_ast(
        s,
        "risk.per_trade.stop_loss_pct",
        Decimal("3.0"),
    )
    assert s2.risk.per_trade.stop_loss_pct == Decimal("3.0")


def test_mutate_min_adtv_inr_path():
    from decimal import Decimal
    s = _load_v3()
    s2 = _mutate_ast(
        s,
        "universe.filter.min_adtv_inr",
        Decimal("100000000"),
    )
    assert s2.universe.filter.min_adtv_inr == Decimal(
        "100000000",
    )


def test_mutate_unknown_path_raises():
    s = _load_v3()
    with pytest.raises(ValueError, match="resolve"):
        _mutate_ast(s, "bogus.path.nope", 7)


def test_mutate_partial_path_raises():
    s = _load_v3()
    with pytest.raises(ValueError, match="resolve"):
        _mutate_ast(
            s, "risk.per_trade.does_not_exist", 7,
        )
```

- [ ] **Step 3.2: Run test — expect ImportError on sweep**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_mutate_ast.py -v
```

Expected: `ImportError` on `backend.algo.backtest.sweep`.

- [ ] **Step 3.3: Create sweep.py with the mutation helper only**

Create `backend/algo/backtest/sweep.py`:

```python
"""Sweep orchestrator + AST mutation helper.

The orchestrator (``run_sweep_job``) is added in a
follow-up task; this module bootstraps with just the
mutation primitive that drives every variant in a sweep.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

_logger = logging.getLogger(__name__)


def _mutate_ast(
    strategy: Any, path: str, value: Any,
) -> Any:
    """Return a deep copy of ``strategy`` with the nested
    field at ``path`` set to ``value``.

    Path is dotted (e.g.
    ``"risk.per_trade.stop_loss_pct"``). Each segment
    must resolve via ``getattr`` on the corresponding
    Pydantic model. If any segment doesn't exist, raises
    ``ValueError`` referencing the failing segment.
    """
    parts = path.split(".")
    if not parts:
        raise ValueError(
            f"empty path: {path!r}",
        )
    new = copy.deepcopy(strategy)
    cur = new
    for seg in parts[:-1]:
        if not hasattr(cur, seg):
            raise ValueError(
                f"cannot resolve path {path!r}: "
                f"segment {seg!r} not found on "
                f"{type(cur).__name__}",
            )
        cur = getattr(cur, seg)
        if cur is None:
            raise ValueError(
                f"cannot resolve path {path!r}: "
                f"segment {seg!r} is None",
            )
    last = parts[-1]
    if not hasattr(cur, last):
        raise ValueError(
            f"cannot resolve path {path!r}: "
            f"final segment {last!r} not found on "
            f"{type(cur).__name__}",
        )
    setattr(cur, last, value)
    return new
```

- [ ] **Step 3.4: Run test — expect 5 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_mutate_ast.py -v
```

Expected: 5 passed.

- [ ] **Step 3.5: Commit**

```bash
git add backend/algo/backtest/sweep.py \
        backend/algo/backtest/tests/test_sweep_mutate_ast.py
git commit -m "$(cat <<'EOF'
feat(sweep): _mutate_ast helper for in-memory AST cloning

Pure function — deep copies a Strategy Pydantic AST and
sets a nested field by dotted path. Each whitelist field's
path resolves cleanly on the v3 + intraday templates; bad
paths raise ValueError with a useful message.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: PBO aggregation helpers

**Files:**
- Create: `backend/algo/backtest/sweep_pbo.py`
- Create: `backend/algo/backtest/tests/test_sweep_pbo.py`

- [ ] **Step 4.1: Write failing tests**

Create `backend/algo/backtest/tests/test_sweep_pbo.py`:

```python
"""Tests for sweep PBO-aggregation helpers."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest

from backend.algo.backtest.sweep_pbo import (
    build_returns_matrix,
    compute_sweep_pbo,
    variant_equity_curve,
)
from backend.algo.backtest.types import (
    BacktestSummary,
    EquityPoint,
)


def _fake_summary(
    start: date, equity_seq: list[float],
) -> BacktestSummary:
    """Minimal BacktestSummary fixture for testing."""
    pts = [
        EquityPoint(
            bar_date=start + timedelta(days=i),
            equity_inr=Decimal(str(v)),
        )
        for i, v in enumerate(equity_seq)
    ]
    return BacktestSummary(
        run_id=__import__("uuid").uuid4(),
        strategy_id=__import__("uuid").uuid4(),
        status="completed",
        period_start=start,
        period_end=start + timedelta(
            days=len(equity_seq) - 1,
        ),
        initial_capital_inr=Decimal(str(equity_seq[0])),
        final_equity_inr=Decimal(str(equity_seq[-1])),
        total_pnl_inr=Decimal(str(equity_seq[-1]))
        - Decimal(str(equity_seq[0])),
        total_pnl_pct=Decimal("0"),
        total_fees_inr=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        started_at=__import__("datetime").datetime.now(),
        completed_at=__import__(
            "datetime",
        ).datetime.now(),
        fee_rates_version="test",
        equity_curve=pts,
    )


def test_variant_equity_curve_single_window():
    w = _fake_summary(date(2025, 1, 1), [100, 102, 105])
    out = variant_equity_curve(
        [w], initial_capital=Decimal("1000"),
    )
    assert len(out) == 3
    assert out[0][1] == Decimal("1000")
    # 102/100 * 1000 = 1020
    assert out[1][1] == Decimal("1020")
    # 105/100 * 1000 = 1050
    assert out[2][1] == Decimal("1050")


def test_variant_equity_curve_chains_windows():
    w1 = _fake_summary(date(2025, 1, 1), [100, 110])
    w2 = _fake_summary(date(2025, 2, 1), [100, 90])
    out = variant_equity_curve(
        [w1, w2], initial_capital=Decimal("1000"),
    )
    # Window 1: ends at 1100 (10% gain)
    # Window 2: 90/100 * 1100 = 990 (10% loss)
    assert out[-1][1] == Decimal("990")


def test_variant_equity_curve_empty():
    out = variant_equity_curve(
        [], initial_capital=Decimal("1000"),
    )
    assert out == []


def test_build_returns_matrix_two_aligned_variants():
    a = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 2), Decimal("101")),
        (date(2025, 1, 3), Decimal("103")),
    ]
    b = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 2), Decimal("102")),
        (date(2025, 1, 3), Decimal("99")),
    ]
    R, dates = build_returns_matrix([a, b])
    assert R.shape == (2, 2)
    np.testing.assert_allclose(R[0], [0.01, 0.02])
    np.testing.assert_allclose(
        R[1], [(103 - 101) / 101, (99 - 102) / 102],
    )


def test_build_returns_matrix_drops_non_common_dates():
    a = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 2), Decimal("101")),
        (date(2025, 1, 3), Decimal("103")),
    ]
    b = [
        (date(2025, 1, 2), Decimal("100")),
        (date(2025, 1, 3), Decimal("102")),
    ]
    R, dates = build_returns_matrix([a, b])
    # Common dates: 2025-01-02, 2025-01-03
    # T = len(common) - 1 = 1
    assert R.shape == (1, 2)
    assert len(dates) == 1


def test_build_returns_matrix_insufficient_common_dates():
    a = [(date(2025, 1, 1), Decimal("100"))]
    b = [(date(2025, 1, 2), Decimal("100"))]
    R, dates = build_returns_matrix([a, b])
    assert R.shape == (0, 0)
    assert dates == []


def test_compute_sweep_pbo_too_few_variants():
    R = np.random.RandomState(42).normal(
        0, 0.01, size=(50, 1),
    )
    assert compute_sweep_pbo(R) is None


def test_compute_sweep_pbo_too_few_days():
    R = np.random.RandomState(42).normal(
        0, 0.01, size=(5, 3),
    )
    assert compute_sweep_pbo(R) is None


def test_compute_sweep_pbo_returns_decimal():
    rng = np.random.RandomState(42)
    R = rng.normal(0, 0.01, size=(100, 5))
    pbo = compute_sweep_pbo(R)
    assert pbo is not None
    assert isinstance(pbo, Decimal)
    assert Decimal("0") <= pbo <= Decimal("1")
```

- [ ] **Step 4.2: Run tests — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_pbo.py -v
```

Expected: `ImportError` on `backend.algo.backtest.sweep_pbo`.

- [ ] **Step 4.3: Implement sweep_pbo.py**

Create `backend/algo/backtest/sweep_pbo.py`:

```python
"""Cross-variant PBO aggregation primitives.

Three pure functions:
  1. ``variant_equity_curve`` — chains per-window
     equity curves into one continuous variant curve.
  2. ``build_returns_matrix`` — aligns N variants on
     common dates and returns a (T, N) returns matrix.
  3. ``compute_sweep_pbo`` — calls the existing CSCV
     PBO implementation on a returns matrix, returning
     a Decimal or None.

All three are pure; no I/O, no DB. The orchestrator
fetches walk-forward summaries from PG and pipes them
through these helpers.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import numpy as np

from backend.algo.backtest.metrics import (
    probability_of_backtest_overfitting,
)
from backend.algo.backtest.types import BacktestSummary

_logger = logging.getLogger(__name__)


def variant_equity_curve(
    window_summaries: list[BacktestSummary],
    initial_capital: Decimal,
) -> list[tuple[date, Decimal]]:
    """Chain per-window returns into one continuous
    variant equity curve.

    Each walk-forward window's curve starts fresh at
    ``initial_capital_inr`` (the backtest engine resets
    between folds). We compute each window's daily
    MULTIPLIER curve and apply it to a running capital
    that starts at ``initial_capital`` and compounds
    across windows.
    """
    points: list[tuple[date, Decimal]] = []
    running = initial_capital
    for w in sorted(
        window_summaries, key=lambda s: s.period_start,
    ):
        if not w.equity_curve:
            continue
        start_eq = Decimal(
            str(w.equity_curve[0].equity_inr),
        )
        if start_eq == 0:
            _logger.warning(
                "variant_equity_curve: window starts "
                "at zero equity; skipping",
            )
            continue
        for pt in w.equity_curve:
            ratio = (
                Decimal(str(pt.equity_inr)) / start_eq
            )
            points.append((pt.bar_date, running * ratio))
        running = points[-1][1]
    return points


def build_returns_matrix(
    variants_curves: list[
        list[tuple[date, Decimal]]
    ],
) -> tuple[np.ndarray, list[date]]:
    """Align N variants on common dates; return
    ``(R, common_dates)`` where R has shape (T, N).

    ``T = len(common_dates) - 1`` because returns are
    pairwise differences. Returns ``(zeros((0,0)), [])``
    when fewer than 2 common dates.
    """
    if not variants_curves:
        return (np.zeros((0, 0)), [])
    date_sets = [
        {d for d, _ in curve}
        for curve in variants_curves
    ]
    common = sorted(set.intersection(*date_sets))
    if len(common) < 2:
        return (np.zeros((0, 0)), [])

    cols = []
    for curve in variants_curves:
        d2v = {d: float(v) for d, v in curve}
        seq = np.array(
            [d2v[d] for d in common], dtype=float,
        )
        # Pairwise returns; guard div-by-zero
        with np.errstate(
            divide="ignore", invalid="ignore",
        ):
            rets = np.diff(seq) / seq[:-1]
        # Replace inf/nan (zero-equity bars,
        # period-end MTM artifacts) with 0
        rets = np.where(
            np.isfinite(rets), rets, 0.0,
        )
        cols.append(rets)
    R = np.column_stack(cols)
    return (R, common[1:])


def compute_sweep_pbo(R: np.ndarray) -> Decimal | None:
    """Cross-variant PBO. Returns None when undefined
    (N < 2, T < 8, or PBO calc itself returns NaN).
    """
    if R.size == 0:
        return None
    T, N = R.shape
    if N < 2 or T < 8:
        return None
    n_blocks = 16 if T >= 16 else 8
    pbo = probability_of_backtest_overfitting(
        R, n_blocks=n_blocks,
    )
    if pbo != pbo:  # NaN
        return None
    return Decimal(str(round(pbo, 3)))
```

- [ ] **Step 4.4: Run tests — expect 9 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_pbo.py -v
```

Expected: 9 passed.

- [ ] **Step 4.5: Commit**

```bash
git add backend/algo/backtest/sweep_pbo.py \
        backend/algo/backtest/tests/test_sweep_pbo.py
git commit -m "$(cat <<'EOF'
feat(sweep): PBO aggregation helpers (3 pure functions)

variant_equity_curve chains per-window equity curves into
one continuous variant curve. build_returns_matrix aligns
N variants on common dates → (T, N) matrix. compute_sweep_pbo
wraps the existing Bailey-de Prado CSCV implementation with
edge-case handling (N<2, T<8, NaN → None).

All pure; no I/O. The orchestrator (next task) feeds
walk-forward summaries through these helpers post-hoc.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Sweep runner + repo extensions

**Files:**
- Modify: `backend/algo/backtest/sweep.py` (append `run_sweep_job`)
- Modify: `backend/algo/backtest/runs_repo.py`
- Create: `backend/algo/backtest/tests/test_sweep_runner.py`

- [ ] **Step 5.1: Extend the repo**

Edit `backend/algo/backtest/runs_repo.py`. Find the `create_pending` method (around line 28) and add a new `parent_sweep_id` kwarg + a new `create_pending_sweep` helper + a new `list_children_of_sweep` method.

Insert AFTER the existing `create_pending` method (around line 68, before `mark_running`):

```python
    async def create_pending_sweep(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        base_strategy_id: UUID,
        period_start: date,
        period_end: date,
    ) -> BacktestRun:
        """Create the sweep parent row (mode='sweep').

        Strategy FK points at the BASE strategy. Variants
        are tracked by parent_sweep_id on their child
        walkforward rows.
        """
        run_id = uuid4()
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.runs ("
                "id, strategy_id, user_id, mode, status, "
                "period_start, period_end, started_at"
                ") VALUES ("
                ":id, :sid, :uid, 'sweep', 'pending', "
                ":ps, :pe, :sa)"
            ),
            {
                "id": run_id, "sid": base_strategy_id,
                "uid": user_id,
                "ps": period_start, "pe": period_end,
                "sa": now,
            },
        )
        return BacktestRun(
            run_id=run_id,
            strategy_id=base_strategy_id,
            status="pending",
            period_start=period_start,
            period_end=period_end,
            started_at=now,
        )

    async def list_children_of_sweep(
        self,
        session: AsyncSession,
        *,
        sweep_run_id: UUID,
    ) -> list[dict[str, Any]]:
        """Variant walkforward rows owned by a sweep."""
        result = await session.execute(
            text(
                "SELECT id, status, summary_json, "
                "error_text "
                "FROM algo.runs "
                "WHERE parent_sweep_id = :sid "
                "  AND mode = 'walkforward' "
                "ORDER BY started_at"
            ),
            {"sid": sweep_run_id},
        )
        return [dict(r) for r in result.mappings().all()]
```

Then find the existing `create_pending` method body and add `parent_sweep_id: UUID | None = None` to its kwargs + include it in the INSERT. Locate the signature (line 28-40):

Replace:

```python
    async def create_pending(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        strategy_id: UUID,
        period_start: date,
        period_end: date,
        mode: str = "backtest",
        parent_walkforward_id: UUID | None = None,
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> BacktestRun:
```

with:

```python
    async def create_pending(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        strategy_id: UUID,
        period_start: date,
        period_end: date,
        mode: str = "backtest",
        parent_walkforward_id: UUID | None = None,
        parent_sweep_id: UUID | None = None,
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> BacktestRun:
```

Replace the INSERT SQL block to include the new column:

```python
        await session.execute(
            text(
                "INSERT INTO algo.runs ("
                "id, strategy_id, user_id, mode, status, "
                "period_start, period_end, started_at, "
                "parent_walkforward_id, parent_sweep_id, "
                "window_start, window_end"
                ") VALUES ("
                ":id, :sid, :uid, :mode, 'pending', "
                ":ps, :pe, :sa, "
                ":pwf_id, :psw_id, :ws, :we)"
            ),
            {
                "id": run_id, "sid": strategy_id, "uid": user_id,
                "mode": mode,
                "ps": period_start, "pe": period_end, "sa": now,
                "pwf_id": parent_walkforward_id,
                "psw_id": parent_sweep_id,
                "ws": window_start,
                "we": window_end,
            },
        )
```

- [ ] **Step 5.2: Write failing test for the orchestrator**

Create `backend/algo/backtest/tests/test_sweep_runner.py`:

```python
"""Integration tests for run_sweep_job."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.backtest.sweep import run_sweep_job
from backend.algo.backtest.sweep_types import SweepConfig


def _build_fake_walkforward_result(
    sharpe: float, pnl_pct: float, ok: bool = True,
):
    """Fake summary_json a finished walkforward row holds."""
    return {
        "status": "completed" if ok else "failed",
        "avg_pnl_pct": str(pnl_pct),
        "avg_win_rate_pct": "60.0",
        "avg_max_drawdown_pct": "5.0",
        "dsr": str(sharpe),
        "window_summaries": [],  # PBO aggregator gets
                                 # empty; that's fine
                                 # for this assertion.
    }


@pytest.mark.asyncio
async def test_sweep_runs_serial_and_aggregates(
    monkeypatch,
):
    """Orchestrator calls run_walkforward_job N times in
    order and writes a SweepResult to the sweep parent
    row's summary_json."""
    sweep_id = uuid4()

    # Mock the walkforward runner so we don't actually
    # backtest.
    wf_calls: list[dict] = []

    async def fake_wf(
        *, walkforward_run_id, user_id, config,
        strategy, universe,
    ):
        wf_calls.append(
            {"wf_id": walkforward_run_id, "cd": (
                strategy.risk.per_trade
                .cooldown_after_failed_exit_days
            )},
        )

    monkeypatch.setattr(
        "backend.algo.backtest.sweep.run_walkforward_job",
        fake_wf,
    )

    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 11, 23),
        period_end=date(2026, 5, 23),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14],
    )

    fake_strategy = MagicMock()
    fake_strategy.risk.per_trade \
        .cooldown_after_failed_exit_days = 7

    # We mock the repo + session_factory so no PG touch.
    fake_session = AsyncMock()
    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(
        return_value=fake_session,
    )
    fake_factory.return_value.__aexit__ = AsyncMock(
        return_value=None,
    )

    with patch(
        "backend.algo.backtest.sweep._session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.backtest.sweep.BacktestRunsRepo",
    ) as RepoCls:
        repo = RepoCls.return_value
        repo.create_pending = AsyncMock(
            return_value=MagicMock(run_id=uuid4()),
        )
        repo.mark_running = AsyncMock()
        repo.mark_completed = AsyncMock()
        repo.list_children_of_sweep = AsyncMock(
            return_value=[],
        )
        await run_sweep_job(
            sweep_run_id=sweep_id,
            user_id=uuid4(),
            config=cfg,
            base_strategy=fake_strategy,
            universe=["TICKER1.NS"],
        )

    # 3 variants → 3 walkforward calls
    assert len(wf_calls) == 3
    # In order: 3, 7, 14
    assert [c["cd"] for c in wf_calls] == [3, 7, 14]


@pytest.mark.asyncio
async def test_sweep_continues_when_one_variant_fails(
    monkeypatch,
):
    """Variant 2 raises; sweep should record failure and
    still attempt variants 3 + 4."""
    call_count = {"n": 0}

    async def flaky_wf(
        *, walkforward_run_id, user_id, config,
        strategy, universe,
    ):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("synthetic engine crash")

    monkeypatch.setattr(
        "backend.algo.backtest.sweep.run_walkforward_job",
        flaky_wf,
    )

    cfg = SweepConfig(
        base_strategy_id=uuid4(),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 1),
        swept_field="cooldown_days",
        swept_values=[3, 7, 14, 21],
    )
    fake_strategy = MagicMock()
    fake_strategy.risk.per_trade \
        .cooldown_after_failed_exit_days = 7

    fake_session = AsyncMock()
    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(
        return_value=fake_session,
    )
    fake_factory.return_value.__aexit__ = AsyncMock(
        return_value=None,
    )

    with patch(
        "backend.algo.backtest.sweep._session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.backtest.sweep.BacktestRunsRepo",
    ) as RepoCls:
        repo = RepoCls.return_value
        repo.create_pending = AsyncMock(
            return_value=MagicMock(run_id=uuid4()),
        )
        repo.mark_running = AsyncMock()
        repo.mark_completed = AsyncMock()
        repo.mark_failed = AsyncMock()
        repo.list_children_of_sweep = AsyncMock(
            return_value=[],
        )
        await run_sweep_job(
            sweep_run_id=uuid4(),
            user_id=uuid4(),
            config=cfg,
            base_strategy=fake_strategy,
            universe=["TICKER1.NS"],
        )

    # All 4 variants ATTEMPTED (failure was caught)
    assert call_count["n"] == 4
    # Variant 2's walkforward row marked failed
    assert repo.mark_failed.await_count >= 1
```

- [ ] **Step 5.3: Run tests — expect failures (no `run_sweep_job` yet)**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_runner.py -v
```

Expected: `AttributeError` (no `run_sweep_job` in `sweep.py`).

- [ ] **Step 5.4: Append `run_sweep_job` to `backend/algo/backtest/sweep.py`**

Add to the existing `sweep.py` (after `_mutate_ast`):

```python
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.sweep_pbo import (
    build_returns_matrix,
    compute_sweep_pbo,
    variant_equity_curve,
)
from backend.algo.backtest.sweep_types import (
    SweepConfig,
    SweepResult,
    SweepVariantSummary,
)
from backend.algo.backtest.sweep_whitelist import (
    SWEEPABLE_FIELDS,
)
from backend.algo.backtest.walkforward import (
    run_walkforward_job,
)


def _session_factory():
    """Lazy import to avoid circular dependency."""
    from backend.algo.backtest.job import (
        _session_factory as f,
    )
    return f


async def run_sweep_job(
    *,
    sweep_run_id: UUID,
    user_id: UUID,
    config: SweepConfig,
    base_strategy: Any,
    universe: list[str],
) -> None:
    """Serial sweep orchestrator. NEVER raises.

    For each value V in config.swept_values:
      1. Deep-copy base_strategy AST + mutate field.
      2. Create child walkforward row with
         parent_sweep_id=sweep_run_id.
      3. Call run_walkforward_job(...) and await.
      4. Record variant summary.

    After all variants:
      5. Pull each variant's equity curve.
      6. Chain → align → (T, N) returns matrix.
      7. Compute cross_variant_pbo.
      8. Rank by per-variant Sharpe.
      9. Write SweepResult to sweep parent's summary_json.
     10. Mark sweep parent 'completed' (or 'failed' if
         < 2 variants survived).
    """
    field_meta = SWEEPABLE_FIELDS.get(config.swept_field)
    if field_meta is None:
        # Should never happen — routes validate first.
        _logger.error(
            "run_sweep_job: unknown swept_field %r",
            config.swept_field,
        )
        return

    factory_fn = _session_factory()
    repo = BacktestRunsRepo()

    # Mark sweep parent running
    async with factory_fn() as session:
        await repo.mark_running(
            session, run_id=sweep_run_id,
        )
        await session.commit()

    variant_walkforward_ids: list[UUID] = []
    variant_outcomes: list[
        tuple[int, Any, UUID, str, str | None]
    ] = []

    from backend.algo.backtest.types import (
        WalkForwardConfig,
    )

    for idx, value in enumerate(config.swept_values):
        mutated = _mutate_ast(
            base_strategy, field_meta.path, value,
        )

        # Create child walkforward row
        async with factory_fn() as session:
            child = await repo.create_pending(
                session,
                user_id=user_id,
                strategy_id=config.base_strategy_id,
                period_start=config.period_start,
                period_end=config.period_end,
                mode="walkforward",
                parent_sweep_id=sweep_run_id,
            )
            await session.commit()

        variant_walkforward_ids.append(child.run_id)

        wf_config = WalkForwardConfig(
            strategy_id=config.base_strategy_id,
            period_start=config.period_start,
            period_end=config.period_end,
            train_days=config.train_days,
            test_days=config.test_days,
            step_days=config.step_days,
            initial_capital_inr=config.initial_capital_inr,
            regime_stratified=config.regime_stratified,
            interval_sec=config.interval_sec,
        )

        try:
            await run_walkforward_job(
                walkforward_run_id=child.run_id,
                user_id=user_id,
                config=wf_config,
                strategy=mutated,
                universe=universe,
            )
            variant_outcomes.append(
                (idx, value, child.run_id,
                 "completed", None),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "sweep variant %d (value=%s) crashed: %s",
                idx, value, exc, exc_info=True,
            )
            async with factory_fn() as session:
                await repo.mark_failed(
                    session,
                    run_id=child.run_id,
                    error_text=str(exc)[:500],
                )
                await session.commit()
            variant_outcomes.append(
                (idx, value, child.run_id, "failed",
                 str(exc)[:500]),
            )

    # Aggregate variants
    survived = [
        o for o in variant_outcomes
        if o[3] == "completed"
    ]
    if len(survived) < 2:
        async with factory_fn() as session:
            await repo.mark_failed(
                session,
                run_id=sweep_run_id,
                error_text=(
                    "Need ≥ 2 completed variants for "
                    "PBO; only "
                    f"{len(survived)} survived"
                ),
            )
            await session.commit()
        return

    # Pull each variant's summary_json + build matrix
    async with factory_fn() as session:
        children_rows = await repo.list_children_of_sweep(
            session, sweep_run_id=sweep_run_id,
        )

    # Map walkforward_run_id -> WalkForwardResult-like dict
    wf_by_id = {r["id"]: r for r in children_rows}

    variant_summaries: list[SweepVariantSummary] = []
    variant_curves: list[
        list[tuple[Any, Any]]
    ] = []

    for idx, value, wf_id, status, err in variant_outcomes:
        row = wf_by_id.get(wf_id, {})
        sj = row.get("summary_json") or {}
        if status != "completed" or not sj:
            variant_summaries.append(
                SweepVariantSummary(
                    variant_index=idx,
                    swept_value=value,
                    walkforward_run_id=wf_id,
                    avg_pnl_pct=__import__(
                        "decimal",
                    ).Decimal("0"),
                    avg_win_rate_pct=__import__(
                        "decimal",
                    ).Decimal("0"),
                    avg_max_drawdown_pct=__import__(
                        "decimal",
                    ).Decimal("0"),
                    sharpe=__import__(
                        "decimal",
                    ).Decimal("0"),
                    dsr=__import__(
                        "decimal",
                    ).Decimal("0"),
                    n_trades=0,
                    status=status,
                    error_text=err,
                ),
            )
            continue

        # Convert window_summaries back to BacktestSummary
        from backend.algo.backtest.types import (
            BacktestSummary,
        )
        ws_raw = sj.get("window_summaries", [])
        ws = [
            BacktestSummary.model_validate(w)
            for w in ws_raw
        ]

        curve = variant_equity_curve(
            ws, config.initial_capital_inr,
        )
        variant_curves.append(curve)

        # Compute per-variant Sharpe (annualised) from
        # the curve we just built.
        import numpy as np
        eq = np.array(
            [float(v) for _, v in curve], dtype=float,
        )
        if eq.size >= 2:
            rets = np.diff(eq) / eq[:-1]
            rets = np.where(
                np.isfinite(rets), rets, 0.0,
            )
            mu = rets.mean()
            sigma = rets.std(ddof=0)
            sharpe = (
                float((mu / sigma) * (252 ** 0.5))
                if sigma > 1e-12 else 0.0
            )
        else:
            sharpe = 0.0

        from decimal import Decimal
        variant_summaries.append(
            SweepVariantSummary(
                variant_index=idx,
                swept_value=value,
                walkforward_run_id=wf_id,
                avg_pnl_pct=Decimal(
                    str(sj.get("avg_pnl_pct", "0")),
                ),
                avg_win_rate_pct=Decimal(
                    str(sj.get(
                        "avg_win_rate_pct", "0",
                    )),
                ),
                avg_max_drawdown_pct=Decimal(
                    str(sj.get(
                        "avg_max_drawdown_pct", "0",
                    )),
                ),
                sharpe=Decimal(str(round(sharpe, 3))),
                dsr=Decimal(
                    str(sj.get("dsr", "0")),
                ),
                n_trades=int(sum(
                    w.total_trades for w in ws
                )),
                status="completed",
                error_text=None,
            ),
        )

    # Compute cross-variant PBO
    R, _ = build_returns_matrix(variant_curves)
    pbo = compute_sweep_pbo(R)

    # Winner by Sharpe
    completed_summaries = [
        s for s in variant_summaries
        if s.status == "completed"
    ]
    if completed_summaries:
        winner = max(
            completed_summaries,
            key=lambda s: s.sharpe,
        )
        winner_idx = winner.variant_index
    else:
        winner_idx = None

    sweep_result = SweepResult(
        run_id=sweep_run_id,
        base_strategy_id=config.base_strategy_id,
        swept_field=config.swept_field,
        swept_values=list(config.swept_values),
        variants=variant_summaries,
        cross_variant_pbo=pbo,
        returns_matrix_shape=(
            int(R.shape[0]),
            int(R.shape[1]),
        ),
        winner_variant_index=winner_idx,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        status="completed",
    )

    # Persist via mark_completed-style update
    async with factory_fn() as session:
        await session.execute(
            __import__(
                "sqlalchemy",
            ).text(
                "UPDATE algo.runs SET status='completed', "
                "completed_at=:ca, summary_json=:sj "
                "WHERE id = :id",
            ),
            {
                "id": sweep_run_id,
                "ca": sweep_result.completed_at,
                "sj": sweep_result.model_dump_json(),
            },
        )
        await session.commit()
```

- [ ] **Step 5.5: Run tests — expect 2 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/test_sweep_runner.py -v
```

Expected: 2 passed.

- [ ] **Step 5.6: Run the broader test sweep for regressions**

```bash
docker compose exec backend python -m pytest \
  backend/algo/backtest/tests/ -v
```

Expected: all green (no regressions on existing tests).

- [ ] **Step 5.7: Restart backend**

```bash
docker compose restart backend
sleep 5
```

(Routes and runner imports change; CLAUDE.md §6.2.)

- [ ] **Step 5.8: Commit**

```bash
git add backend/algo/backtest/sweep.py \
        backend/algo/backtest/runs_repo.py \
        backend/algo/backtest/tests/test_sweep_runner.py
git commit -m "$(cat <<'EOF'
feat(sweep): serial run_sweep_job orchestrator + repo

For each value in config.swept_values:
  - Deep-copy base strategy + mutate field
  - Create child walkforward row (parent_sweep_id=…)
  - Run walk-forward via existing run_walkforward_job

After all variants: chain per-window equity curves into
continuous variant curves, build (T, N) returns matrix,
compute cross-variant PBO, rank by Sharpe, write
SweepResult to the sweep parent's summary_json.

Fail-soft: if a variant crashes, mark its walkforward
row failed and continue. Sweep completes if ≥ 2 variants
survived; otherwise marks itself failed.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: HTTP routes

**Files:**
- Create: `backend/algo/routes/sweep.py`
- Modify: `backend/algo/routes/__init__.py` (mount the router)
- Create: `backend/algo/tests/test_sweep_routes.py`

- [ ] **Step 6.1: Write failing route tests**

Create `backend/algo/tests/test_sweep_routes.py`:

```python
"""HTTP-level tests for /v1/algo/sweep/* endpoints."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# We test the route handlers directly via lifted _impl
# functions; no full HTTP harness needed.
from backend.algo.routes.sweep import (
    _sweep_fields_impl,
    _sweep_start_impl,
)


def test_sweep_fields_returns_whitelist():
    out = _sweep_fields_impl()
    assert "fields" in out
    keys = {f["key"] for f in out["fields"]}
    assert "cooldown_days" in keys
    assert "stop_loss_pct" in keys
    # Each row has label/type/min/max
    for f in out["fields"]:
        assert "key" in f
        assert "label" in f
        assert "field_type" in f
        assert "min_value" in f
        assert "max_value" in f


@pytest.mark.asyncio
async def test_sweep_start_validates_whitelist_field():
    """Unknown field → 400."""
    from fastapi import HTTPException
    from backend.algo.backtest.sweep_types import (
        SweepConfig,
    )

    cfg_dict = {
        "base_strategy_id": str(uuid4()),
        "period_start": "2025-11-23",
        "period_end": "2026-05-23",
        "swept_field": "bogus_field",
        "swept_values": [1, 2, 3],
    }
    body = SweepConfig.model_validate(cfg_dict)

    user_id = uuid4()
    with pytest.raises(HTTPException) as exc:
        await _sweep_start_impl(
            body=body, user_id=user_id,
            background_tasks=MagicMock(),
        )
    assert exc.value.status_code == 400
    assert "unknown field" in str(
        exc.value.detail,
    ).lower()


@pytest.mark.asyncio
async def test_sweep_start_rejects_single_value():
    from fastapi import HTTPException
    from backend.algo.backtest.sweep_types import (
        SweepConfig,
    )
    body = SweepConfig.model_validate({
        "base_strategy_id": str(uuid4()),
        "period_start": "2025-11-23",
        "period_end": "2026-05-23",
        "swept_field": "cooldown_days",
        "swept_values": [7],
    })
    with pytest.raises(HTTPException) as exc:
        await _sweep_start_impl(
            body=body, user_id=uuid4(),
            background_tasks=MagicMock(),
        )
    assert exc.value.status_code == 400
    assert "at least 2" in str(exc.value.detail).lower()
```

- [ ] **Step 6.2: Run test — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/tests/test_sweep_routes.py -v
```

Expected: `ImportError` on `backend.algo.routes.sweep`.

- [ ] **Step 6.3: Implement the routes module**

Create `backend/algo/routes/sweep.py`:

```python
"""POST /v1/algo/sweep/run + GET endpoints.

Routes follow the lift-to-module-level pattern used by
the admin backup endpoints in PR #239: route handlers
delegate to pure ``_impl`` functions that are unit-
testable without an HTTP harness.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException,
)
from sqlalchemy import text

from auth.models import UserContext
from auth.deps import pro_or_superuser
from backend.algo.backtest.runs_repo import (
    BacktestRunsRepo,
)
from backend.algo.backtest.sweep import run_sweep_job
from backend.algo.backtest.sweep_types import (
    SweepConfig, SweepResult,
)
from backend.algo.backtest.sweep_whitelist import (
    SWEEPABLE_FIELDS, validate_swept_values,
)
from backend.algo.backtest.universe import (
    resolve_universe,
)
from backend.algo.strategy.repo import get_strategy
from db.engine import get_session_factory

_logger = logging.getLogger(__name__)


def _sweep_fields_impl() -> dict:
    """Return the whitelist for the form dropdown."""
    return {
        "fields": [
            {
                "key": key,
                "label": f.label,
                "field_type": f.field_type,
                "min_value": str(f.min_value),
                "max_value": str(f.max_value),
            }
            for key, f in SWEEPABLE_FIELDS.items()
        ],
    }


async def _sweep_start_impl(
    *,
    body: SweepConfig,
    user_id: UUID,
    background_tasks: BackgroundTasks,
) -> dict:
    """POST /v1/algo/sweep/run handler body.

    Validates the whitelist field + values, loads the
    base strategy, resolves universe, creates the sweep
    parent row, schedules the runner as a background
    task. Returns sweep_run_id immediately.
    """
    # Whitelist validation
    try:
        coerced = validate_swept_values(
            body.swept_field, body.swept_values,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=str(exc),
        ) from exc

    factory = get_session_factory()
    async with factory() as session:
        strategy = await get_strategy(
            session, user_id, body.base_strategy_id,
        )
    if strategy is None:
        raise HTTPException(
            status_code=404,
            detail="Base strategy not found",
        )

    uc = UserContext(
        user_id=str(user_id), email="", role="pro",
    )
    universe = await resolve_universe(
        user=uc, strategy=strategy,
    )

    # Create the sweep parent row
    repo = BacktestRunsRepo()
    async with factory() as session:
        row = await repo.create_pending_sweep(
            session,
            user_id=user_id,
            base_strategy_id=body.base_strategy_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
        await session.commit()

    # Re-build SweepConfig with coerced values
    body_coerced = body.model_copy(
        update={"swept_values": coerced},
    )

    background_tasks.add_task(
        run_sweep_job,
        sweep_run_id=row.run_id,
        user_id=user_id,
        config=body_coerced,
        base_strategy=strategy,
        universe=universe,
    )
    return {
        "sweep_run_id": str(row.run_id),
        "status": "pending",
    }


async def _sweep_get_impl(
    *, run_id: UUID, user_id: UUID,
) -> dict:
    """GET /v1/algo/sweep/runs/{id} handler body."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT id, status, period_start, "
                "period_end, started_at, completed_at, "
                "summary_json, error_text "
                "FROM algo.runs "
                "WHERE id = :id AND user_id = :uid "
                "  AND mode = 'sweep'"
            ),
            {"id": run_id, "uid": user_id},
        )
        row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Sweep run not found",
        )

    sj = row["summary_json"]
    if sj is not None:
        # Completed: parse SweepResult and return
        result_obj = SweepResult.model_validate(sj)
        return result_obj.model_dump(mode="json")

    # Pending / running — minimal shape
    return {
        "run_id": str(row["id"]),
        "status": row["status"],
        "period_start": row["period_start"].isoformat(),
        "period_end": row["period_end"].isoformat(),
        "started_at": (
            row["started_at"].isoformat()
            if row["started_at"] else None
        ),
        "completed_at": None,
        "variants": [],
        "cross_variant_pbo": None,
        "returns_matrix_shape": [0, 0],
        "winner_variant_index": None,
        "error_text": row["error_text"],
    }


async def _sweep_list_impl(*, user_id: UUID) -> dict:
    """GET /v1/algo/sweep/runs handler body."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT id, strategy_id, status, "
                "started_at, completed_at "
                "FROM algo.runs "
                "WHERE user_id = :uid "
                "  AND mode = 'sweep' "
                "ORDER BY started_at DESC LIMIT 100"
            ),
            {"uid": user_id},
        )
        rows = result.mappings().all()
    return {
        "sweeps": [
            {
                "run_id": str(r["id"]),
                "base_strategy_id": str(r["strategy_id"]),
                "status": r["status"],
                "started_at": (
                    r["started_at"].isoformat()
                    if r["started_at"] else None
                ),
                "completed_at": (
                    r["completed_at"].isoformat()
                    if r["completed_at"] else None
                ),
            }
            for r in rows
        ],
    }


def create_sweep_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/sweep", tags=["algo-trading"],
    )

    @router.get("/fields")
    async def sweep_fields(
        user: UserContext = Depends(pro_or_superuser),
    ):
        return _sweep_fields_impl()

    @router.post("/run", status_code=202)
    async def sweep_start(
        body: SweepConfig,
        background_tasks: BackgroundTasks,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _sweep_start_impl(
            body=body,
            user_id=UUID(user.user_id),
            background_tasks=background_tasks,
        )

    @router.get("/runs/{run_id}")
    async def sweep_get(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _sweep_get_impl(
            run_id=run_id,
            user_id=UUID(user.user_id),
        )

    @router.get("/runs")
    async def sweep_list(
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _sweep_list_impl(
            user_id=UUID(user.user_id),
        )

    return router
```

- [ ] **Step 6.4: Mount the router**

Edit `backend/algo/routes/__init__.py`. Find the existing imports (the file mounts all algo routers). Add the sweep router alongside walkforward. The exact line depends on the file's current structure; the pattern matches:

```python
from backend.algo.routes.sweep import create_sweep_router
# … existing imports …
router.include_router(create_sweep_router())
```

- [ ] **Step 6.5: Run tests — expect 3 PASS**

```bash
docker compose restart backend && sleep 5
docker compose exec backend python -m pytest \
  backend/algo/tests/test_sweep_routes.py -v
```

Expected: 3 passed.

- [ ] **Step 6.6: Smoke-test the routes via curl**

```bash
# Get fields (no auth needed in dev mode? or pass token)
# If your dev runs with auth, get a token first:
TOKEN=$(docker compose exec -T backend python -c \
  "from auth.token_store import mint_dev_token; \
   print(mint_dev_token('superuser'))")

curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8181/v1/algo/sweep/fields | jq .
```

Expected: JSON with 7 fields, each with key/label/type/min/max.

- [ ] **Step 6.7: Commit**

```bash
git add backend/algo/routes/sweep.py \
        backend/algo/routes/__init__.py \
        backend/algo/tests/test_sweep_routes.py
git commit -m "$(cat <<'EOF'
feat(sweep): POST /run + GET /runs + /fields endpoints

Four HTTP routes following the lift-to-module-level pattern
from PR #239 — handlers delegate to pure _impl functions
that unit-test without an HTTP harness. Whitelist
validation happens in the start endpoint; bad input returns
400 with a clear message before any PG write.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Frontend types + hooks + sub-tab scaffolding

**Files:**
- Create: `frontend/lib/types/algoSweep.ts`
- Create: `frontend/hooks/useSweepRuns.ts`
- Create: `frontend/hooks/useSweepableFields.ts`
- Create: `frontend/components/algo-trading/SweepSubTab.tsx`
- Modify: `frontend/components/algo-trading/StrategiesTab.tsx` (or whichever component renders the Backtest sub-tab strip)
- Create: `frontend/components/algo-trading/__tests__/useSweepRuns.test.ts`

- [ ] **Step 7.1: Find the Backtest sub-tab strip**

```bash
grep -n "Walk-forward CV\|Single run" \
  frontend/components/algo-trading/StrategiesTab.tsx \
  frontend/components/algo-trading/BacktestTab.tsx \
  2>/dev/null
```

Identify the file + lines that render the sub-tab nav. That's where the new "Parameter sweep" tab item goes.

- [ ] **Step 7.2: Create TypeScript shapes**

Create `frontend/lib/types/algoSweep.ts`:

```typescript
// Mirrors backend/algo/backtest/sweep_types.py.

export interface SweepableField {
  key: string;
  label: string;
  field_type: "int" | "decimal";
  min_value: string;  // as string from JSON to preserve precision
  max_value: string;
}

export type SweepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface SweepVariantSummary {
  variant_index: number;
  swept_value: number | string;
  walkforward_run_id: string;
  avg_pnl_pct: string;
  avg_win_rate_pct: string;
  avg_max_drawdown_pct: string;
  sharpe: string;
  dsr: string;
  n_trades: number;
  status: "completed" | "failed" | "skipped";
  error_text: string | null;
}

export interface SweepResult {
  run_id: string;
  base_strategy_id: string;
  swept_field: string;
  swept_values: (number | string)[];
  variants: SweepVariantSummary[];
  cross_variant_pbo: string | null;
  returns_matrix_shape: [number, number];
  winner_variant_index: number | null;
  started_at: string;
  completed_at: string | null;
  status: SweepStatus;
  error_text?: string | null;
}

export interface SweepConfig {
  base_strategy_id: string;
  period_start: string;
  period_end: string;
  train_days?: number;
  test_days?: number;
  step_days?: number;
  initial_capital_inr?: string;
  regime_stratified?: boolean;
  swept_field: string;
  swept_values: (number | string)[];
  interval_sec?: number;
}

export interface SweepRow {
  run_id: string;
  base_strategy_id: string;
  status: SweepStatus;
  started_at: string | null;
  completed_at: string | null;
}
```

- [ ] **Step 7.3: Create SWR hooks**

Create `frontend/hooks/useSweepRuns.ts`:

```typescript
"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  SweepConfig, SweepResult, SweepRow,
} from "@/lib/types/algoSweep";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useSweepRuns() {
  const { data, error, isLoading, mutate } = useSWR<
    { sweeps: SweepRow[] }
  >(
    `${API_URL}/algo/sweep/runs`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 120_000 },
  );
  return {
    runs: data?.sweeps ?? [],
    isLoading,
    error,
    mutate,
  };
}

export function useSweepRun(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<
    SweepResult
  >(
    runId
      ? `${API_URL}/algo/sweep/runs/${runId}`
      : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) => {
        if (!latest) return 3_000;
        if (
          latest.status === "completed"
          || latest.status === "failed"
        ) return 0;
        return 3_000;
      },
    },
  );
  return { run: data, isLoading, error, mutate };
}

export async function startSweepRun(
  config: SweepConfig,
): Promise<{ sweep_run_id: string }> {
  const r = await apiFetch(
    `${API_URL}/algo/sweep/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    },
  );
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Sweep start failed: ${body}`);
  }
  return r.json();
}
```

Create `frontend/hooks/useSweepableFields.ts`:

```typescript
"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { SweepableField } from "@/lib/types/algoSweep";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useSweepableFields() {
  const { data, error, isLoading } = useSWR<
    { fields: SweepableField[] }
  >(
    `${API_URL}/algo/sweep/fields`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );
  return {
    fields: data?.fields ?? [],
    isLoading,
    error,
  };
}
```

- [ ] **Step 7.4: Create the SweepSubTab shell**

Create `frontend/components/algo-trading/SweepSubTab.tsx`:

```typescript
"use client";

import { useState } from "react";
import { useSweepRun } from "@/hooks/useSweepRuns";

import { SweepForm } from "./SweepForm";
import { SweepProgressPanel } from "./SweepProgressPanel";
import { SweepResultsTable } from "./SweepResultsTable";
import { SweepPboBadge } from "./SweepPboBadge";
import { SweepEquityCurves } from "./SweepEquityCurves";

export function SweepSubTab() {
  const [activeSweepId, setActiveSweepId] = useState<
    string | null
  >(null);
  const { run } = useSweepRun(activeSweepId);

  const isDone =
    run?.status === "completed"
    || run?.status === "failed";

  return (
    <div className="space-y-4" data-testid="sweep-sub-tab">
      {(activeSweepId == null || isDone) && (
        <SweepForm onStarted={setActiveSweepId} />
      )}
      {activeSweepId && !isDone && (
        <SweepProgressPanel sweepRunId={activeSweepId} />
      )}
      {run && run.status === "completed" && (
        <>
          <SweepResultsTable run={run} />
          <SweepPboBadge run={run} />
          <SweepEquityCurves run={run} />
        </>
      )}
    </div>
  );
}
```

(The four component imports will be created in subsequent tasks. We're scaffolding the parent.)

- [ ] **Step 7.5: Mount the sub-tab**

Edit the Backtest sub-tab strip (location identified in Step 7.1) to add a "Parameter sweep" tab item that renders `<SweepSubTab />` when active. Match the existing sub-tab pattern (probably useState + Tailwind underline).

- [ ] **Step 7.6: Write hook smoke test**

Create `frontend/components/algo-trading/__tests__/useSweepRuns.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { startSweepRun } from "@/hooks/useSweepRuns";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      sweep_run_id: "abc-123",
    }),
  }),
}));

describe("startSweepRun", () => {
  it("POSTs config and returns sweep_run_id", async () => {
    const out = await startSweepRun({
      base_strategy_id: "stub",
      period_start: "2025-01-01",
      period_end: "2025-06-01",
      swept_field: "cooldown_days",
      swept_values: [3, 7],
    });
    expect(out.sweep_run_id).toBe("abc-123");
  });
});
```

- [ ] **Step 7.7: Run test**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx vitest run \
  components/algo-trading/__tests__/useSweepRuns.test.ts
```

Expected: 1 passed.

- [ ] **Step 7.8: Commit**

```bash
git add frontend/lib/types/algoSweep.ts \
        frontend/hooks/useSweepRuns.ts \
        frontend/hooks/useSweepableFields.ts \
        frontend/components/algo-trading/SweepSubTab.tsx \
        frontend/components/algo-trading/StrategiesTab.tsx \
        frontend/components/algo-trading/__tests__/useSweepRuns.test.ts
git commit -m "$(cat <<'EOF'
feat(sweep-ui): types, SWR hooks, sub-tab scaffolding

TS shapes mirroring backend Pydantic. Two SWR hooks
(useSweepRuns + useSweepableFields) plus startSweepRun
POST helper. SweepSubTab is the parent shell that swaps
between Form / Progress / Results views based on the
active sweep's status (the four child components arrive
in follow-up tasks; their imports are placeholders).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: SweepForm + SweepProgressPanel

**Files:**
- Create: `frontend/components/algo-trading/SweepForm.tsx`
- Create: `frontend/components/algo-trading/SweepProgressPanel.tsx`
- Create: `frontend/components/algo-trading/__tests__/SweepForm.test.tsx`

- [ ] **Step 8.1: Write failing form test**

Create `frontend/components/algo-trading/__tests__/SweepForm.test.tsx`:

```typescript
import {
  render, screen, waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  describe, it, expect, vi, beforeEach,
} from "vitest";
import { SweepForm } from "../SweepForm";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/hooks/useSweepableFields", () => ({
  useSweepableFields: () => ({
    fields: [
      {
        key: "cooldown_days",
        label: "Cooldown (days)",
        field_type: "int",
        min_value: "0",
        max_value: "60",
      },
      {
        key: "stop_loss_pct",
        label: "Stop loss %",
        field_type: "decimal",
        min_value: "0.5",
        max_value: "20.0",
      },
    ],
    isLoading: false,
    error: null,
  }),
}));

vi.mock("@/hooks/useStrategies", () => ({
  useStrategies: () => ({
    strategies: [
      { id: "strat-1", name: "RSI(2) v3" },
    ],
    isLoading: false,
  }),
}));

describe("SweepForm", () => {
  beforeEach(() => vi.resetAllMocks());

  it("renders field dropdown with whitelist", async () => {
    render(<SweepForm onStarted={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByText("Cooldown (days)"),
      ).toBeDefined();
    });
  });

  it("disables submit when fewer than 2 values", async () => {
    const user = userEvent.setup();
    render(<SweepForm onStarted={vi.fn()} />);
    const valuesInput = await screen.findByTestId(
      "sweep-values-input",
    );
    await user.type(valuesInput, "7");
    const btn = screen.getByTestId("sweep-submit");
    expect(btn.hasAttribute("disabled")).toBe(true);
  });

  it("enables submit when 2+ values entered", async () => {
    const user = userEvent.setup();
    render(<SweepForm onStarted={vi.fn()} />);
    const valuesInput = await screen.findByTestId(
      "sweep-values-input",
    );
    await user.type(valuesInput, "3, 7, 14");
    const btn = screen.getByTestId("sweep-submit");
    expect(btn.hasAttribute("disabled")).toBe(false);
  });
});
```

- [ ] **Step 8.2: Run test — expect fail (no component yet)**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx vitest run \
  components/algo-trading/__tests__/SweepForm.test.tsx
```

Expected: failure on missing module.

- [ ] **Step 8.3: Implement SweepForm**

Create `frontend/components/algo-trading/SweepForm.tsx`:

```typescript
"use client";

import { useMemo, useState } from "react";
import { useSweepableFields }
  from "@/hooks/useSweepableFields";
import { useStrategies } from "@/hooks/useStrategies";
import {
  startSweepRun,
} from "@/hooks/useSweepRuns";
import type {
  SweepConfig, SweepableField,
} from "@/lib/types/algoSweep";

interface Props {
  onStarted: (sweepRunId: string) => void;
}

function parseValues(
  raw: string, field: SweepableField | undefined,
): { values: (number | string)[]; error: string | null } {
  if (!field) return { values: [], error: null };
  const parts = raw.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return { values: [], error: null };
  }
  if (field.field_type === "int") {
    const out: number[] = [];
    for (const p of parts) {
      const n = Number(p);
      if (!Number.isInteger(n)) {
        return {
          values: [],
          error: `'${p}' is not a valid integer`,
        };
      }
      const lo = Number(field.min_value);
      const hi = Number(field.max_value);
      if (n < lo || n > hi) {
        return {
          values: [],
          error: `${n} is out of range [${lo}, ${hi}]`,
        };
      }
      out.push(n);
    }
    if (new Set(out).size !== out.length) {
      return {
        values: [],
        error: "Duplicate values not allowed",
      };
    }
    return { values: out, error: null };
  }
  // decimal
  const out: string[] = [];
  for (const p of parts) {
    const n = Number(p);
    if (Number.isNaN(n)) {
      return {
        values: [],
        error: `'${p}' is not a valid number`,
      };
    }
    const lo = Number(field.min_value);
    const hi = Number(field.max_value);
    if (n < lo || n > hi) {
      return {
        values: [],
        error: `${n} is out of range [${lo}, ${hi}]`,
      };
    }
    out.push(p);
  }
  if (new Set(out).size !== out.length) {
    return {
      values: [],
      error: "Duplicate values not allowed",
    };
  }
  return { values: out, error: null };
}

export function SweepForm({ onStarted }: Props) {
  const { fields } = useSweepableFields();
  const { strategies } = useStrategies();

  const [strategyId, setStrategyId] = useState<string>("");
  const [periodFrom, setPeriodFrom] = useState<string>(
    "2025-11-23",
  );
  const [periodTo, setPeriodTo] = useState<string>(
    "2026-05-23",
  );
  const [trainDays, setTrainDays] = useState(60);
  const [testDays, setTestDays] = useState(30);
  const [stepDays, setStepDays] = useState(30);
  const [capital, setCapital] = useState("100000");
  const [regimeStratified, setRegimeStratified] = useState(
    false,
  );
  const [fieldKey, setFieldKey] = useState<string>("");
  const [valuesRaw, setValuesRaw] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(
    null,
  );

  const field = useMemo(
    () => fields.find((f) => f.key === fieldKey),
    [fields, fieldKey],
  );
  const { values: parsedValues, error: parseErr } =
    useMemo(() => parseValues(valuesRaw, field), [
      valuesRaw, field,
    ]);

  const canSubmit =
    strategyId
    && fieldKey
    && parsedValues.length >= 2
    && parseErr == null
    && !submitting;

  const runtimeEstimate = useMemo(() => {
    if (parsedValues.length < 2) return "—";
    const totalDays =
      (new Date(periodTo).getTime()
       - new Date(periodFrom).getTime())
      / (1000 * 60 * 60 * 24);
    const windows = Math.max(
      1, Math.floor(totalDays / stepDays),
    );
    const secPerWindow = 30;
    const totalSec =
      parsedValues.length * windows * secPerWindow;
    const min = Math.round(totalSec / 60);
    return `~${min} min for ${parsedValues.length} variants`;
  }, [parsedValues, periodFrom, periodTo, stepDays]);

  async function handleSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setFormErr(null);
    try {
      const cfg: SweepConfig = {
        base_strategy_id: strategyId,
        period_start: periodFrom,
        period_end: periodTo,
        train_days: trainDays,
        test_days: testDays,
        step_days: stepDays,
        initial_capital_inr: capital,
        regime_stratified: regimeStratified,
        swept_field: fieldKey,
        swept_values: parsedValues,
      };
      const { sweep_run_id } = await startSweepRun(cfg);
      onStarted(sweep_run_id);
    } catch (exc) {
      setFormErr(
        exc instanceof Error
          ? exc.message
          : "Failed to start sweep",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-4 space-y-3"
      data-testid="sweep-form"
    >
      <h3 className="text-sm font-semibold">
        Parameter sweep
      </h3>

      <label className="flex flex-col gap-1 text-xs">
        <span>Base strategy</span>
        <select
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="sweep-base-strategy-select"
          className="rounded border px-2 py-1"
        >
          <option value="">— select —</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <div className="flex gap-2 flex-wrap">
        <label className="flex flex-col text-xs">
          <span>Period from</span>
          <input
            type="date"
            value={periodFrom}
            onChange={(e) => setPeriodFrom(e.target.value)}
            data-testid="sweep-period-from"
            className="rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Period to</span>
          <input
            type="date"
            value={periodTo}
            onChange={(e) => setPeriodTo(e.target.value)}
            data-testid="sweep-period-to"
            className="rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Train days</span>
          <input
            type="number"
            value={trainDays}
            onChange={(e) =>
              setTrainDays(Number(e.target.value))}
            data-testid="sweep-train-days"
            className="rounded border px-2 py-1 w-20"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Test days</span>
          <input
            type="number"
            value={testDays}
            onChange={(e) =>
              setTestDays(Number(e.target.value))}
            data-testid="sweep-test-days"
            className="rounded border px-2 py-1 w-20"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Step days</span>
          <input
            type="number"
            value={stepDays}
            onChange={(e) =>
              setStepDays(Number(e.target.value))}
            data-testid="sweep-step-days"
            className="rounded border px-2 py-1 w-20"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Capital ₹</span>
          <input
            type="number"
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
            className="rounded border px-2 py-1 w-28"
          />
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs mt-4">
          <input
            type="checkbox"
            checked={regimeStratified}
            onChange={(e) =>
              setRegimeStratified(e.target.checked)}
            data-testid="sweep-regime-stratified"
          />
          Regime-stratified
        </label>
      </div>

      <div className="border-t pt-3 space-y-2">
        <label className="flex flex-col gap-1 text-xs">
          <span>Sweep parameter</span>
          <select
            value={fieldKey}
            onChange={(e) => setFieldKey(e.target.value)}
            data-testid="sweep-field-select"
            className="rounded border px-2 py-1"
          >
            <option value="">— select —</option>
            {fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span>
            Values (comma-separated)
            {field && (
              <span className="ml-2 text-slate-400">
                ({field.field_type},
                {" "}{field.min_value}–{field.max_value})
              </span>
            )}
          </span>
          <input
            type="text"
            value={valuesRaw}
            onChange={(e) => setValuesRaw(e.target.value)}
            placeholder="3, 7, 14, 21, 28"
            data-testid="sweep-values-input"
            className="rounded border px-2 py-1"
          />
        </label>
        {parseErr && (
          <p className="text-xs text-rose-600">
            {parseErr}
          </p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={handleSubmit}
          data-testid="sweep-submit"
          className={
            "rounded bg-indigo-600 px-3 py-1.5 text-sm "
            + "font-medium text-white "
            + (canSubmit ? "" : "opacity-50 cursor-not-allowed")
          }
        >
          {submitting ? "Starting…" : "Run sweep"}
        </button>
        <span className="text-xs text-slate-500">
          Est. runtime: {runtimeEstimate}
        </span>
        {formErr && (
          <span className="text-xs text-rose-600">
            {formErr}
          </span>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 8.4: Implement SweepProgressPanel**

Create `frontend/components/algo-trading/SweepProgressPanel.tsx`:

```typescript
"use client";

import { useSweepRun } from "@/hooks/useSweepRuns";

interface Props {
  sweepRunId: string;
}

export function SweepProgressPanel({ sweepRunId }: Props) {
  const { run, isLoading } = useSweepRun(sweepRunId);

  if (isLoading || !run) {
    return (
      <div
        className="rounded-md border p-4 text-sm text-slate-500"
        data-testid="sweep-progress-panel"
      >
        Starting…
      </div>
    );
  }

  const total = run.swept_values.length;
  const completed = run.variants.filter(
    (v) => v.status === "completed",
  ).length;
  const failed = run.variants.filter(
    (v) => v.status === "failed",
  ).length;
  const inFlight = total - completed - failed;
  const pct = total > 0
    ? Math.round((completed / total) * 100)
    : 0;

  return (
    <div
      className="rounded-md border p-4 space-y-3"
      data-testid="sweep-progress-panel"
    >
      <div className="text-sm font-medium">
        Sweep in progress —
        {" "}{completed} of {total} variants complete
        {failed > 0 && (
          <span className="ml-1 text-rose-600">
            ({failed} failed)
          </span>
        )}
      </div>
      <div className="h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
        <div
          className="h-full bg-indigo-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ul className="text-xs space-y-1">
        {run.swept_values.map((v, i) => {
          const variant = run.variants[i];
          if (!variant) {
            return (
              <li key={i} className="text-slate-400">
                ⏸ value={String(v)} (queued)
              </li>
            );
          }
          if (variant.status === "completed") {
            return (
              <li
                key={i}
                className="text-emerald-700 dark:text-emerald-300"
                data-testid={`sweep-variant-row-${i}`}
              >
                ✅ value={String(v)}: PnL=
                {variant.avg_pnl_pct}% DD=
                {variant.avg_max_drawdown_pct}%
              </li>
            );
          }
          if (variant.status === "failed") {
            return (
              <li
                key={i}
                className="text-rose-700 dark:text-rose-300"
                data-testid={`sweep-variant-row-${i}`}
              >
                ❌ value={String(v)} ({
                  variant.error_text ?? "failed"
                })
              </li>
            );
          }
          return (
            <li
              key={i}
              className="text-indigo-600 dark:text-indigo-400"
              data-testid={`sweep-variant-row-${i}`}
            >
              ⏳ value={String(v)} (running)
            </li>
          );
        })}
      </ul>
    </div>
  );
}
```

- [ ] **Step 8.5: Run tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx vitest run \
  components/algo-trading/__tests__/SweepForm.test.tsx
```

Expected: 3 passed.

- [ ] **Step 8.6: Lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx eslint \
  components/algo-trading/SweepForm.tsx \
  components/algo-trading/SweepProgressPanel.tsx \
  --fix
```

- [ ] **Step 8.7: Commit**

```bash
git add frontend/components/algo-trading/SweepForm.tsx \
        frontend/components/algo-trading/SweepProgressPanel.tsx \
        frontend/components/algo-trading/__tests__/SweepForm.test.tsx
git commit -m "$(cat <<'EOF'
feat(sweep-ui): SweepForm + SweepProgressPanel

Form validates values client-side (whitelist-aware type +
range checks, duplicate detection), disables submit when
< 2 valid values, surfaces parse errors inline. Progress
panel polls /v1/algo/sweep/runs/{id} every 3 s and shows
per-variant rows (queued / running / completed / failed).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: Results UI + promotion + E2E + PR

**Files:**
- Create: `frontend/components/algo-trading/SweepResultsTable.tsx`
- Create: `frontend/components/algo-trading/SweepPboBadge.tsx`
- Create: `frontend/components/algo-trading/SweepEquityCurves.tsx`
- Create: `frontend/components/algo-trading/SweepPromoteModal.tsx`
- Create: `frontend/components/algo-trading/__tests__/SweepResultsTable.test.tsx`
- Create: `frontend/components/algo-trading/__tests__/SweepPboBadge.test.tsx`
- Modify: `e2e/utils/selectors.ts`
- Create: `e2e/pages/frontend/SweepPage.ts`
- Create: `e2e/algo-trading/sweep.spec.ts`
- Modify: `PROGRESS.md`

This task is the home stretch — Block A/B/C of the results UI, the promotion modal, an E2E smoke, and the PROGRESS entry.

- [ ] **Step 9.1: Implement SweepResultsTable**

Create `frontend/components/algo-trading/SweepResultsTable.tsx`:

```typescript
"use client";

import type { SweepResult } from "@/lib/types/algoSweep";
import { useState } from "react";
import { SweepPromoteModal } from "./SweepPromoteModal";

interface Props { run: SweepResult; }

export function SweepResultsTable({ run }: Props) {
  const [promoteOpen, setPromoteOpen] = useState(false);

  // Rank by Sharpe desc; ties keep original variant_index order.
  const sorted = [...run.variants].sort((a, b) => {
    const da = Number(b.sharpe) - Number(a.sharpe);
    if (da !== 0) return da;
    return a.variant_index - b.variant_index;
  });

  // Compute display rank with tie-handling.
  const rankBySharpe = new Map<number, number>();
  let rank = 0;
  let prev: number | null = null;
  for (const [i, v] of sorted.entries()) {
    const s = Number(v.sharpe);
    if (prev === null || s !== prev) rank = i + 1;
    rankBySharpe.set(v.variant_index, rank);
    prev = s;
  }

  return (
    <div
      className="rounded-md border"
      data-testid="sweep-results-table"
    >
      <table className="w-full text-xs">
        <thead className="bg-slate-50 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-1.5 text-left">Rank</th>
            <th className="px-3 py-1.5 text-left">Value</th>
            <th className="px-3 py-1.5 text-right">Trades</th>
            <th className="px-3 py-1.5 text-right">Win %</th>
            <th className="px-3 py-1.5 text-right">PnL %</th>
            <th className="px-3 py-1.5 text-right">Max DD %</th>
            <th className="px-3 py-1.5 text-right">Sharpe</th>
            <th className="px-3 py-1.5 text-right">DSR</th>
            <th className="px-3 py-1.5 text-left">Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((v) => {
            const r = rankBySharpe.get(v.variant_index)!;
            return (
              <tr
                key={v.variant_index}
                data-testid={
                  `sweep-results-row-${v.variant_index}`
                }
                className="border-t"
              >
                <td className="px-3 py-1.5">
                  {r === 1 ? "🏆 " : ""}{r}
                </td>
                <td className="px-3 py-1.5">
                  {String(v.swept_value)}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.n_trades}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.avg_win_rate_pct}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.avg_pnl_pct}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.avg_max_drawdown_pct}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.sharpe}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.dsr}
                </td>
                <td className="px-3 py-1.5">
                  <a
                    href={
                      `/algo-trading/strategies?tab=backtest`
                      + `&walkforward_id=`
                      + v.walkforward_run_id
                    }
                    className="text-indigo-600 underline"
                  >
                    View →
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {run.winner_variant_index !== null && (
        <div className="p-3 border-t">
          <button
            type="button"
            onClick={() => setPromoteOpen(true)}
            className="rounded bg-emerald-600 text-white px-3 py-1.5 text-sm"
            data-testid="sweep-promote-winner-button"
          >
            Save winner as new strategy
          </button>
        </div>
      )}
      {promoteOpen && (
        <SweepPromoteModal
          run={run}
          onClose={() => setPromoteOpen(false)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 9.2: Implement SweepPboBadge**

Create `frontend/components/algo-trading/SweepPboBadge.tsx`:

```typescript
"use client";

import type { SweepResult } from "@/lib/types/algoSweep";
import { InfoTooltip } from "@/components/common/InfoTooltip";

interface Props { run: SweepResult; }

export function SweepPboBadge({ run }: Props) {
  const pbo = run.cross_variant_pbo;
  const T = run.returns_matrix_shape[0];
  const N = run.returns_matrix_shape[1];

  let verdict: string;
  let tone: "good" | "warn" | "bad" | "muted";
  if (pbo == null) {
    verdict = "N/A — too few common days or variants";
    tone = "muted";
  } else {
    const p = Number(pbo);
    if (p <= 0.30) {
      verdict = (
        "ROBUST. The rank-1 variant tends to also win "
        + "out-of-sample. Promotion is supported."
      );
      tone = "good";
    } else if (p <= 0.50) {
      verdict = (
        "AT-RISK. The rank-1 in-sample winner is partly "
        + "luck. Corroborate with a longer period before "
        + "promoting."
      );
      tone = "warn";
    } else {
      verdict = (
        "LIKELY OVERFIT. The in-sample winner regularly "
        + "underperforms out-of-sample. Don't pick by this "
        + "sweep alone."
      );
      tone = "bad";
    }
  }

  const toneClass = {
    good: "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30",
    warn: "border-amber-500 bg-amber-50 dark:bg-amber-950/30",
    bad: "border-rose-500 bg-rose-50 dark:bg-rose-950/30",
    muted: "border-slate-300 bg-slate-50 dark:bg-slate-800",
  }[tone];

  return (
    <div
      className={`rounded-md border p-4 ${toneClass}`}
      data-testid="sweep-pbo-badge"
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        <span>Cross-variant PBO</span>
        <InfoTooltip label="What is cross-variant PBO?">
          <span className="whitespace-pre-line">
            {
              "Probability of Backtest Overfitting "
              + "(Bailey-de Prado, 2017).\n\n"
              + "Across all variants in this sweep, how "
              + "often does the in-sample winner "
              + "underperform out-of-sample?\n\n"
              + "≤ 0.30 robust · ≤ 0.50 at-risk · "
              + "> 0.50 likely overfit."
            }
          </span>
        </InfoTooltip>
      </div>
      <p className="text-lg font-semibold mt-1">
        PBO ={" "}
        {pbo ?? "N/A"}
        <span className="ml-2 text-xs text-slate-500">
          ({T} days × {N} variants)
        </span>
      </p>
      <p className="text-xs mt-1">{verdict}</p>
    </div>
  );
}
```

- [ ] **Step 9.3: Implement SweepEquityCurves**

Create `frontend/components/algo-trading/SweepEquityCurves.tsx`:

```typescript
"use client";

import { useDarkMode } from "./useDarkMode";
import type { SweepResult } from "@/lib/types/algoSweep";
import ReactECharts from "echarts-for-react";

interface Props { run: SweepResult; }

export function SweepEquityCurves({ run }: Props) {
  const isDark = useDarkMode();

  // For v1: each variant row carries no per-day equity
  // curve in the SweepResult; the walk-forward child row
  // does. The full overlay UI requires fetching N child
  // rows. For the first ship, render a placeholder
  // pointing to "View → walk-forward" links.
  return (
    <div
      className="rounded-md border p-4 text-xs text-slate-500"
      data-testid="sweep-equity-curves"
      key={isDark ? "d" : "l"}
    >
      Overlaid equity curves coming in v2. For now, click
      "View →" on any variant row to see its walk-forward
      equity curve.
    </div>
  );
}
```

> **Note**: full equity-curve overlay needs fetching each
> variant's child walkforward row + reconstructing the
> per-day curve. That's a v2 polish — the v1 ship shows
> the placeholder. The table View → link gives users
> per-variant equity curves immediately.

- [ ] **Step 9.4: Implement SweepPromoteModal**

Create `frontend/components/algo-trading/SweepPromoteModal.tsx`:

```typescript
"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { SweepResult } from "@/lib/types/algoSweep";

interface Props {
  run: SweepResult;
  onClose: () => void;
}

export function SweepPromoteModal(
  { run, onClose }: Props,
) {
  const winnerIdx = run.winner_variant_index;
  const winner = winnerIdx !== null
    ? run.variants[winnerIdx] : null;
  const [name, setName] = useState(
    winner
      ? `Sweep winner — ${run.swept_field}=${winner.swept_value}`
      : "",
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!winner) {
    return null;
  }

  async function handleConfirm() {
    setSubmitting(true);
    setErr(null);
    try {
      const r = await apiFetch(
        `${API_URL}/algo/strategies/`
        + `${run.base_strategy_id}/clone`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            new_name: name,
            patch: {
              [run.swept_field]: winner!.swept_value,
            },
          }),
        },
      );
      if (!r.ok) {
        throw new Error(`Clone failed: ${r.status}`);
      }
      onClose();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? exc.message
          : "Failed to promote",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="sweep-promote-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-96 space-y-3">
        <h3 className="text-sm font-semibold">
          Save winner as new strategy
        </h3>
        <p className="text-xs">
          Winning value: {run.swept_field}=
          {String(winner.swept_value)} (Sharpe=
          {winner.sharpe})
        </p>
        <label className="flex flex-col gap-1 text-xs">
          <span>New strategy name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="rounded border px-2 py-1"
          />
        </label>
        {err && (
          <p className="text-xs text-rose-600">{err}</p>
        )}
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting || !name.trim()}
            className="rounded bg-emerald-600 text-white px-3 py-1.5 text-sm"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 9.5: Write Vitest tests for the results pieces**

Create `frontend/components/algo-trading/__tests__/SweepResultsTable.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { SweepResultsTable }
  from "../SweepResultsTable";
import type {
  SweepResult,
} from "@/lib/types/algoSweep";

const fixture: SweepResult = {
  run_id: "sweep-1",
  base_strategy_id: "strat-1",
  swept_field: "cooldown_days",
  swept_values: [3, 7, 14],
  variants: [
    {
      variant_index: 0, swept_value: 3,
      walkforward_run_id: "wf-1",
      avg_pnl_pct: "1.47",
      avg_win_rate_pct: "62.2",
      avg_max_drawdown_pct: "7.91",
      sharpe: "0.311", dsr: "0.41",
      n_trades: 82,
      status: "completed", error_text: null,
    },
    {
      variant_index: 1, swept_value: 7,
      walkforward_run_id: "wf-2",
      avg_pnl_pct: "3.74",
      avg_win_rate_pct: "63.9",
      avg_max_drawdown_pct: "7.63",
      sharpe: "0.648", dsr: "0.62",
      n_trades: 83,
      status: "completed", error_text: null,
    },
    {
      variant_index: 2, swept_value: 14,
      walkforward_run_id: "wf-3",
      avg_pnl_pct: "3.74",
      avg_win_rate_pct: "63.9",
      avg_max_drawdown_pct: "7.63",
      sharpe: "0.648", dsr: "0.62",
      n_trades: 83,
      status: "completed", error_text: null,
    },
  ],
  cross_variant_pbo: "0.328",
  returns_matrix_shape: [122, 3],
  winner_variant_index: 1,
  started_at: "2026-05-24T10:00:00Z",
  completed_at: "2026-05-24T10:15:00Z",
  status: "completed",
};

describe("SweepResultsTable", () => {
  it("renders rows sorted by Sharpe descending", () => {
    render(<SweepResultsTable run={fixture} />);
    const rows = screen.getAllByTestId(
      /^sweep-results-row-/,
    );
    // sorted order: 1 (sharpe 0.648), 2 (sharpe 0.648), 0
    expect(rows[0].getAttribute("data-testid"))
      .toBe("sweep-results-row-1");
    expect(rows[2].getAttribute("data-testid"))
      .toBe("sweep-results-row-0");
  });

  it("ties at Sharpe share the same rank", () => {
    render(<SweepResultsTable run={fixture} />);
    const rows = screen.getAllByTestId(
      /^sweep-results-row-/,
    );
    const rank0 = rows[0].textContent ?? "";
    const rank1 = rows[1].textContent ?? "";
    // Both start with "1" (variant 1 has 🏆, variant 2 doesn't)
    expect(rank0.includes("🏆 1")).toBe(true);
    expect(rank1.trim().startsWith("1")).toBe(true);
  });

  it("renders promote button when winner exists", () => {
    render(<SweepResultsTable run={fixture} />);
    expect(
      screen.getByTestId("sweep-promote-winner-button"),
    ).toBeDefined();
  });
});
```

Create `frontend/components/algo-trading/__tests__/SweepPboBadge.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { SweepPboBadge } from "../SweepPboBadge";

function mkRun(pbo: string | null) {
  return {
    run_id: "x",
    base_strategy_id: "y",
    swept_field: "cooldown_days",
    swept_values: [3, 7],
    variants: [],
    cross_variant_pbo: pbo,
    returns_matrix_shape: [100, 2] as [number, number],
    winner_variant_index: null,
    started_at: "",
    completed_at: null,
    status: "completed" as const,
  };
}

describe("SweepPboBadge", () => {
  it("shows ROBUST verdict when PBO ≤ 0.30", () => {
    render(<SweepPboBadge run={mkRun("0.20")} />);
    expect(screen.getByText(/ROBUST/)).toBeDefined();
  });

  it("shows AT-RISK verdict when 0.30 < PBO ≤ 0.50", () => {
    render(<SweepPboBadge run={mkRun("0.40")} />);
    expect(screen.getByText(/AT-RISK/)).toBeDefined();
  });

  it("shows LIKELY OVERFIT verdict when PBO > 0.50", () => {
    render(<SweepPboBadge run={mkRun("0.70")} />);
    expect(screen.getByText(/LIKELY OVERFIT/))
      .toBeDefined();
  });

  it("shows N/A when PBO is null", () => {
    render(<SweepPboBadge run={mkRun(null)} />);
    expect(screen.getByText(/N\/A/)).toBeDefined();
  });
});
```

- [ ] **Step 9.6: Run frontend tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx vitest run \
  components/algo-trading/__tests__/SweepResultsTable.test.tsx \
  components/algo-trading/__tests__/SweepPboBadge.test.tsx \
  components/algo-trading/__tests__/SweepForm.test.tsx \
  components/algo-trading/__tests__/useSweepRuns.test.ts
```

Expected: 3 + 4 + 3 + 1 = 11 passed.

- [ ] **Step 9.7: Add E2E testids to registry**

Edit `e2e/utils/selectors.ts`. Find the `FE` object and add:

```typescript
  sweepSubTab: "sweep-sub-tab",
  sweepForm: "sweep-form",
  sweepBaseStrategySelect: "sweep-base-strategy-select",
  sweepPeriodFrom: "sweep-period-from",
  sweepPeriodTo: "sweep-period-to",
  sweepTrainDays: "sweep-train-days",
  sweepTestDays: "sweep-test-days",
  sweepStepDays: "sweep-step-days",
  sweepFieldSelect: "sweep-field-select",
  sweepValuesInput: "sweep-values-input",
  sweepRegimeStratified: "sweep-regime-stratified",
  sweepSubmit: "sweep-submit",
  sweepProgressPanel: "sweep-progress-panel",
  sweepResultsTable: "sweep-results-table",
  sweepPboBadge: "sweep-pbo-badge",
  sweepPromoteWinnerButton: "sweep-promote-winner-button",
  sweepPromoteModal: "sweep-promote-modal",
```

- [ ] **Step 9.8: Create the Playwright POM**

Create `e2e/pages/frontend/SweepPage.ts`:

```typescript
import { BasePage } from "./BasePage";
import { FE } from "@/utils/selectors";

export class SweepPage extends BasePage {
  async open() {
    await this.page.goto(
      "/algo-trading/strategies?tab=backtest",
    );
    await this.page.click(`text=Parameter sweep`);
    await this.tid(FE.sweepForm).waitFor();
  }

  async selectStrategy(name: string) {
    await this.tid(FE.sweepBaseStrategySelect)
      .selectOption({ label: name });
  }

  async setField(label: string) {
    await this.tid(FE.sweepFieldSelect)
      .selectOption({ label });
  }

  async setValues(csv: string) {
    await this.tid(FE.sweepValuesInput).fill(csv);
  }

  async submit() {
    await this.tid(FE.sweepSubmit).click();
  }

  async waitForResults() {
    await this.tid(FE.sweepResultsTable).waitFor({
      state: "attached", timeout: 180_000,
    });
  }
}
```

- [ ] **Step 9.9: Create the E2E spec**

Create `e2e/algo-trading/sweep.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { SweepPage } from "@/pages/frontend/SweepPage";
import { FE } from "@/utils/selectors";

test.use({
  storageState: "e2e/.auth/superuser.json",
});

test("sweep produces ranked variants + PBO + promote", async ({
  page,
}) => {
  const sweep = new SweepPage(page);
  await sweep.open();
  await sweep.selectStrategy("RSI(2) Connors Daily v3");
  await sweep.setField("Cooldown (days)");
  await sweep.setValues("3, 7, 14");
  await sweep.submit();

  // Progress panel appears within 30s
  await expect(
    page.getByTestId(FE.sweepProgressPanel),
  ).toBeVisible({ timeout: 30_000 });

  // Results render once sweep completes
  await sweep.waitForResults();

  // 3 rows
  const rows = page.getByTestId(
    /^sweep-results-row-/,
  );
  await expect(rows).toHaveCount(3);

  // Rank 1 row has trophy
  await expect(rows.first()).toContainText("🏆");

  // PBO badge present
  await expect(
    page.getByTestId(FE.sweepPboBadge),
  ).toBeVisible();

  // Open promote modal
  await page.getByTestId(
    FE.sweepPromoteWinnerButton,
  ).click();
  await expect(
    page.getByTestId(FE.sweepPromoteModal),
  ).toBeVisible();
});
```

- [ ] **Step 9.10: Add PROGRESS.md entry**

Edit `PROGRESS.md`. Add a new dated entry at the TOP:

```markdown
### 2026-05-24 — Walk-forward parameter sweep (1D)

Shipped Option B parameter sweep on top of walk-forward CV.
User picks a saved strategy + one tunable field (curated
whitelist of 7) + a list of values; engine runs a full
walk-forward per value and reports per-variant metrics
(Sharpe-ranked) plus a cross-variant PBO (Bailey-de Prado
CSCV).

Three-level row tree in `algo.runs` (sweep → walkforward →
backtest). Serial execution; AST mutated in memory only.
Failure-tolerant: a variant crash skips that row and the
sweep completes if ≥ 2 variants survive.

PRs shipped (one slice each):
- migration + Pydantic types
- whitelist + validators
- _mutate_ast helper
- PBO aggregation primitives
- sweep runner + repo extensions
- HTTP routes
- frontend types + hooks + sub-tab shell
- SweepForm + SweepProgressPanel
- Results table + PBO badge + promote modal + E2E

Deferred to v2:
- Grid (multi-param) sweep
- Parallel variant execution
- AST-path escape hatch
- Equity-curve overlay (Block C placeholder shipped)
- Mid-run cancellation
- Auto-promotion to paper

Spec: `docs/superpowers/specs/2026-05-24-walkforward-parameter-sweep-design.md`
Plan: `docs/superpowers/plans/2026-05-24-walkforward-parameter-sweep.md`
```

- [ ] **Step 9.11: Stage serena memories**

```bash
git add .serena/ 2>/dev/null
git status --short
```

- [ ] **Step 9.12: Final commit + push + PR**

```bash
git add frontend/components/algo-trading/SweepResultsTable.tsx \
        frontend/components/algo-trading/SweepPboBadge.tsx \
        frontend/components/algo-trading/SweepEquityCurves.tsx \
        frontend/components/algo-trading/SweepPromoteModal.tsx \
        frontend/components/algo-trading/__tests__/SweepResultsTable.test.tsx \
        frontend/components/algo-trading/__tests__/SweepPboBadge.test.tsx \
        e2e/utils/selectors.ts \
        e2e/pages/frontend/SweepPage.ts \
        e2e/algo-trading/sweep.spec.ts \
        PROGRESS.md
git commit -m "$(cat <<'EOF'
feat(sweep-ui): results table + PBO badge + promote + E2E

Block A (per-variant table, Sharpe-ranked, tie-aware ranks,
🏆 on rank 1) + Block B (PBO badge with color band and
three-tier verdict copy) + promote-winner modal that POSTs
to the existing /strategies/{id}/clone endpoint with the
mutated AST. Equity-curve overlay (Block C) ships as a
placeholder for v1; full overlay deferred to v2.

E2E smoke: form → progress → results → promote modal.

PROGRESS.md entry summarising the 9 slices.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"

git push -u origin feature/walkforward-parameter-sweep
gh pr create \
  --base dev \
  --title "Walk-forward parameter sweep (Option B, 1D)" \
  --body "$(cat <<'EOF'
## Summary

- 1D parameter sweep on top of walk-forward CV — pick a strategy + one tunable field + a list of values, get per-variant walk-forward results plus a cross-variant PBO
- Three-level row tree in `algo.runs` (sweep → walkforward → backtest); serial execution; AST mutated in memory only
- Curated whitelist of 7 sweepable fields; validation client + server
- Manual "Save winner as new strategy" promotion
- Failure-tolerant: ≥ 2 surviving variants → sweep completes

Spec: `docs/superpowers/specs/2026-05-24-walkforward-parameter-sweep-design.md`
Plan: `docs/superpowers/plans/2026-05-24-walkforward-parameter-sweep.md`

## Test plan

- [x] `docker compose exec backend python -m pytest backend/algo/backtest/tests/test_sweep_*.py backend/algo/tests/test_sweep_routes.py -v` → all green
- [x] `cd frontend && npx vitest run components/algo-trading/__tests__/Sweep*` → all green
- [ ] `cd e2e && npx playwright test sweep.spec.ts --project=frontend-chromium` → smoke green
- [ ] Manual: kick off sweep with cd=[3, 7, 14, 21] on v3 (6-month period); confirm rank-1 trophy, PBO badge, promote-winner modal flow

## Out of scope (deferred to v2)

- Grid (multi-param combinatorial) sweep
- Parallel variant execution
- AST-path escape hatch for non-whitelist fields
- Equity-curve overlay (Block C placeholder shipped)
- Mid-run cancellation
- Auto-promotion to paper
- Bootstrap CI on PBO
- Returns-matrix heatmap

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- All 9 spec components (architecture, schema, types, whitelist, runner, PBO helpers, routes, frontend, testing) map 1:1 to the 9 tasks above.
- Pre-existing identifiers used by the plan (verified by inspecting source before writing): `BacktestRunsRepo.create_pending`, `BacktestRunsRepo.mark_running`, `BacktestRunsRepo.mark_failed`, `run_walkforward_job(*, walkforward_run_id, user_id, config, strategy, universe)`, `WalkForwardConfig`, `Strategy.risk.per_trade.cooldown_after_failed_exit_days`, `parse_strategy`, `probability_of_backtest_overfitting`, `BacktestSummary.equity_curve`, `EquityPoint`, `resolve_universe`, `get_strategy`, `get_session_factory`, `apiFetch`, `API_URL`, `InfoTooltip`, `useDarkMode`.
- The path `risk.per_trade.cooldown_after_failed_exit_days` is verified to exist in `backend/algo/strategy/templates/rsi2_connors_daily_v3.json` at the `risk.per_trade` nesting (line 107-113).
- Each task ends in exactly one commit; the 9 commits map to 9 PR slices.
- Test files use the same `docker compose exec backend python -m pytest` / `cd frontend && npx vitest run` invocation pattern the codebase already uses.
- No bare `print()` in any new script; all logging via `_logger`.
- Pydantic models use `model_config = ConfigDict(extra="forbid")` matching the codebase convention.
- The `_mutate_ast` test (Task 3) uses the real v3 template file rather than a mock — catches actual path-resolution issues against the canonical strategy shape.
- The PBO computation uses `probability_of_backtest_overfitting()` from `metrics.py` unchanged (no new copy).
- E2E smoke uses the existing storage-state superuser fixture (`e2e/.auth/superuser.json`) per CLAUDE.md §5.14.
- Equity-curve overlay (Block C) ships as a placeholder in v1 with a note pointing to v2 — flagged in the PR body's "out of scope" list.
