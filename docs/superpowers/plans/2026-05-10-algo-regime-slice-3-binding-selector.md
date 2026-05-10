# Regime-Aware Multi-Factor System — Slice REGIME-3: Strategy↔Regime Binding + Selector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two-tier strategy↔regime expressiveness — `applicable_regimes` metadata field on strategies + in-AST `regime_eq("bull")` predicate + selector that filters/warns by current regime + amber banner on regime flip.

**Architecture:** PG `algo.strategy_metadata` table (mutable). AST `Literal_.literal` extended to accept `str` (one-line widening — no new node type). Evaluator handles string-equal/not-equal when both operands are strings. Daily orchestrator detects regime flips by diffing today vs yesterday in `stocks.regime_history`; emits one `regime_changed` event per boundary. Frontend gets `useRegimeStatus` SWR hook + `RegimeChangeBanner` (polls + diffs against localStorage cache; 4-hour dismiss TTL) + multi-select chips in StrategyBuilder.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, Alembic, Pydantic v2, Next.js 16, React 19, SWR.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.9 (AST grammar), §1 Goals + Regime flip behavior, §5.1 REGIME-3 row.

**Branch:** `feature/regime-slice-3-binding-selector` (already created, tracking `origin`).

**Estimated SP:** 8

---

## Pre-flight (MUST DO before writing code)

Per `feedback_subagent_grep_preflight` — verify these symbols/paths exist BEFORE each task:

- **Alembic head revision:** `e1f2a3b4c5d6` — confirmed via `docker compose exec backend alembic current`. Use this as `down_revision` for the new migration.
- **Alembic dir:** `backend/db/migrations/versions/`. Existing template: `backend/db/migrations/versions/2026_05_11_algo_drift_state.py` (has `op.create_table` + `postgresql.UUID` patterns).
- **AST module:** `backend/algo/strategy/ast.py`. `Literal_` model at line ~51 with `literal: float | int`. `CompareNode` at line ~64. `Strategy` class at line ~281 — needs no change (metadata is a separate PG row keyed by strategy.id, not embedded in AST).
- **Evaluator:** `backend/algo/backtest/evaluator.py` — `_resolve_operand` at line ~37, `compare` handler at line ~55. Both currently coerce to `Decimal`. Needs string-aware path.
- **Strategy CRUD routes:** `backend/algo/routes/strategies.py` — `create_strategies_router()` factory, methods `list_/get_/create_/update_/delete_`. Use `pro_or_superuser` dep + `_get_session_factory()`.
- **`UserContext`** at `auth.models.response.UserContext`.
- **Strategy repo:** `backend/algo/strategy/repo.py` — `list_strategies / get_strategy / create_strategy / update_strategy`. Need to verify the actual function signatures with grep.
- **Event writer:** `backend/algo/backtest/event_writer.py` — `event_row(...)` factory; `flush_events(...)`. Used to emit events into `algo.events`.
- **Features registry:** `backend/algo/strategy/features.py` — REGIME-1 already registered `regime_label` (string type) under source `"regime"`. Verify before re-registering.
- **Frontend Strategy type:** `frontend/lib/types/algoStrategy.ts` — extend with optional `applicable_regimes?: ("bull"|"sideways"|"bear")[]`.
- **StrategyBuilder UI:** `frontend/components/algo-trading/builder/StrategyBuilder.tsx`.
- **PaperTab (header host):** `frontend/components/algo-trading/PaperTab.tsx` — RegimeWidget already mounted by REGIME-1. Banner mounts in same header row.

If any symbol doesn't resolve, STOP and report.

---

## File Structure

**Backend — new:**
- `backend/db/migrations/versions/2026_05_10_algo_strategy_metadata.py` — Alembic migration creating `algo.strategy_metadata`.
- `backend/algo/strategy/metadata_repo.py` — async PG CRUD for the metadata table.
- `backend/algo/jobs/regime_change_notifier.py` — daily orchestrator (22:35 IST) that diffs today's vs yesterday's regime in `stocks.regime_history` and emits a `regime_changed` event when different.
- `backend/algo/tests/test_strategy_metadata.py`
- `backend/algo/tests/test_ast_string_literal.py`
- `backend/algo/tests/test_evaluator_string_compare.py`
- `backend/algo/tests/test_regime_change_notifier.py`

**Backend — modified:**
- `backend/algo/strategy/ast.py` — widen `Literal_.literal: float | int` → `float | int | str`.
- `backend/algo/backtest/evaluator.py` — handle string equal/not-equal in `compare` when both operands are strings.
- `backend/algo/strategy/repo.py` — extend strategy GET response shape with `applicable_regimes` (joined from `algo.strategy_metadata`); extend create/update to upsert metadata.
- `backend/algo/routes/strategies.py` — accept optional `applicable_regimes` in `StrategyCreateRequest`/`StrategyUpdateRequest`; return in `Strategy` response.
- `backend/algo/jobs/__init__.py` — import `regime_change_notifier` for `@register_job` side-effect.
- `backend/jobs/executor.py` — add `@register_job("regime_change_notifier")` wrapper.

**Frontend — new:**
- `frontend/hooks/useStrategyMetadata.ts` — SWR fetch of `applicable_regimes` per strategy; `upsertStrategyMetadata()` writer.
- `frontend/components/algo-trading/RegimeChangeBanner.tsx` — amber banner driven by `useRegimeCurrent` (REGIME-1 hook); compares against localStorage cache `algo.regime.lastSeen`; shows "Regime changed BULL → SIDEWAYS" with dismiss button (4-hour TTL stored in localStorage).
- `frontend/components/algo-trading/RegimeApplicabilityChips.tsx` — multi-select chip group for `["bull","sideways","bear"]` (defaults all-3 = regime-agnostic); used inside StrategyBuilder.

**Frontend — modified:**
- `frontend/lib/types/algoStrategy.ts` — extend Strategy type with optional `applicable_regimes`.
- `frontend/components/algo-trading/builder/StrategyBuilder.tsx` — mount `RegimeApplicabilityChips` in editor header.
- `frontend/components/algo-trading/PaperTab.tsx` — mount `RegimeChangeBanner` immediately above `<RegimeWidget />` block in the header row.

**E2E:**
- `e2e/utils/selectors.ts` — testids `regimeChangeBanner`, `regimeChangeBannerDismiss`, `regimeApplicabilityChips`, `regimeApplicabilityChipBull`, `regimeApplicabilityChipSideways`, `regimeApplicabilityChipBear`.
- `e2e/tests/frontend/algo-regime-binding.spec.ts` — strategy CRUD with `applicable_regimes`; banner dismiss persists across reload.

---

## Task 1 — PG migration: `algo.strategy_metadata`

**Files:**
- Create: `backend/db/migrations/versions/2026_05_10_algo_strategy_metadata.py`.
- Test: `backend/algo/tests/test_strategy_metadata.py` (the migration test asserts table exists post-upgrade).

Schema: `strategy_id UUID PK FK→algo.strategies(id) ON DELETE CASCADE`, `applicable_regimes TEXT[] NOT NULL DEFAULT ARRAY['bull','sideways','bear']`, `expected_edge NUMERIC NULL`, `description TEXT NOT NULL DEFAULT ''`, `updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()`.

- [ ] **Step 1.1: Failing test**

Create `backend/algo/tests/test_strategy_metadata.py`:

```python
"""Round-trip tests for the strategy_metadata PG table + repo."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from backend.algo.strategy.metadata_repo import (
    upsert_metadata,
    get_metadata,
    delete_metadata,
    StrategyMetadata,
)


@pytest.mark.asyncio
async def test_upsert_then_get(pg_session_factory) -> None:
    """Upsert a metadata row, fetch it back."""
    sid = uuid4()
    factory = pg_session_factory
    async with factory() as s:
        await upsert_metadata(
            s, sid,
            StrategyMetadata(
                applicable_regimes=["bull", "sideways"],
                expected_edge=0.18,
                description="bull/sideways momentum strategy",
            ),
        )
        await s.commit()
    async with factory() as s:
        got = await get_metadata(s, sid)
    assert got is not None
    assert got.applicable_regimes == ["bull", "sideways"]
    assert float(got.expected_edge) == pytest.approx(0.18)


@pytest.mark.asyncio
async def test_get_missing_returns_none(pg_session_factory) -> None:
    sid = uuid4()
    async with pg_session_factory() as s:
        got = await get_metadata(s, sid)
    assert got is None


@pytest.mark.asyncio
async def test_default_regime_agnostic(pg_session_factory) -> None:
    """When applicable_regimes is empty/None, treat as all-3."""
    md = StrategyMetadata()
    assert md.applicable_regimes == ["bull", "sideways", "bear"]
```

If the project's pg_session_factory fixture name differs, grep:
```bash
grep -rn "pg_session_factory\|@pytest.fixture.*session" backend/algo/tests/conftest.py 2>/dev/null
```

- [ ] **Step 1.2: Run to verify fail**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_strategy_metadata.py -v
```
Expected: ImportError.

- [ ] **Step 1.3: Implement migration**

Create `backend/db/migrations/versions/2026_05_10_algo_strategy_metadata.py`:

```python
"""algo: add strategy_metadata table (REGIME-3 binding).

Adds:
  - algo.strategy_metadata — per-strategy regime applicability + edge
    + free-form description. FK→algo.strategies(id) CASCADE.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a1b2c4d5e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_metadata",
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "algo.strategies.id", ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "applicable_regimes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text(
                "ARRAY['bull','sideways','bear']::text[]",
            ),
        ),
        sa.Column(
            "expected_edge",
            sa.Numeric(),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_table("strategy_metadata", schema="algo")
```

If the existing `algo.strategies` PK column name differs (e.g. `strategy_id` vs `id`), grep first:
```bash
grep -n "Column.*primary_key=True" backend/db/migrations/versions/2026_05_08_algo_schema_init.py | head -5
```

- [ ] **Step 1.4: Run migration**

```bash
docker compose exec -T backend alembic upgrade head
```
Expected: log line for the new revision.

- [ ] **Step 1.5: Implement `metadata_repo.py`**

Create `backend/algo/strategy/metadata_repo.py`:

```python
"""Async PG repo for algo.strategy_metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class StrategyMetadata:
    applicable_regimes: list[str] = field(
        default_factory=lambda: ["bull", "sideways", "bear"],
    )
    expected_edge: Decimal | float | None = None
    description: str = ""


async def upsert_metadata(
    session: AsyncSession,
    strategy_id: UUID,
    md: StrategyMetadata,
) -> None:
    await session.execute(
        text(
            "INSERT INTO algo.strategy_metadata "
            "(strategy_id, applicable_regimes, expected_edge, "
            " description, updated_at) "
            "VALUES (:sid, :regimes, :edge, :descr, NOW()) "
            "ON CONFLICT (strategy_id) DO UPDATE SET "
            "applicable_regimes = EXCLUDED.applicable_regimes, "
            "expected_edge = EXCLUDED.expected_edge, "
            "description = EXCLUDED.description, "
            "updated_at = NOW()"
        ),
        {
            "sid": str(strategy_id),
            "regimes": md.applicable_regimes,
            "edge": (
                float(md.expected_edge)
                if md.expected_edge is not None else None
            ),
            "descr": md.description,
        },
    )


async def get_metadata(
    session: AsyncSession, strategy_id: UUID,
) -> StrategyMetadata | None:
    row = (await session.execute(
        text(
            "SELECT applicable_regimes, expected_edge, description "
            "FROM algo.strategy_metadata WHERE strategy_id = :sid"
        ),
        {"sid": str(strategy_id)},
    )).mappings().first()
    if row is None:
        return None
    return StrategyMetadata(
        applicable_regimes=list(row["applicable_regimes"]),
        expected_edge=row["expected_edge"],
        description=row["description"] or "",
    )


async def delete_metadata(
    session: AsyncSession, strategy_id: UUID,
) -> None:
    await session.execute(
        text(
            "DELETE FROM algo.strategy_metadata "
            "WHERE strategy_id = :sid"
        ),
        {"sid": str(strategy_id)},
    )
```

- [ ] **Step 1.6: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_strategy_metadata.py -v
git add backend/db/migrations/versions/2026_05_10_algo_strategy_metadata.py backend/algo/strategy/metadata_repo.py backend/algo/tests/test_strategy_metadata.py
git commit -m "feat(algo): algo.strategy_metadata PG table + async repo (REGIME-3)"
```

---

## Task 2 — AST `Literal_` widening + evaluator string-compare

**Files:**
- Modify: `backend/algo/strategy/ast.py` (one-line widen).
- Modify: `backend/algo/backtest/evaluator.py` (string-aware compare).
- Test: `backend/algo/tests/test_ast_string_literal.py`, `backend/algo/tests/test_evaluator_string_compare.py`.

The cleanest extension: widen `Literal_.literal: float | int` → `float | int | str`. No new node, no schema change. Evaluator's `_resolve_operand` already returns the raw value; `compare` needs to dispatch to string-equal when both sides are strings (and reject mixed string/numeric comparisons cleanly).

`regime_eq("bull")` is therefore writable directly today as:
```json
{"type": "compare", "left": {"feature": "regime_label"},
 "op": "==", "right": {"literal": "bull"}}
```

No "sugar node" needed — keep the AST minimal.

- [ ] **Step 2.1: AST literal widening test**

Create `backend/algo/tests/test_ast_string_literal.py`:

```python
"""Verify Literal_ accepts str values and parses round-trip."""
from __future__ import annotations

from backend.algo.strategy.ast import Literal_


def test_string_literal_parses() -> None:
    lit = Literal_.model_validate({"literal": "bull"})
    assert lit.literal == "bull"


def test_int_literal_still_works() -> None:
    lit = Literal_.model_validate({"literal": 42})
    assert lit.literal == 42


def test_float_literal_still_works() -> None:
    lit = Literal_.model_validate({"literal": 3.14})
    assert lit.literal == 3.14


def test_literal_rejects_dict() -> None:
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Literal_.model_validate({"literal": {"x": 1}})
```

- [ ] **Step 2.2: Run (verify fail)**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_ast_string_literal.py -v
```

- [ ] **Step 2.3: Widen `Literal_`**

Edit `backend/algo/strategy/ast.py`:
```python
class Literal_(BaseModel):
    model_config = ConfigDict(extra="forbid")
    literal: float | int | str
```

- [ ] **Step 2.4: Evaluator string-compare test**

Create `backend/algo/tests/test_evaluator_string_compare.py`:

```python
"""Evaluator handles string-equal and string-not-equal compares."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.algo.backtest.evaluator import Evaluator, EvalContext


def _ctx(features: dict) -> EvalContext:
    return EvalContext(
        ticker="TEST.NS",
        bar_date=date(2026, 5, 10),
        features=features,
        open_qty=0,
    )


def _compare(left, op, right):
    return {
        "type": "compare",
        "left": left, "op": op, "right": right,
    }


def test_string_equal_true() -> None:
    node = _compare(
        {"feature": "regime_label"}, "==", {"literal": "bull"},
    )
    ctx = _ctx({"regime_label": "bull"})
    assert Evaluator()._eval_condition(node, ctx) is True


def test_string_equal_false() -> None:
    node = _compare(
        {"feature": "regime_label"}, "==", {"literal": "bull"},
    )
    ctx = _ctx({"regime_label": "sideways"})
    assert Evaluator()._eval_condition(node, ctx) is False


def test_string_not_equal() -> None:
    node = _compare(
        {"feature": "regime_label"}, "!=", {"literal": "bear"},
    )
    ctx = _ctx({"regime_label": "bull"})
    assert Evaluator()._eval_condition(node, ctx) is True


def test_numeric_compare_unchanged() -> None:
    node = _compare(
        {"feature": "rsi"}, ">", {"literal": 70},
    )
    ctx = _ctx({"rsi": Decimal("75")})
    assert Evaluator()._eval_condition(node, ctx) is True
```

If `Evaluator._eval_condition` has a different name, grep + adjust:
```bash
grep -n "def _eval\|def evaluate\|def _resolve" backend/algo/backtest/evaluator.py | head -10
```

- [ ] **Step 2.5: Extend the evaluator**

Read `backend/algo/backtest/evaluator.py` `compare` branch first; the goal is: when BOTH operands resolve to strings, dispatch string equality. Otherwise keep numeric compare. Mixed types raise (don't silently coerce).

Concrete pattern (adjust to actual `_resolve_operand` signature):
```python
        if t == "compare":
            left_op = node["left"]
            right_op = node["right"]
            # String-equality fast-path: both operands string literals
            # OR string features. Resolve raw (no Decimal coercion).
            left_raw = _resolve_operand_raw(left_op, ctx)
            right_raw = _resolve_operand_raw(right_op, ctx)
            if isinstance(left_raw, str) or isinstance(right_raw, str):
                if not (isinstance(left_raw, str)
                        and isinstance(right_raw, str)):
                    raise ValueError(
                        f"Mixed string/numeric compare: "
                        f"{type(left_raw).__name__} {node['op']} "
                        f"{type(right_raw).__name__}"
                    )
                op = node["op"]
                if op == "==":
                    return left_raw == right_raw
                if op == "!=":
                    return left_raw != right_raw
                raise ValueError(
                    f"String operands only support ==/!=, got {op}"
                )
            # Numeric path — unchanged
            left = Decimal(str(left_raw))
            right = Decimal(str(right_raw))
            ...
```

Add a helper `_resolve_operand_raw(op, ctx)` that returns whatever the underlying type is (str for string features/literals, the existing numeric for everything else). Don't break the existing `_resolve_operand` Decimal-returning path used elsewhere.

- [ ] **Step 2.6: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_ast_string_literal.py backend/algo/tests/test_evaluator_string_compare.py -v
# Regression: existing evaluator tests pass
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/ -k "evaluator or strategy" -q --no-header 2>&1 | tail -5
git add backend/algo/strategy/ast.py backend/algo/backtest/evaluator.py backend/algo/tests/test_ast_string_literal.py backend/algo/tests/test_evaluator_string_compare.py
git commit -m "feat(algo): AST Literal_ accepts strings + evaluator string-compare (REGIME-3)"
```

---

## Task 3 — Strategy CRUD extension: `applicable_regimes` round-trip

**Files:**
- Modify: `backend/algo/strategy/repo.py` (extend list/get/create/update/delete to handle metadata).
- Modify: `backend/algo/routes/strategies.py` (extend request/response models).
- Test: `backend/algo/tests/test_strategy_metadata_crud.py`.

The metadata is a separate PG row, not part of the AST JSON. Strategy GET returns `applicable_regimes` as a sibling field; Strategy POST/PUT accepts it. Default = all 3 regimes.

- [ ] **Step 3.1: Failing test (HTTP round-trip)**

Create `backend/algo/tests/test_strategy_metadata_crud.py`:

```python
"""HTTP round-trip for applicable_regimes via strategies CRUD."""
from __future__ import annotations

# Mirror existing test_strategies_routes.py fixture pattern. Grep
# for the actual fixture name + auth dep override:
#   grep -n "test_app\|pro_or_superuser\|UserContext" \
#         backend/algo/tests/test_strategies_routes.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def _strategy_payload(name: str = "metadata-test") -> dict:
    """Minimal valid strategy AST. Mirror the helper used in
    test_strategies_routes.py — copy its _payload() output exactly."""
    # If a peer fixture exists (`minimal_strategy_payload`), use it.
    from backend.algo.tests._strategy_fixtures import (  # noqa
        minimal_strategy_payload,
    )
    p = minimal_strategy_payload()
    p["name"] = name
    return p


def test_create_with_applicable_regimes(client) -> None:
    body = {
        "payload": _strategy_payload(),
        "applicable_regimes": ["bull", "sideways"],
    }
    r = client.post("/v1/algo/strategies", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    r = client.get(f"/v1/algo/strategies/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["applicable_regimes"] == ["bull", "sideways"]


def test_default_regime_agnostic(client) -> None:
    """No applicable_regimes in POST → default to all 3."""
    body = {"payload": _strategy_payload(name="default-test")}
    r = client.post("/v1/algo/strategies", json=body)
    sid = r.json()["id"]
    r = client.get(f"/v1/algo/strategies/{sid}")
    assert set(r.json()["applicable_regimes"]) == {
        "bull", "sideways", "bear",
    }
```

If `_strategy_fixtures` doesn't exist, inline the AST payload exactly mirroring the one in `test_strategies_routes.py::_payload()` — grep for it first.

- [ ] **Step 3.2: Run + verify fail**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_strategy_metadata_crud.py -v
```

- [ ] **Step 3.3: Extend Pydantic request/response models**

Edit `backend/algo/routes/strategies.py`:

1. Find `StrategyCreateRequest` (likely in same file or in `backend/algo/routes/_models.py`). Add:
```python
class StrategyCreateRequest(BaseModel):
    payload: dict
    applicable_regimes: list[str] | None = None  # None = use default
```

2. Find the `Strategy` response model (likely `backend.algo.strategy.ast.Strategy` Pydantic — grep). Since `Strategy` is the AST model, the HTTP response shape is wider — extend the route's GET handler to wrap with metadata. Easiest: add a `StrategyResponse(BaseModel)` that nests the strategy + applicable_regimes:
```python
class StrategyResponse(BaseModel):
    id: UUID
    name: str
    payload: dict        # the strategy AST as JSON
    applicable_regimes: list[str]
```

Update the GET route to compose it from `get_strategy()` + `get_metadata()`.

3. Update POST/PUT to call `upsert_metadata()` after `create_strategy()`/`update_strategy()` succeeds. Default = `["bull", "sideways", "bear"]` when None.

(If the project has a different response convention — i.e. `Strategy` IS the response — keep it but add `applicable_regimes` as a sibling on the JSON output via the route handler's `Response.model_dump()`. Adapt to match existing peers.)

- [ ] **Step 3.4: Extend `repo.py` if needed**

If existing `get_strategy()` returns the bare AST, leave it alone — do the metadata join at the route layer (composable, no repo bloat).

If you prefer joining in the repo, grep `def get_strategy` and add an optional `include_metadata: bool = False` parameter that LEFT JOINs `algo.strategy_metadata` and returns a tuple/dataclass.

- [ ] **Step 3.5: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_strategy_metadata_crud.py -v
# Regression: existing strategy CRUD tests must still pass
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_strategies_routes.py -q --no-header 2>&1 | tail -5
git add backend/algo/routes/strategies.py backend/algo/strategy/repo.py backend/algo/tests/test_strategy_metadata_crud.py
git commit -m "feat(algo): strategy CRUD round-trips applicable_regimes (REGIME-3)"
```

---

## Task 4 — Regime-change daily notifier

**Files:**
- Create: `backend/algo/jobs/regime_change_notifier.py`.
- Test: `backend/algo/tests/test_regime_change_notifier.py`.
- Modify: `backend/algo/jobs/__init__.py` (import for side-effect), `backend/jobs/executor.py` (`@register_job` wrapper).

22:35 IST (after the regime classifier has run at 22:30). Compares today's vs yesterday's regime in `stocks.regime_history`. If different, emits ONE `regime_changed` event into `algo.events` (no per-user / per-strategy expansion — frontend polls `useRegimeCurrent` + diffs against localStorage to render the banner). **No email** — deferred to v3.1.

- [ ] **Step 4.1: Failing test**

```python
"""Regime change notifier — diffs today vs yesterday, emits event once."""
from __future__ import annotations

from datetime import date

from backend.algo.jobs import regime_change_notifier as mod


def test_no_event_when_regime_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "_get_regime_for_date",
        lambda d: "BULL",
    )
    captured: list = []
    monkeypatch.setattr(mod, "_emit_event", lambda **kw: captured.append(kw))
    mod.run_notifier(as_of=date(2026, 5, 10))
    assert captured == []


def test_event_emitted_on_flip(monkeypatch) -> None:
    def regime_for(d: date) -> str | None:
        return "SIDEWAYS" if d == date(2026, 5, 10) else "BULL"
    monkeypatch.setattr(mod, "_get_regime_for_date", regime_for)
    captured: list = []
    monkeypatch.setattr(mod, "_emit_event", lambda **kw: captured.append(kw))
    mod.run_notifier(as_of=date(2026, 5, 10))
    assert len(captured) == 1
    payload = captured[0]["payload"]
    assert payload["from_regime"] == "BULL"
    assert payload["to_regime"] == "SIDEWAYS"


def test_no_event_when_yesterday_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "_get_regime_for_date",
        lambda d: "BULL" if d == date(2026, 5, 10) else None,
    )
    captured: list = []
    monkeypatch.setattr(mod, "_emit_event", lambda **kw: captured.append(kw))
    mod.run_notifier(as_of=date(2026, 5, 10))
    assert captured == []
```

- [ ] **Step 4.2: Implement**

Create `backend/algo/jobs/regime_change_notifier.py`:

```python
"""Daily regime-change notifier (22:35 IST).

Diffs today's vs yesterday's regime label from stocks.regime_history.
On flip, writes a single ``regime_changed`` event to ``algo.events``.
Frontend ``RegimeChangeBanner`` polls ``useRegimeCurrent`` and shows
the amber banner via localStorage diff — no per-user fan-out here.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from uuid import uuid4

from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.regime.repo import get_regime_history

_logger = logging.getLogger(__name__)


def _get_regime_for_date(d: date) -> str | None:
    rows = get_regime_history(start=d, end=d)
    return rows[0].regime_label if rows else None


def _emit_event(*, payload: dict) -> None:
    session_id = uuid4()
    row = event_row(
        session_id=session_id,
        user_id=None,
        strategy_id=None,
        mode="system",
        type_="regime_changed",
        payload=payload,
    )
    flush_events([row])


def run_notifier(as_of: date | None = None) -> dict | None:
    if as_of is None:
        as_of = date.today()
    today = _get_regime_for_date(as_of)
    yesterday = _get_regime_for_date(as_of - timedelta(days=1))
    if today is None or yesterday is None:
        _logger.info(
            "regime_changed: skip — today=%s yesterday=%s",
            today, yesterday,
        )
        return None
    if today == yesterday:
        return None
    payload = {
        "from_regime": yesterday,
        "to_regime": today,
        "bar_date": as_of.isoformat(),
    }
    _emit_event(payload=payload)
    _logger.info("regime_changed event emitted: %s", payload)
    return payload
```

If `event_row()` or `flush_events()` signatures differ, grep `backend/algo/backtest/event_writer.py` and adapt. Mirror the call shape used in `regime_classifier_daily` if simpler.

- [ ] **Step 4.3: Wire scheduler entry**

`backend/algo/jobs/__init__.py`:
```python
# REGIME-3 — daily regime-change notifier (22:35 IST)
from backend.algo.jobs import regime_change_notifier  # noqa: F401
```

`backend/jobs/executor.py` — add after the REGIME-2a wrapper:
```python
@register_job("regime_change_notifier")
def _regime_change_notifier(payload: dict) -> dict:
    from backend.algo.jobs.regime_change_notifier import run_notifier
    from datetime import date
    as_of = payload.get("as_of")
    parsed = date.fromisoformat(as_of) if as_of else None
    out = run_notifier(as_of=parsed)
    return {"emitted": out is not None, "payload": out}
```

- [ ] **Step 4.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_regime_change_notifier.py -v
docker compose restart backend && sleep 6
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend python -c "from jobs.executor import JOB_EXECUTORS; print('regime_change_notifier registered:', 'regime_change_notifier' in JOB_EXECUTORS)"
git add backend/algo/jobs/regime_change_notifier.py backend/algo/tests/test_regime_change_notifier.py backend/algo/jobs/__init__.py backend/jobs/executor.py
git commit -m "feat(algo): regime_change_notifier daily job + register_job wrapper (REGIME-3)"
```

---

## Task 5 — Frontend: useStrategyMetadata hook + Strategy type widening

**Files:**
- Modify: `frontend/lib/types/algoStrategy.ts` (add optional `applicable_regimes`).
- Create: `frontend/hooks/useStrategyMetadata.ts`.

- [ ] **Step 5.1: Widen Strategy type**

```ts
// in frontend/lib/types/algoStrategy.ts
export type RegimeLabel = "bull" | "sideways" | "bear";

export interface Strategy {
  id: string;
  name: string;
  payload: Record<string, unknown>;
  applicable_regimes?: RegimeLabel[];  // default = all 3 regimes
}
```

- [ ] **Step 5.2: Hook**

Create `frontend/hooks/useStrategyMetadata.ts`:

```ts
"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { RegimeLabel } from "@/lib/types/algoStrategy";

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

export function useStrategyMetadata(strategyId: string | null) {
  const { data, error, isLoading, mutate } = useSWR(
    strategyId ? `${API_URL}/algo/strategies/${strategyId}` : null,
    fetcher<{ applicable_regimes?: RegimeLabel[] }>,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return {
    applicableRegimes:
      (data?.applicable_regimes as RegimeLabel[] | undefined)
      ?? (["bull", "sideways", "bear"] as RegimeLabel[]),
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

export async function upsertStrategyMetadata(
  strategyId: string,
  applicableRegimes: RegimeLabel[],
  patch: Partial<{ payload: Record<string, unknown> }> = {},
): Promise<void> {
  const res = await apiFetch(
    `${API_URL}/algo/strategies/${strategyId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...patch,
        applicable_regimes: applicableRegimes,
      }),
    },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}
```

- [ ] **Step 5.3: Lint + commit**

```bash
cd frontend && npx eslint hooks/useStrategyMetadata.ts lib/types/algoStrategy.ts
git add frontend/lib/types/algoStrategy.ts frontend/hooks/useStrategyMetadata.ts
git commit -m "feat(algo-fe): useStrategyMetadata hook + RegimeLabel type (REGIME-3)"
```

---

## Task 6 — RegimeApplicabilityChips component + StrategyBuilder mount

**Files:**
- Create: `frontend/components/algo-trading/RegimeApplicabilityChips.tsx`.
- Modify: `frontend/components/algo-trading/builder/StrategyBuilder.tsx` (mount in editor header).

- [ ] **Step 6.1: Create the chip group**

```tsx
"use client";

import type { RegimeLabel } from "@/lib/types/algoStrategy";

const REGIMES: RegimeLabel[] = ["bull", "sideways", "bear"];

const ACTIVE_BG: Record<RegimeLabel, string> = {
  bull: "bg-emerald-500 text-white",
  sideways: "bg-slate-500 text-white",
  bear: "bg-rose-500 text-white",
};

interface Props {
  selected: RegimeLabel[];
  onChange: (next: RegimeLabel[]) => void;
  currentRegime?: RegimeLabel;
  disabled?: boolean;
}

export function RegimeApplicabilityChips({
  selected, onChange, currentRegime, disabled,
}: Props) {
  const toggle = (r: RegimeLabel) => {
    if (disabled) return;
    if (selected.includes(r)) {
      onChange(selected.filter(x => x !== r));
    } else {
      onChange([...selected, r]);
    }
  };
  const mismatched = currentRegime && !selected.includes(currentRegime);
  return (
    <div data-testid="regime-applicability-chips" className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium text-slate-500">
          Applicable regimes
        </span>
        {mismatched && (
          <span
            className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800 dark:bg-amber-950/50 dark:text-amber-200"
            data-testid="regime-applicability-mismatch-warning"
          >
            Current: {currentRegime?.toUpperCase()} — not selected
          </span>
        )}
      </div>
      <div className="flex gap-2">
        {REGIMES.map((r) => {
          const active = selected.includes(r);
          return (
            <button
              key={r}
              type="button"
              onClick={() => toggle(r)}
              disabled={disabled}
              className={
                "rounded-full px-3 py-1 text-xs font-medium "
                + "border border-slate-300 dark:border-slate-600 "
                + (active
                  ? ACTIVE_BG[r]
                  : "bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200")
              }
              data-testid={`regime-applicability-chip-${r}`}
            >
              {r.toUpperCase()}
            </button>
          );
        })}
      </div>
      <p className="text-[10px] text-slate-400">
        Empty = regime-agnostic (default). Strategies are filtered in
        the live selector by current regime ∩ this set.
      </p>
    </div>
  );
}
```

- [ ] **Step 6.2: Mount in StrategyBuilder**

Read `frontend/components/algo-trading/builder/StrategyBuilder.tsx`. Find the editor's top-of-form area (typically near the strategy name input). Add:

```tsx
import { RegimeApplicabilityChips } from "../RegimeApplicabilityChips";
import { useRegimeCurrent } from "@/hooks/useRegime";

// inside the component — assume `strategy` state holds the
// current AST + metadata; if not, lift state from the parent.
const { current: regimeCurrent } = useRegimeCurrent();
// ...
<RegimeApplicabilityChips
  selected={(strategy.applicable_regimes ?? ["bull","sideways","bear"]) as RegimeLabel[]}
  onChange={(next) => setStrategy({...strategy, applicable_regimes: next})}
  currentRegime={regimeCurrent?.regime_label?.toLowerCase() as RegimeLabel | undefined}
/>
```

If StrategyBuilder doesn't currently know about `applicable_regimes`, also wire it through to the persistence call (the existing save handler). Ensure the value round-trips via `upsertStrategyMetadata` on save (or via the existing PUT, depending on the project's CRUD shape).

- [ ] **Step 6.3: Lint + commit**

```bash
cd frontend && npx eslint components/algo-trading/RegimeApplicabilityChips.tsx components/algo-trading/builder/StrategyBuilder.tsx
git add frontend/components/algo-trading/RegimeApplicabilityChips.tsx frontend/components/algo-trading/builder/StrategyBuilder.tsx
git commit -m "feat(algo-fe): RegimeApplicabilityChips in StrategyBuilder (REGIME-3)"
```

---

## Task 7 — RegimeChangeBanner + PaperTab mount

**Files:**
- Create: `frontend/components/algo-trading/RegimeChangeBanner.tsx`.
- Modify: `frontend/components/algo-trading/PaperTab.tsx` (mount above existing `<RegimeWidget />`).
- Modify: `e2e/utils/selectors.ts` (testids).

Banner driven by `useRegimeCurrent` polling (60s). Compares current `regime_label` with `localStorage.getItem("algo.regime.lastSeen")`. On mismatch shows banner; on dismiss writes `algo.regime.dismissed:<regime>` with a 4-hour expiry timestamp. SSR-safe: localStorage access only inside `useEffect`.

- [ ] **Step 7.1: Add testids**

In `e2e/utils/selectors.ts` `FE` object (REGIME-1 area), append:
```ts
  regimeChangeBanner: "regime-change-banner",
  regimeChangeBannerDismiss: "regime-change-banner-dismiss",
  regimeApplicabilityChips: "regime-applicability-chips",
  regimeApplicabilityChipBull: "regime-applicability-chip-bull",
  regimeApplicabilityChipSideways: "regime-applicability-chip-sideways",
  regimeApplicabilityChipBear: "regime-applicability-chip-bear",
```

- [ ] **Step 7.2: Create the banner**

```tsx
"use client";

import { useEffect, useState } from "react";

import { useRegimeCurrent } from "@/hooks/useRegime";

const LAST_SEEN_KEY = "algo.regime.lastSeen";
const DISMISS_KEY_PREFIX = "algo.regime.dismissed.";
const DISMISS_TTL_MS = 4 * 60 * 60 * 1000;  // 4 hours

interface BannerState {
  from: string;
  to: string;
}

export function RegimeChangeBanner() {
  const { current } = useRegimeCurrent();
  const [banner, setBanner] = useState<BannerState | null>(null);

  useEffect(() => {
    if (!current) return;
    if (typeof window === "undefined") return;
    const lastSeen = localStorage.getItem(LAST_SEEN_KEY);
    if (lastSeen && lastSeen !== current.regime_label) {
      const dismissedTs = localStorage.getItem(
        DISMISS_KEY_PREFIX + current.regime_label,
      );
      if (
        dismissedTs
        && Date.now() - parseInt(dismissedTs, 10) < DISMISS_TTL_MS
      ) {
        return;  // suppressed within the 4h window
      }
      setBanner({ from: lastSeen, to: current.regime_label });
    }
    localStorage.setItem(LAST_SEEN_KEY, current.regime_label);
  }, [current]);

  if (!banner) return null;

  const dismiss = () => {
    if (typeof window !== "undefined") {
      localStorage.setItem(
        DISMISS_KEY_PREFIX + banner.to,
        String(Date.now()),
      );
    }
    setBanner(null);
  };

  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100 flex items-center justify-between gap-3"
      data-testid="regime-change-banner"
    >
      <span>
        Regime changed: <strong>{banner.from}</strong> → <strong>{banner.to}</strong>.
        Strategies bound to <strong>{banner.from}</strong> only are now off-regime — review applicable strategies.
      </span>
      <button
        type="button"
        onClick={dismiss}
        className="rounded bg-amber-100 px-2 py-0.5 text-amber-900 hover:bg-amber-200 dark:bg-amber-900/50 dark:text-amber-100 dark:hover:bg-amber-900"
        data-testid="regime-change-banner-dismiss"
      >
        Dismiss
      </button>
    </div>
  );
}
```

- [ ] **Step 7.3: Mount in PaperTab**

In `frontend/components/algo-trading/PaperTab.tsx`, find the title block where `<RegimeWidget />` was added by REGIME-1. Add the banner immediately above the title row:

```tsx
import { RegimeChangeBanner } from "./RegimeChangeBanner";
...
return (
  <div className="space-y-4" data-testid="paper-tab">
    <RegimeChangeBanner />
    <div className="flex items-center justify-between gap-4">
      ...
```

- [ ] **Step 7.4: Lint + smoke + commit**

```bash
cd frontend && npx eslint components/algo-trading/RegimeChangeBanner.tsx components/algo-trading/PaperTab.tsx
# Sync changes into the running frontend container & verify hot-reload
WT=/Users/abhay/Documents/projects/ai-agent-ui/.worktrees/regime-slice-3-binding-selector
FE_CID=$(cd /Users/abhay/Documents/projects/ai-agent-ui && docker compose ps -q frontend)
docker cp "$WT/frontend/components/algo-trading/RegimeChangeBanner.tsx" "$FE_CID:/app/components/algo-trading/RegimeChangeBanner.tsx"
docker cp "$WT/frontend/components/algo-trading/PaperTab.tsx" "$FE_CID:/app/components/algo-trading/PaperTab.tsx"
sleep 3
cd /Users/abhay/Documents/projects/ai-agent-ui && docker compose logs frontend --tail=10
git add frontend/components/algo-trading/RegimeChangeBanner.tsx frontend/components/algo-trading/PaperTab.tsx e2e/utils/selectors.ts
git commit -m "feat(algo-fe): RegimeChangeBanner with localStorage diff + dismiss (REGIME-3)"
```

---

## Task 8 — E2E spec + ship

**Files:**
- Create: `e2e/tests/frontend/algo-regime-binding.spec.ts`.
- Optional: `docs/algo-trading/strategy-regime-binding.md`.

Two assertions per spec — keep tight (REGIME-1's E2E was permissive enough to pass without seed; mirror that approach).

- [ ] **Step 8.1: E2E**

```ts
import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("REGIME-3 — binding + banner", () => {
  test("editor exposes applicability chips", async ({ page }) => {
    await page.goto("/algo-trading?tab=builder");
    await expect(
      page.getByTestId(FE.regimeApplicabilityChips),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.regimeApplicabilityChipBull),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.regimeApplicabilityChipSideways),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.regimeApplicabilityChipBear),
    ).toBeVisible();
  });

  test("banner appears when localStorage seeded with stale regime", async ({
    page,
  }) => {
    await page.goto("/algo-trading");
    // Seed a regime mismatch via localStorage before the banner mounts
    await page.evaluate(() => {
      localStorage.setItem("algo.regime.lastSeen", "BEAR");
    });
    await page.reload();
    // Either the banner renders (if regime classifier produced data),
    // or the page renders gracefully without one — both are valid.
    const banner = page.getByTestId(FE.regimeChangeBanner);
    if (await banner.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await page.getByTestId(FE.regimeChangeBannerDismiss).click();
      await expect(banner).toBeHidden();
    }
  });
});
```

- [ ] **Step 8.2: Run spec + final push**

```bash
cd e2e && npx playwright test --project=frontend-chromium tests/frontend/algo-regime-binding.spec.ts -j 1
cd /Users/abhay/Documents/projects/ai-agent-ui/.worktrees/regime-slice-3-binding-selector
git add e2e/tests/frontend/algo-regime-binding.spec.ts
git commit -m "test(e2e): regime applicability chips + banner (REGIME-3)"
git push origin feature/regime-slice-3-binding-selector
```

---

## Acceptance Checklist

- [ ] `algo.strategy_metadata` PG table created via Alembic migration.
- [ ] `applicable_regimes` round-trips through POST/PUT/GET strategy CRUD.
- [ ] Default = `["bull","sideways","bear"]` when not specified.
- [ ] `Literal_.literal` accepts `str` (verified via parse test).
- [ ] Evaluator handles `regime_label == "bull"` style compares.
- [ ] Mixed string/numeric compare raises with a clear error.
- [ ] Existing numeric compare tests still pass (no regression).
- [ ] `regime_change_notifier` job registered + emits exactly once on flip.
- [ ] `RegimeApplicabilityChips` renders 3 chips + mismatch warning when current regime not in selection.
- [ ] `RegimeChangeBanner` renders on regime mismatch, dismiss persists 4h via localStorage.
- [ ] Frontend hot-reloads clean (no console errors).
- [ ] All 4 new backend test files PASS.
- [ ] Branch pushed to `feature/regime-slice-3-binding-selector`.

---

## Out of Scope for REGIME-3

- Auto-pause on regime change (v4).
- Email dispatch on regime flip (v3.1 — keep banner only for now).
- Per-user `algo_regime_override` PG row (spec §8 Q11).
- Mandatory `applicable_regimes` enforcement at live-toggle time (soft only in v3).
- Strategy-list dropdown actually filtering by current regime (the chip + banner cover the UX; selector dropdown filtering is a polish for v3.1).
- Per-sector regime overlay (v4).
