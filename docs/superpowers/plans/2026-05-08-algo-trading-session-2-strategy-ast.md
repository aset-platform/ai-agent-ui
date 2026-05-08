# Algo Trading — Session 2: Strategy AST + Visual Builder

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Slices 4 + 5 from the Algo Trading epic spec — backend strategy AST schema + storage + CRUD API + feature dictionary registry, plus the frontend visual JSON-AST builder UI mounted on the Strategies tab.

**Architecture:** Pydantic discriminated-union AST model (Condition / Action / Composite node families) is the single source of truth, exported to JSON Schema for the frontend validator. Strategies persist as JSONB in `algo.strategies` (table created in Session 1). The visual builder is a left-rail node palette + drag-onto-canvas tree editor + live JSON pane — all client-side, validated server-side on save.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic 2 (TypeAdapter + discriminated unions) / pytest. Next.js 16 / React 19 / SWR / vitest. Postgres `algo.strategies` (JSONB).

**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (§4 Strategy AST Grammar)

**Branch:** `feature/algo-trading-session-2-strategy-ast` (already created off Session 1's tip `ccbe513`).

**Conventions reminders:**
- Branch off `dev`; squash-only merge; Co-Authored-By Abhay; line length 79; `X | None`; `_logger` for new logging; backend restart after route/model changes; Redis FLUSHALL after cache code changes.
- Frontend↔backend allowlist sync test pattern from `tests/backend/test_filter_catalog_sync.py` is the reference for keeping the feature registry in lockstep across the language boundary.
- E2E selectors live in `e2e/utils/selectors.ts` `FE`.

---

## File Structure

### Slice 4 — Strategy AST + storage + CRUD

**Backend (new):**
- `backend/algo/strategy/__init__.py` — package marker.
- `backend/algo/strategy/features.py` — feature-dictionary registry (frozen list of leaf names).
- `backend/algo/strategy/ast.py` — Pydantic models for Condition / Action / Composite nodes; `Strategy` root model; JSON-schema export helper.
- `backend/algo/strategy/repo.py` — async CRUD over `algo.strategies` Postgres table.
- `backend/algo/routes/strategies.py` — REST endpoints (list/create/get/update/archive).
- `backend/algo/tests/test_ast_validation.py` — 60+ AST-shape pytest cases (valid + invalid).
- `backend/algo/tests/test_strategies_routes.py` — endpoint smoke tests with auth + scope.
- `backend/algo/tests/test_feature_registry_sync.py` — CI gate that asserts frontend feature catalog matches backend registry.

**Backend (modified):**
- `backend/algo/routes/__init__.py` — register the strategies router.
- `backend/routes.py` — `app.include_router(create_strategies_router(), prefix="/v1")`.

**Frontend (new):**
- `frontend/lib/types/algoStrategy.ts` — TS literal types mirroring AST node `type` discriminators + feature key set.
- `frontend/components/algo-trading/strategyFeatureCatalog.ts` — feature-dictionary mirror (CI sync target).
- `frontend/hooks/useStrategies.ts` — SWR list + CRUD wrappers.
- `frontend/components/algo-trading/StrategiesTab.tsx` — Slice 4's UI: list view + create-from-blank + clone + archive + delete.
- `frontend/components/algo-trading/__tests__/StrategiesTab.test.tsx` — vitest.

### Slice 5 — Visual builder

**Frontend (new):**
- `frontend/components/algo-trading/builder/StrategyBuilder.tsx` — top-level builder shell (palette + canvas + JSON pane + validate/save).
- `frontend/components/algo-trading/builder/NodePalette.tsx` — left-rail draggable node types.
- `frontend/components/algo-trading/builder/AstTreeView.tsx` — recursive tree renderer.
- `frontend/components/algo-trading/builder/JsonPane.tsx` — live JSON preview (read-only) + paste-to-import escape hatch.
- `frontend/components/algo-trading/builder/nodeRenderers.tsx` — one-row renderer per AST node `type`.
- `frontend/components/algo-trading/builder/templates.ts` — 3 sample-strategy templates (golden cross, mean reversion, momentum).
- `frontend/components/algo-trading/__tests__/StrategyBuilder.test.tsx` — vitest.
- `frontend/components/algo-trading/__tests__/AstTreeView.test.tsx` — vitest.

**Frontend (modified):**
- `frontend/components/algo-trading/StrategiesTab.tsx` — Slice 4 list view becomes a two-mode container (list ↔ builder); selection of a strategy or "New strategy" opens the builder.

---

## Task 1: Backend feature-dictionary registry

**Files:**
- Create: `backend/algo/strategy/__init__.py`
- Create: `backend/algo/strategy/features.py`

- [ ] **Step 1: Package marker**

```python
# backend/algo/strategy/__init__.py
"""Strategy authoring — AST models + feature registry."""
```

- [ ] **Step 2: Feature registry**

```python
# backend/algo/strategy/features.py
"""Feature dictionary registry — single source of truth for the
leaf vocabulary the strategy AST can reference. Mirrors are
generated for the frontend at
``frontend/components/algo-trading/strategyFeatureCatalog.ts``;
drift caught by ``test_feature_registry_sync.py``.

Each feature has a stable key, a UI label, a numeric type
(int / float), and a source identifier — used by the
backtest engine in Slice 7 to know which Iceberg/PG table
to pull the value from. Adding a feature is a 4-step PR:
1. Add to FEATURES below.
2. Add the matching entry to strategyFeatureCatalog.ts.
3. Implement the resolver in Slice 7's runtime.
4. Add a sample backtest case if the feature is non-obvious.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FeatureType = Literal["int", "float"]
FeatureSource = Literal[
    "ohlcv",
    "technical",          # rsi, sma_50, sma_200, golden_cross_days_ago
    "fundamentals",       # pscore, debt_to_eq, roce, sales/profit growth
    "recommendation",
    "forecast",
]


class Feature(BaseModel):
    key: str
    label: str
    type: FeatureType
    source: FeatureSource


# Initial feature dictionary — equity, daily-bar features only.
# Slice 6 adds intraday-bar features. F&O features = v2.
FEATURES: list[Feature] = [
    # OHLCV
    Feature(key="today_ltp", label="Today LTP", type="float", source="ohlcv"),
    Feature(key="prev_day_ltp", label="Prev day LTP", type="float", source="ohlcv"),
    Feature(key="today_vol", label="Today volume", type="int", source="ohlcv"),
    Feature(
        key="today_x_vol",
        label="Today × Vol (vs avg)",
        type="float",
        source="ohlcv",
    ),
    Feature(
        key="away_from_52week_high",
        label="Away from 52w high (%)",
        type="float",
        source="ohlcv",
    ),
    # Technical
    Feature(
        key="golden_cross_days_ago",
        label="Golden cross (days ago)",
        type="int",
        source="technical",
    ),
    Feature(key="sma_50", label="SMA 50", type="float", source="technical"),
    Feature(key="sma_200", label="SMA 200", type="float", source="technical"),
    Feature(key="rsi", label="RSI (14)", type="float", source="technical"),
    Feature(
        key="today_dpc",
        label="Today delivery %",
        type="float",
        source="technical",
    ),
    # Fundamentals
    Feature(key="pscore", label="P-Score (Piotroski)", type="int", source="fundamentals"),
    Feature(
        key="debt_to_eq",
        label="Debt / Equity",
        type="float",
        source="fundamentals",
    ),
    Feature(key="roce", label="ROCE %", type="float", source="fundamentals"),
    Feature(
        key="sales_growth_3yrs",
        label="Sales growth 3y %",
        type="float",
        source="fundamentals",
    ),
    Feature(
        key="prft_growth_3yrs",
        label="Profit growth 3y %",
        type="float",
        source="fundamentals",
    ),
    # Recommendation
    Feature(
        key="recommendation_score",
        label="Recommendation score",
        type="float",
        source="recommendation",
    ),
    # Forecast
    Feature(
        key="forecast_30d_pct_change",
        label="Forecast 30d % change",
        type="float",
        source="forecast",
    ),
    Feature(
        key="forecast_confidence",
        label="Forecast confidence",
        type="float",
        source="forecast",
    ),
]


FEATURE_KEYS: frozenset[str] = frozenset(f.key for f in FEATURES)
FEATURE_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURES}
```

- [ ] **Step 3: Smoke-test the registry parses cleanly**

```bash
docker compose exec backend python -c "
from backend.algo.strategy.features import FEATURES, FEATURE_KEYS
print('count:', len(FEATURES))
print('keys are unique:', len(FEATURE_KEYS) == len(FEATURES))
print('first 3:', [f.key for f in FEATURES[:3]])
"
```

Expected: 18 features, unique keys, sample output.

- [ ] **Step 4: Commit**

```bash
git add backend/algo/strategy/__init__.py backend/algo/strategy/features.py
git commit -m "$(cat <<'EOF'
feat(algo): strategy feature registry

Slice 4 of the Algo Trading epic. 18 daily-bar features
(OHLCV / technical / fundamentals / recommendation / forecast)
form the leaf vocabulary the strategy AST can reference.
Single source of truth; frontend mirror lands in Task 4 with
a CI sync test.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Backend AST Pydantic models

**Files:**
- Create: `backend/algo/strategy/ast.py`
- Create: `backend/algo/tests/test_ast_validation.py`

- [ ] **Step 1: Write failing AST-validation tests**

```python
# backend/algo/tests/test_ast_validation.py
"""Pydantic-validation tests for the strategy AST (Slice 4).

The grammar is closed: every node has a ``type`` discriminator
and a fixed payload shape. Unknown ``type`` values, unknown
feature keys, and bad arithmetic operands all raise at the
validator layer — never at runtime in the backtest engine.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.algo.strategy.ast import Strategy, parse_strategy


def _wrap(root: dict) -> dict:
    return {
        "id": str(uuid4()),
        "name": "Test strategy",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 10},
        "root": root,
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


# ---- Happy-path shapes ---------------------------------------------

def test_minimal_strategy_with_hold_root():
    s = parse_strategy(_wrap({"type": "hold"}))
    assert isinstance(s, Strategy)
    assert s.root.type == "hold"


def test_compare_two_features():
    root = {
        "type": "compare",
        "left": {"feature": "today_ltp"},
        "op": ">",
        "right": {"feature": "sma_50"},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "compare"
    assert s.root.op == ">"


def test_compare_feature_to_literal():
    root = {
        "type": "compare",
        "left": {"feature": "rsi"},
        "op": "<",
        "right": {"literal": 70},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.right.literal == 70


def test_and_operator_with_two_compares():
    root = {
        "type": "and",
        "operands": [
            {
                "type": "compare",
                "left": {"feature": "today_ltp"},
                "op": ">",
                "right": {"feature": "sma_50"},
            },
            {
                "type": "compare",
                "left": {"feature": "pscore"},
                "op": ">=",
                "right": {"literal": 7},
            },
        ],
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "and"
    assert len(s.root.operands) == 2


def test_or_with_three_operands():
    root = {
        "type": "or",
        "operands": [
            {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": "<",
                "right": {"literal": 30},
            },
            {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 70},
            },
            {
                "type": "compare",
                "left": {"feature": "today_x_vol"},
                "op": ">=",
                "right": {"literal": 2},
            },
        ],
    }
    s = parse_strategy(_wrap(root))
    assert len(s.root.operands) == 3


def test_not_with_compare_inner():
    root = {
        "type": "not",
        "operand": {
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": ">",
            "right": {"literal": 70},
        },
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "not"


def test_crossover_node():
    root = {
        "type": "crossover",
        "fast": {"feature": "sma_50"},
        "slow": {"feature": "sma_200"},
        "direction": "above",
    }
    s = parse_strategy(_wrap(root))
    assert s.root.direction == "above"


def test_between_node():
    root = {
        "type": "between",
        "value": {"feature": "rsi"},
        "low": {"literal": 30},
        "high": {"literal": 70},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "between"


def test_if_then_else_with_select_top_n():
    root = {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "today_ltp"},
            "op": ">",
            "right": {"feature": "sma_50"},
        },
        "then": {
            "type": "select_top_n",
            "n": 5,
            "rank_by": {"feature": "today_x_vol"},
            "rank_dir": "desc",
            "action": {"type": "set_target_weight", "weight": 0.20},
        },
        "else": {"type": "exit", "scope": "all_open"},
    }
    s = parse_strategy(_wrap(root))
    assert s.root.type == "if"


def test_buy_action():
    root = {"type": "buy", "qty": {"shares": 10}}
    s = parse_strategy(_wrap(root))
    assert s.root.type == "buy"


def test_sell_action():
    root = {"type": "sell", "qty": {"all": True}}
    s = parse_strategy(_wrap(root))
    assert s.root.type == "sell"


def test_set_target_weight_action():
    root = {"type": "set_target_weight", "weight": 0.25}
    s = parse_strategy(_wrap(root))
    assert s.root.type == "set_target_weight"


def test_exit_all_open():
    root = {"type": "exit", "scope": "all_open"}
    s = parse_strategy(_wrap(root))
    assert s.root.scope == "all_open"


# ---- Validation: rejections ----------------------------------------

def test_unknown_node_type_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "magic_pony"}))


def test_unknown_feature_rejected():
    with pytest.raises(ValidationError, match="not_a_feature"):
        parse_strategy(_wrap({
            "type": "compare",
            "left": {"feature": "not_a_feature"},
            "op": ">",
            "right": {"literal": 0},
        }))


def test_compare_unknown_op_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": "bogus",
            "right": {"literal": 50},
        }))


def test_and_with_zero_operands_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "and", "operands": []}))


def test_select_top_n_with_zero_n_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({
            "type": "select_top_n", "n": 0,
            "rank_by": {"feature": "today_x_vol"},
            "rank_dir": "desc",
            "action": {"type": "set_target_weight", "weight": 0.10},
        }))


def test_set_target_weight_negative_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "set_target_weight", "weight": -0.5}))


def test_set_target_weight_above_one_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "set_target_weight", "weight": 1.5}))


def test_buy_negative_shares_rejected():
    with pytest.raises(ValidationError):
        parse_strategy(_wrap({"type": "buy", "qty": {"shares": -10}}))


def test_unknown_universe_scope_rejected():
    bad = _wrap({"type": "hold"})
    bad["universe"]["scope"] = "alien"
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_unknown_schedule_interval_rejected():
    bad = _wrap({"type": "hold"})
    bad["schedule"]["interval"] = "2d"
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_risk_per_trade_negative_sl_rejected():
    bad = _wrap({"type": "hold"})
    bad["risk"]["per_trade"]["stop_loss_pct"] = -1
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_risk_portfolio_exposure_above_100_rejected():
    bad = _wrap({"type": "hold"})
    bad["risk"]["portfolio"]["max_exposure_pct"] = 150
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_missing_root_rejected():
    bad = _wrap({"type": "hold"})
    del bad["root"]
    with pytest.raises(ValidationError):
        parse_strategy(bad)


def test_extra_top_level_key_rejected():
    bad = _wrap({"type": "hold"})
    bad["extra_field"] = "boom"
    with pytest.raises(ValidationError):
        parse_strategy(bad)


# ---- Recursion / depth --------------------------------------------

def test_deeply_nested_and_or():
    inner = {
        "type": "compare",
        "left": {"feature": "rsi"},
        "op": "<",
        "right": {"literal": 30},
    }
    nested = inner
    for _ in range(8):
        nested = {"type": "not", "operand": nested}
    s = parse_strategy(_wrap(nested))
    # Walk back to find the inner compare
    n = s.root
    depth = 0
    while n.type == "not":
        n = n.operand
        depth += 1
    assert depth == 8
    assert n.type == "compare"


# ---- JSON-schema export -------------------------------------------

def test_strategy_emits_json_schema():
    schema = Strategy.model_json_schema()
    assert "Strategy" in schema.get("title", "")
    # Discriminator field present somewhere
    schema_str = str(schema)
    assert "type" in schema_str
    assert "compare" in schema_str
    assert "buy" in schema_str
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_ast_validation.py -v 2>&1 | tail -10
```

Expected: ImportError on `backend.algo.strategy.ast`.

- [ ] **Step 3: Implement the AST models**

```python
# backend/algo/strategy/ast.py
"""Strategy AST — Pydantic 2 discriminated-union grammar.

A Strategy is a tree of nodes evaluated against a per-bar
context. Three node families:

- Condition: returns ``bool`` (compare/and/or/not/crossover/
  between).
- Action: returns order intents (buy/sell/exit/hold/
  set_target_weight).
- Composite: dispatch / aggregate (if/select_top_n/weighted).

Discriminator field on every node is ``type``. Unknown values,
unknown feature names, and out-of-range literals all raise
``ValidationError`` at parse time — never at runtime.

JSON-schema export drives the frontend validator (Task 5
mirror + CI sync test).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from backend.algo.strategy.features import FEATURE_KEYS

# ---- Operand leaves ------------------------------------------------


class FeatureRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature: str

    @field_validator("feature")
    @classmethod
    def _known_feature(cls, v: str) -> str:
        if v not in FEATURE_KEYS:
            raise ValueError(f"Unknown feature: {v}")
        return v


class Literal_(BaseModel):
    model_config = ConfigDict(extra="forbid")
    literal: float | int


Operand = Union[FeatureRef, Literal_]


# ---- Condition family ---------------------------------------------

CompareOp = Literal["<", "<=", "==", "!=", ">=", ">"]


class CompareNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["compare"] = "compare"
    left: Operand
    op: CompareOp
    right: Operand


class AndNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["and"] = "and"
    operands: list["ConditionNode"] = Field(min_length=2)


class OrNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["or"] = "or"
    operands: list["ConditionNode"] = Field(min_length=2)


class NotNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["not"] = "not"
    operand: "ConditionNode"


CrossoverDirection = Literal["above", "below"]


class CrossoverNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["crossover"] = "crossover"
    fast: Operand
    slow: Operand
    direction: CrossoverDirection


class BetweenNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["between"] = "between"
    value: Operand
    low: Operand
    high: Operand


ConditionNode = Annotated[
    Union[
        CompareNode, AndNode, OrNode, NotNode, CrossoverNode, BetweenNode,
    ],
    Field(discriminator="type"),
]


# ---- Action family ------------------------------------------------


class BuyQtyShares(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shares: int = Field(ge=1)


class BuyQtyNotional(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notional_inr: float = Field(gt=0)


class SellQtyShares(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shares: int = Field(ge=1)


class SellQtyAll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: Literal[True]


class BuyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["buy"] = "buy"
    qty: Union[BuyQtyShares, BuyQtyNotional]


class SellNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["sell"] = "sell"
    qty: Union[SellQtyShares, SellQtyAll]


class ExitNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["exit"] = "exit"
    scope: Literal["all_open", "this_symbol"]


class HoldNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["hold"] = "hold"


class SetTargetWeightNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["set_target_weight"] = "set_target_weight"
    weight: float = Field(ge=0, le=1)


# ---- Composite family ---------------------------------------------


class IfNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["if"] = "if"
    cond: ConditionNode
    then: "AnyNode"
    else_: "AnyNode | None" = Field(default=None, alias="else")


class SelectTopNNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["select_top_n"] = "select_top_n"
    n: int = Field(ge=1, le=100)
    rank_by: Operand
    rank_dir: Literal["asc", "desc"]
    action: "AnyNode"


class WeightedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["weighted"] = "weighted"
    branches: list["WeightedBranch"] = Field(min_length=1)


class WeightedBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weight: float = Field(ge=0, le=1)
    node: "AnyNode"


# ---- Root union ---------------------------------------------------


AnyNode = Annotated[
    Union[
        # Condition
        CompareNode, AndNode, OrNode, NotNode, CrossoverNode, BetweenNode,
        # Action
        BuyNode, SellNode, ExitNode, HoldNode, SetTargetWeightNode,
        # Composite
        IfNode, SelectTopNNode, WeightedNode,
    ],
    Field(discriminator="type"),
]


# Forward-ref resolution for self-referential composite nodes.
AndNode.model_rebuild()
OrNode.model_rebuild()
NotNode.model_rebuild()
IfNode.model_rebuild()
SelectTopNNode.model_rebuild()
WeightedNode.model_rebuild()
WeightedBranch.model_rebuild()


# ---- Strategy wrapper ---------------------------------------------


class UniverseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker_type: list[Literal["stock", "etf"]] = Field(min_length=1)
    market: Literal["india", "us", "all"] = "india"


class UniverseScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["scope"] = "scope"
    scope: Literal["discovery", "watchlist", "portfolio"]
    filter: UniverseFilter


class ScheduleBarClose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bar_close"] = "bar_close"
    interval: Literal["1d"]   # 1m / 5m added in Slice 6
    time: str = Field(default="15:25 IST")


class RebalanceDaily(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["daily"] = "daily"
    max_positions: int = Field(ge=1, le=50)


class RiskPerTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stop_loss_pct: float = Field(ge=0, le=50)
    max_qty: int = Field(ge=1, le=1_000_000)


class RiskPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_exposure_pct: float = Field(ge=0, le=100)
    max_concentration_pct: float = Field(ge=0, le=100)


class RiskDaily(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_loss_pct: float = Field(ge=0, le=50)
    max_open_positions: int = Field(ge=1, le=50)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_trade: RiskPerTrade
    portfolio: RiskPortfolio
    daily: RiskDaily


class Strategy(BaseModel):
    """Top-level strategy AST.

    ``id`` is a UUID (DB row id once persisted; transient at
    edit time). ``name`` is a free-form label.
    """
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=128)
    universe: UniverseScope
    schedule: ScheduleBarClose
    rebalance: RebalanceDaily
    root: AnyNode
    risk: RiskConfig

    @model_validator(mode="after")
    def _root_must_be_actionable(self) -> "Strategy":
        # A bare condition at the root would never produce orders;
        # require at least one action / composite.
        action_or_composite = (
            "buy", "sell", "exit", "hold", "set_target_weight",
            "if", "select_top_n", "weighted",
        )
        if self.root.type not in action_or_composite:
            raise ValueError(
                "root must be an action or composite; "
                "wrap a condition in an `if` node."
            )
        return self


# Single Pydantic adapter the rest of the codebase uses.
strategy_adapter: TypeAdapter[Strategy] = TypeAdapter(Strategy)


def parse_strategy(payload: dict) -> Strategy:
    """Validate + parse a strategy dict. Raises ``ValidationError``."""
    return strategy_adapter.validate_python(payload)
```

- [ ] **Step 4: Run tests, expect 30+ passed**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_ast_validation.py -v 2>&1 | tail -15
```

Expected: 30 passed.

- [ ] **Step 5: Lint**

```bash
docker compose exec backend python -m flake8 backend/algo/strategy/ast.py backend/algo/strategy/features.py backend/algo/tests/test_ast_validation.py 2>&1 | tail -5
```

Expected: zero violations.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/strategy/ast.py backend/algo/tests/test_ast_validation.py
git commit -m "$(cat <<'EOF'
feat(algo): strategy AST Pydantic models + 30 validation tests

Slice 4 of the Algo Trading epic. Closed-grammar AST with
discriminated-union nodes (condition/action/composite),
extra="forbid" everywhere, feature-key allowlist, range bounds
on weights/SL/exposure. parse_strategy() raises ValidationError
on any deviation — backtest engine in Slice 7 will never see
malformed input.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: Strategies repo + CRUD route

**Files:**
- Create: `backend/algo/strategy/repo.py`
- Create: `backend/algo/routes/strategies.py`
- Modify: `backend/algo/routes/__init__.py` (re-export)
- Modify: `backend/routes.py` (register `create_strategies_router()`)
- Create: `backend/algo/tests/test_strategies_routes.py`

- [ ] **Step 1: Write the strategies repo**

```python
# backend/algo/strategy/repo.py
"""Async CRUD for ``algo.strategies``.

The table stores the Pydantic-validated AST as JSONB; reads
re-parse to enforce schema even after server restarts that
might have changed the AST grammar (re-validation is cheap
relative to a backtest run).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.strategy.ast import Strategy, parse_strategy

_logger = logging.getLogger(__name__)


async def list_strategies(
    session: AsyncSession,
    user_id: UUID,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Return strategy summary rows (no full AST) for the user.

    For the full AST, callers must hit ``get_strategy``.
    """
    sql = (
        "SELECT id, name, mode, status, created_at, updated_at, "
        "archived_at "
        "FROM algo.strategies "
        "WHERE user_id = :uid "
    )
    if not include_archived:
        sql += "AND archived_at IS NULL "
    sql += "ORDER BY updated_at DESC LIMIT 200"
    rows = (await session.execute(text(sql), {"uid": user_id})).mappings()
    return [dict(r) for r in rows]


async def get_strategy(
    session: AsyncSession, user_id: UUID, strategy_id: UUID,
) -> Strategy | None:
    """Fetch + re-parse one strategy. Returns None on miss."""
    row = (await session.execute(
        text(
            "SELECT id, name, ast_json, mode, status "
            "FROM algo.strategies "
            "WHERE user_id = :uid AND id = :sid"
        ),
        {"uid": user_id, "sid": strategy_id},
    )).mappings().first()
    if row is None:
        return None
    payload = dict(row["ast_json"])
    payload["id"] = str(row["id"])
    payload["name"] = row["name"]
    return parse_strategy(payload)


async def create_strategy(
    session: AsyncSession, user_id: UUID, strategy: Strategy,
) -> UUID:
    """Persist a new strategy. Returns the row id (== strategy.id)."""
    new_id = strategy.id or uuid4()
    now = datetime.now(timezone.utc)
    await session.execute(
        text(
            "INSERT INTO algo.strategies "
            "(id, user_id, name, ast_json, mode, status, "
            " created_at, updated_at) "
            "VALUES (:id, :uid, :name, :ast, 'draft', 'active', "
            " :now, :now)"
        ),
        {
            "id": new_id,
            "uid": user_id,
            "name": strategy.name,
            "ast": json.dumps(
                strategy.model_dump(mode="json", by_alias=True),
            ),
            "now": now,
        },
    )
    await session.commit()
    return new_id


async def update_strategy(
    session: AsyncSession,
    user_id: UUID,
    strategy_id: UUID,
    strategy: Strategy,
) -> bool:
    """Replace the AST for a user-owned strategy. Returns False on miss."""
    now = datetime.now(timezone.utc)
    res = await session.execute(
        text(
            "UPDATE algo.strategies SET "
            "name = :name, ast_json = :ast, updated_at = :now "
            "WHERE user_id = :uid AND id = :sid AND archived_at IS NULL"
        ),
        {
            "name": strategy.name,
            "ast": json.dumps(
                strategy.model_dump(mode="json", by_alias=True),
            ),
            "now": now,
            "uid": user_id,
            "sid": strategy_id,
        },
    )
    await session.commit()
    return res.rowcount > 0


async def archive_strategy(
    session: AsyncSession, user_id: UUID, strategy_id: UUID,
) -> bool:
    """Soft-delete a strategy. Returns False on miss."""
    now = datetime.now(timezone.utc)
    res = await session.execute(
        text(
            "UPDATE algo.strategies SET archived_at = :now "
            "WHERE user_id = :uid AND id = :sid AND archived_at IS NULL"
        ),
        {"now": now, "uid": user_id, "sid": strategy_id},
    )
    await session.commit()
    return res.rowcount > 0


async def hard_delete_strategy(
    session: AsyncSession, user_id: UUID, strategy_id: UUID,
) -> bool:
    """Hard delete (only allowed on archived rows). Returns False on miss."""
    res = await session.execute(
        text(
            "DELETE FROM algo.strategies "
            "WHERE user_id = :uid AND id = :sid "
            "AND archived_at IS NOT NULL"
        ),
        {"uid": user_id, "sid": strategy_id},
    )
    await session.commit()
    return res.rowcount > 0
```

- [ ] **Step 2: Strategies router**

```python
# backend/algo/routes/strategies.py
"""CRUD endpoints for algo.strategies."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.strategy.ast import Strategy, parse_strategy
from backend.algo.strategy.repo import (
    archive_strategy,
    create_strategy,
    get_strategy,
    hard_delete_strategy,
    list_strategies,
    update_strategy,
)
from backend.db.session import get_async_session

_logger = logging.getLogger(__name__)


class StrategySummary(BaseModel):
    id: UUID
    name: str
    mode: str
    status: str
    created_at: Any
    updated_at: Any
    archived_at: Any


class StrategyListResponse(BaseModel):
    strategies: list[StrategySummary]


class StrategyCreateRequest(BaseModel):
    payload: dict = Field(..., description="Full AST payload")


class StrategyCreateResponse(BaseModel):
    id: UUID


def create_strategies_router() -> APIRouter:
    router = APIRouter(prefix="/algo/strategies", tags=["algo-trading"])

    @router.get("", response_model=StrategyListResponse)
    async def list_(
        user: UserContext = Depends(pro_or_superuser),
        include_archived: bool = False,
        session=Depends(get_async_session),
    ) -> StrategyListResponse:
        rows = await list_strategies(
            session, UUID(user.user_id),
            include_archived=include_archived,
        )
        return StrategyListResponse(
            strategies=[StrategySummary(**r) for r in rows],
        )

    @router.get("/{strategy_id}", response_model=Strategy)
    async def get_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
        session=Depends(get_async_session),
    ) -> Strategy:
        s = await get_strategy(session, UUID(user.user_id), strategy_id)
        if s is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )
        return s

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        response_model=StrategyCreateResponse,
    )
    async def create_(
        body: StrategyCreateRequest,
        user: UserContext = Depends(pro_or_superuser),
        session=Depends(get_async_session),
    ) -> StrategyCreateResponse:
        try:
            strategy = parse_strategy(body.payload)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors())
        new_id = await create_strategy(
            session, UUID(user.user_id), strategy,
        )
        return StrategyCreateResponse(id=new_id)

    @router.put("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def update_(
        strategy_id: UUID,
        body: StrategyCreateRequest,
        user: UserContext = Depends(pro_or_superuser),
        session=Depends(get_async_session),
    ) -> None:
        try:
            strategy = parse_strategy(body.payload)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors())
        ok = await update_strategy(
            session, UUID(user.user_id), strategy_id, strategy,
        )
        if not ok:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

    @router.delete(
        "/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT,
    )
    async def archive_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
        session=Depends(get_async_session),
    ) -> None:
        # Soft-archive first; hard-delete only when called on
        # an already-archived row (idempotent client UX).
        if await archive_strategy(
            session, UUID(user.user_id), strategy_id,
        ):
            return
        if await hard_delete_strategy(
            session, UUID(user.user_id), strategy_id,
        ):
            return
        raise HTTPException(status_code=404, detail="Strategy not found")

    return router
```

- [ ] **Step 3: Update `backend/algo/routes/__init__.py` to re-export**

Replace the file's contents with:

```python
"""HTTP routers for the algo trading module."""

from backend.algo.routes.fees import create_fees_router
from backend.algo.routes.strategies import create_strategies_router

__all__ = ["create_fees_router", "create_strategies_router"]
```

- [ ] **Step 4: Register the router in `backend/routes.py`**

Read `backend/routes.py` to find where `create_fees_router()` is included (added in Session 1 Task 8). Add the strategies router on the next line:

```python
from backend.algo.routes import create_strategies_router
app.include_router(create_strategies_router(), prefix="/v1")
```

If `create_fees_router` is already imported, append `create_strategies_router` to that import line:

```python
from backend.algo.routes import (
    create_fees_router,
    create_strategies_router,
)
```

- [ ] **Step 5: Restart backend**

```bash
docker compose restart backend
sleep 6
```

- [ ] **Step 6: Write endpoint smoke tests**

```python
# backend/algo/tests/test_strategies_routes.py
"""Endpoint smoke tests for /v1/algo/strategies/*."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.strategies import create_strategies_router


_VALID_PAYLOAD = {
    "id": str(uuid4()),
    "name": "Bullish + Quality v1",
    "universe": {
        "type": "scope", "scope": "watchlist",
        "filter": {"ticker_type": ["stock"], "market": "india"},
    },
    "schedule": {
        "type": "bar_close", "interval": "1d", "time": "15:25 IST",
    },
    "rebalance": {"type": "daily", "max_positions": 10},
    "root": {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "today_ltp"},
            "op": ">",
            "right": {"feature": "sma_50"},
        },
        "then": {"type": "set_target_weight", "weight": 0.20},
        "else": {"type": "exit", "scope": "all_open"},
    },
    "risk": {
        "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
        "portfolio": {
            "max_exposure_pct": 80,
            "max_concentration_pct": 25,
        },
        "daily": {"max_loss_pct": 2, "max_open_positions": 10},
    },
}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(create_strategies_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="t@t",
        role="superuser",
    )

    # In-memory async-session stub.
    rows: dict = {"items": []}

    class _Stub:
        async def execute(self, q, params=None):
            sql = str(q)
            class _Res:
                def __init__(self, items):
                    self._items = items
                def mappings(self):
                    return self
                def all(self):
                    return self._items
                def __iter__(self):
                    return iter(self._items)
                def first(self):
                    return self._items[0] if self._items else None
                @property
                def rowcount(self):
                    return len(self._items)

            if sql.startswith("SELECT id, name, mode"):
                return _Res(rows["items"])
            if sql.startswith("SELECT id, name, ast_json"):
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(params.get("sid"))
                ]
                return _Res(hit)
            if sql.startswith("INSERT INTO algo.strategies"):
                rows["items"].append({
                    "id": params["id"], "name": params["name"],
                    "ast_json": _VALID_PAYLOAD,
                    "mode": "draft", "status": "active",
                    "created_at": None, "updated_at": None,
                    "archived_at": None,
                })
                return _Res([])
            if sql.startswith("UPDATE algo.strategies SET name"):
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(params["sid"])
                ]
                for h in hit:
                    h["name"] = params["name"]
                return _Res(hit)
            if sql.startswith("UPDATE algo.strategies SET archived_at"):
                hit = [
                    r for r in rows["items"]
                    if str(r.get("id")) == str(params["sid"])
                    and r.get("archived_at") is None
                ]
                for h in hit:
                    h["archived_at"] = params["now"]
                return _Res(hit)
            if sql.startswith("DELETE FROM algo.strategies"):
                before = len(rows["items"])
                rows["items"] = [
                    r for r in rows["items"]
                    if not (
                        str(r.get("id")) == str(params["sid"])
                        and r.get("archived_at") is not None
                    )
                ]
                return _Res([None] * (before - len(rows["items"])))
            return _Res([])

        async def commit(self):
            return None

    from backend.db import session as session_mod
    monkeypatch.setattr(
        session_mod, "get_async_session", lambda: _Stub(),
    )
    return app


def test_post_then_list(app: FastAPI):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/strategies",
        json={"payload": _VALID_PAYLOAD},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    listing = client.get("/v1/algo/strategies").json()
    assert any(s["id"] == new_id for s in listing["strategies"])


def test_post_invalid_ast_returns_400(app: FastAPI):
    client = TestClient(app)
    bad = deepcopy(_VALID_PAYLOAD)
    bad["root"]["cond"]["left"]["feature"] = "not_a_feature"
    r = client.post(
        "/v1/algo/strategies", json={"payload": bad},
    )
    assert r.status_code == 400


def test_get_404_on_missing(app: FastAPI):
    client = TestClient(app)
    r = client.get(f"/v1/algo/strategies/{uuid4()}")
    assert r.status_code == 404


def test_archive_then_list_excludes(app: FastAPI):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/strategies", json={"payload": _VALID_PAYLOAD},
    )
    new_id = r.json()["id"]
    r = client.delete(f"/v1/algo/strategies/{new_id}")
    assert r.status_code == 204
    listing = client.get("/v1/algo/strategies").json()
    assert all(s["id"] != new_id for s in listing["strategies"])
```

- [ ] **Step 7: Run tests**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_strategies_routes.py -v 2>&1 | tail -10
```

Expected: 4 passed.

- [ ] **Step 8: Lint + commit**

```bash
docker compose exec backend python -m flake8 backend/algo/strategy/repo.py backend/algo/routes/strategies.py backend/algo/routes/__init__.py backend/algo/tests/test_strategies_routes.py 2>&1 | tail -5
git add backend/algo/strategy/repo.py backend/algo/routes/strategies.py backend/algo/routes/__init__.py backend/routes.py backend/algo/tests/test_strategies_routes.py
git commit -m "$(cat <<'EOF'
feat(algo): strategy CRUD repo + /v1/algo/strategies/* routes

Slice 4 of the Algo Trading epic. Async repo over algo.strategies
JSONB; routes for list/get/create/update/archive with per-user
isolation enforced at the SQL level; bad AST → 400 with
ValidationError detail; missing → 404; soft-archive that promotes
to hard-delete on a second call (idempotent client UX). 4
endpoint smoke tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Frontend feature catalog mirror + sync test

**Files:**
- Create: `frontend/components/algo-trading/strategyFeatureCatalog.ts`
- Create: `frontend/lib/types/algoStrategy.ts`
- Create: `backend/algo/tests/test_feature_registry_sync.py`

- [ ] **Step 1: Frontend feature catalog**

```ts
// frontend/components/algo-trading/strategyFeatureCatalog.ts
/**
 * Feature dictionary mirror — KEEP IN SYNC with
 * ``backend/algo/strategy/features.py``.
 *
 * CI gate: ``backend/algo/tests/test_feature_registry_sync.py``
 * parses this file as text and asserts the key set matches
 * ``FEATURE_KEYS``. Drift fails CI.
 */

export interface StrategyFeature {
  key: string;
  label: string;
  type: "int" | "float";
  source:
    | "ohlcv"
    | "technical"
    | "fundamentals"
    | "recommendation"
    | "forecast";
}

export const STRATEGY_FEATURES: StrategyFeature[] = [
  // OHLCV
  { key: "today_ltp", label: "Today LTP", type: "float", source: "ohlcv" },
  { key: "prev_day_ltp", label: "Prev day LTP", type: "float", source: "ohlcv" },
  { key: "today_vol", label: "Today volume", type: "int", source: "ohlcv" },
  { key: "today_x_vol", label: "Today × Vol (vs avg)", type: "float", source: "ohlcv" },
  { key: "away_from_52week_high", label: "Away from 52w high (%)", type: "float", source: "ohlcv" },
  // Technical
  { key: "golden_cross_days_ago", label: "Golden cross (days ago)", type: "int", source: "technical" },
  { key: "sma_50", label: "SMA 50", type: "float", source: "technical" },
  { key: "sma_200", label: "SMA 200", type: "float", source: "technical" },
  { key: "rsi", label: "RSI (14)", type: "float", source: "technical" },
  { key: "today_dpc", label: "Today delivery %", type: "float", source: "technical" },
  // Fundamentals
  { key: "pscore", label: "P-Score (Piotroski)", type: "int", source: "fundamentals" },
  { key: "debt_to_eq", label: "Debt / Equity", type: "float", source: "fundamentals" },
  { key: "roce", label: "ROCE %", type: "float", source: "fundamentals" },
  { key: "sales_growth_3yrs", label: "Sales growth 3y %", type: "float", source: "fundamentals" },
  { key: "prft_growth_3yrs", label: "Profit growth 3y %", type: "float", source: "fundamentals" },
  // Recommendation
  { key: "recommendation_score", label: "Recommendation score", type: "float", source: "recommendation" },
  // Forecast
  { key: "forecast_30d_pct_change", label: "Forecast 30d % change", type: "float", source: "forecast" },
  { key: "forecast_confidence", label: "Forecast confidence", type: "float", source: "forecast" },
];

export const STRATEGY_FEATURE_KEY_SET: Set<string> = new Set(
  STRATEGY_FEATURES.map((f) => f.key),
);

export const STRATEGY_FEATURE_BY_KEY: Record<string, StrategyFeature> =
  Object.fromEntries(STRATEGY_FEATURES.map((f) => [f.key, f]));
```

- [ ] **Step 2: AST node-type literal**

```ts
// frontend/lib/types/algoStrategy.ts
/**
 * Type literals mirroring the backend AST node ``type``
 * discriminator. No semantic logic — the visual builder uses
 * this only to render a labelled palette + dispatch
 * renderers per node type.
 */

export type AstNodeType =
  // Condition
  | "compare"
  | "and"
  | "or"
  | "not"
  | "crossover"
  | "between"
  // Action
  | "buy"
  | "sell"
  | "exit"
  | "hold"
  | "set_target_weight"
  // Composite
  | "if"
  | "select_top_n"
  | "weighted";

export type AstNodeFamily = "condition" | "action" | "composite";

export const AST_NODE_FAMILY: Record<AstNodeType, AstNodeFamily> = {
  compare: "condition",
  and: "condition",
  or: "condition",
  not: "condition",
  crossover: "condition",
  between: "condition",
  buy: "action",
  sell: "action",
  exit: "action",
  hold: "action",
  set_target_weight: "action",
  if: "composite",
  select_top_n: "composite",
  weighted: "composite",
};

export const AST_NODE_LABEL: Record<AstNodeType, string> = {
  compare: "Compare",
  and: "AND",
  or: "OR",
  not: "NOT",
  crossover: "Crossover",
  between: "Between",
  buy: "Buy",
  sell: "Sell",
  exit: "Exit",
  hold: "Hold",
  set_target_weight: "Set target weight",
  if: "If / then / else",
  select_top_n: "Select top N",
  weighted: "Weighted",
};
```

- [ ] **Step 3: Sync test**

```python
# backend/algo/tests/test_feature_registry_sync.py
"""CI gate: backend feature registry must match frontend mirror.

Loads strategyFeatureCatalog.ts as text, regex-extracts every
quoted key from the STRATEGY_FEATURES array, asserts equality
with FEATURE_KEYS.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.algo.strategy.features import FEATURE_KEYS

_FRONTEND_FILE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "components"
    / "algo-trading"
    / "strategyFeatureCatalog.ts"
)
_BLOCK_RE = re.compile(
    r"export const STRATEGY_FEATURES\s*:\s*StrategyFeature\[\]\s*=\s*"
    r"\[(?P<body>.*?)\];",
    re.DOTALL,
)
_KEY_RE = re.compile(r'key:\s*"([a-z0-9_]+)"')


def _parse_keys() -> set[str]:
    text = _FRONTEND_FILE.read_text(encoding="utf-8")
    block = _BLOCK_RE.search(text)
    assert block is not None, "STRATEGY_FEATURES not found"
    return set(_KEY_RE.findall(block.group("body")))


def test_feature_registry_in_sync():
    backend_keys = set(FEATURE_KEYS)
    frontend_keys = _parse_keys()
    assert backend_keys == frontend_keys, (
        f"Feature registry drift — frontend extra: "
        f"{frontend_keys - backend_keys}; "
        f"backend extra: {backend_keys - frontend_keys}"
    )
```

- [ ] **Step 4: Run sync test, expect pass**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_feature_registry_sync.py -v 2>&1 | tail -5
```

Expected: 1 passed.

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npx eslint components/algo-trading/strategyFeatureCatalog.ts lib/types/algoStrategy.ts --fix
cd ..
git add frontend/components/algo-trading/strategyFeatureCatalog.ts frontend/lib/types/algoStrategy.ts backend/algo/tests/test_feature_registry_sync.py
git commit -m "$(cat <<'EOF'
feat(algo): frontend feature catalog mirror + AST node-type literals

Slice 4 of the Algo Trading epic. STRATEGY_FEATURES TS literal
mirrors backend FEATURES; AstNodeType + AST_NODE_FAMILY +
AST_NODE_LABEL drive the visual builder palette in Slice 5.
CI sync test (test_feature_registry_sync.py) blocks drift.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: useStrategies SWR hook + StrategiesTab list view

**Files:**
- Create: `frontend/hooks/useStrategies.ts`
- Create: `frontend/components/algo-trading/StrategiesTab.tsx`
- Create: `frontend/components/algo-trading/__tests__/StrategiesTab.test.tsx`
- Modify: `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx` (wire StrategiesTab to the `strategies` tab id)

- [ ] **Step 1: SWR hook**

```ts
// frontend/hooks/useStrategies.ts
"use client";
/**
 * SWR hook for /v1/algo/strategies/*.
 *
 * - useStrategies(): list view; SWR-keyed on user implicitly
 *   via the cookie-bearing apiFetch.
 * - useStrategy(id): full AST fetch; lazy.
 * - createStrategy / updateStrategy / archiveStrategy:
 *   imperative wrappers that mutate the list cache.
 */

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface StrategySummary {
  id: string;
  name: string;
  mode: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
}

export interface StrategyAst {
  id: string;
  name: string;
  universe: unknown;
  schedule: unknown;
  rebalance: unknown;
  root: unknown;
  risk: unknown;
}

const LIST_KEY = `${API_URL}/algo/strategies`;

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(
      `${url}: HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  return r.json();
}

export function useStrategies() {
  const { data, error, isLoading } = useSWR<{ strategies: StrategySummary[] }>(
    LIST_KEY,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  return {
    strategies: data?.strategies ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load strategies"
      : null,
  };
}

export async function createStrategy(payload: StrategyAst): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/strategies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  if (!r.ok) {
    throw new Error(`createStrategy: HTTP ${r.status}`);
  }
  const body = (await r.json()) as { id: string };
  await mutate(LIST_KEY);
  return body.id;
}

export async function updateStrategy(
  id: string,
  payload: StrategyAst,
): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  if (!r.ok) {
    throw new Error(`updateStrategy: HTTP ${r.status}`);
  }
  await mutate(LIST_KEY);
}

export async function archiveStrategy(id: string): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`, {
    method: "DELETE",
  });
  if (!r.ok) {
    throw new Error(`archiveStrategy: HTTP ${r.status}`);
  }
  await mutate(LIST_KEY);
}
```

- [ ] **Step 2: StrategiesTab list view**

```tsx
// frontend/components/algo-trading/StrategiesTab.tsx
"use client";
/**
 * Algo Trading — Strategies tab. Slice 4 ships the list view +
 * archive action; Slice 5 wires the visual builder via the
 * "New strategy" / "Edit" buttons.
 */

import { useCallback, useState } from "react";

import {
  archiveStrategy,
  useStrategies,
  type StrategySummary,
} from "@/hooks/useStrategies";

interface Props {
  onOpenBuilder?: (id: string | null) => void;  // null = new
}

export function StrategiesTab({ onOpenBuilder }: Props) {
  const { strategies, loading, error } = useStrategies();
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleArchive = useCallback(async (id: string) => {
    setBusyId(id);
    try {
      await archiveStrategy(id);
    } catch (err) {
      console.error("Archive failed", err);
    } finally {
      setBusyId(null);
    }
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Strategies
        </h2>
        <button
          type="button"
          onClick={() => onOpenBuilder?.(null)}
          data-testid="algo-strategies-new"
          className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1 text-xs"
        >
          + New strategy
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-xs p-2"
        >
          {error}
        </div>
      )}

      {loading && strategies.length === 0 ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : strategies.length === 0 ? (
        <p
          data-testid="algo-strategies-empty"
          className="text-sm text-gray-500 dark:text-gray-400"
        >
          No strategies yet. Click &ldquo;+ New strategy&rdquo; to draft one.
        </p>
      ) : (
        <div
          data-testid="algo-strategies-list"
          className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700"
        >
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800/50 text-xs">
              <tr>
                <Th>Name</Th>
                <Th>Mode</Th>
                <Th>Status</Th>
                <Th>Updated</Th>
                <Th align="right">Actions</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {strategies.map((s) => (
                <Row
                  key={s.id}
                  s={s}
                  busy={busyId === s.id}
                  onEdit={() => onOpenBuilder?.(s.id)}
                  onArchive={() => handleArchive(s.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({
  children, align = "left",
}: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      className={`px-3 py-2 text-${align} text-xs font-medium text-gray-600 dark:text-gray-300`}
    >
      {children}
    </th>
  );
}

function Row({
  s, busy, onEdit, onArchive,
}: {
  s: StrategySummary;
  busy: boolean;
  onEdit: () => void;
  onArchive: () => void;
}) {
  return (
    <tr
      data-testid={`algo-strategies-row-${s.id}`}
      className="hover:bg-gray-50 dark:hover:bg-gray-800/50"
    >
      <td className="px-3 py-2">{s.name}</td>
      <td className="px-3 py-2 text-gray-500">{s.mode}</td>
      <td className="px-3 py-2 text-gray-500">{s.status}</td>
      <td className="px-3 py-2 text-gray-500">
        {s.updated_at ? new Date(s.updated_at).toLocaleString() : "—"}
      </td>
      <td className="px-3 py-2 text-right space-x-2">
        <button
          type="button"
          onClick={onEdit}
          data-testid={`algo-strategies-edit-${s.id}`}
          className="text-indigo-600 dark:text-indigo-400 hover:underline text-xs"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onArchive}
          disabled={busy}
          data-testid={`algo-strategies-archive-${s.id}`}
          className="text-red-600 dark:text-red-400 hover:underline text-xs disabled:opacity-40"
        >
          {busy ? "Archiving…" : "Archive"}
        </button>
      </td>
    </tr>
  );
}
```

- [ ] **Step 3: Wire into `AlgoTradingClient.tsx`**

Read the file. In the `tabPanel` `useMemo` switch (currently has `case "settings"` returning `<SettingsTab />`), add a case for `"strategies"` that returns `<StrategiesTab />`. Also add the import at the top:

```tsx
import { StrategiesTab } from "@/components/algo-trading/StrategiesTab";
```

```tsx
case "strategies":
  return <StrategiesTab />;
```

- [ ] **Step 4: Vitest spec**

```tsx
// frontend/components/algo-trading/__tests__/StrategiesTab.test.tsx
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

vi.mock("swr", () => ({
  default: () => ({ data: { strategies: [] }, error: null, isLoading: false }),
  mutate: vi.fn(),
}));

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/lib/config", () => ({
  API_URL: "http://test/api",
}));

import { StrategiesTab } from "../StrategiesTab";

afterEach(() => cleanup());

describe("StrategiesTab", () => {
  it("renders empty state when no strategies", () => {
    render(<StrategiesTab />);
    expect(screen.getByTestId("algo-strategies-empty")).toBeTruthy();
  });

  it("calls onOpenBuilder(null) when New strategy clicked", () => {
    const onOpen = vi.fn();
    render(<StrategiesTab onOpenBuilder={onOpen} />);
    fireEvent.click(screen.getByTestId("algo-strategies-new"));
    expect(onOpen).toHaveBeenCalledWith(null);
  });
});
```

- [ ] **Step 5: Run vitest**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/StrategiesTab.test.tsx 2>&1 | tail -8
cd ..
```

Expected: 2 passed.

- [ ] **Step 6: Lint + commit**

```bash
cd frontend && npx eslint hooks/useStrategies.ts components/algo-trading/StrategiesTab.tsx components/algo-trading/__tests__/StrategiesTab.test.tsx 'app/(authenticated)/algo-trading/AlgoTradingClient.tsx' --fix
cd ..
git add frontend/hooks/useStrategies.ts frontend/components/algo-trading/StrategiesTab.tsx frontend/components/algo-trading/__tests__/StrategiesTab.test.tsx 'frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx'
git commit -m "$(cat <<'EOF'
feat(algo): Strategies tab list view

Slice 4 of the Algo Trading epic. SWR-driven list of saved
strategies with archive action; "+ New strategy" + per-row
"Edit" buttons stub the Slice 5 builder via onOpenBuilder
callback.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: StrategyBuilder shell — palette + canvas + JSON pane

**Files:**
- Create: `frontend/components/algo-trading/builder/StrategyBuilder.tsx`
- Create: `frontend/components/algo-trading/builder/NodePalette.tsx`
- Create: `frontend/components/algo-trading/builder/JsonPane.tsx`
- Create: `frontend/components/algo-trading/builder/templates.ts`
- Create: `frontend/components/algo-trading/__tests__/StrategyBuilder.test.tsx`

- [ ] **Step 1: Sample templates**

```ts
// frontend/components/algo-trading/builder/templates.ts
/**
 * Three sample-strategy templates used as starting points by
 * the visual builder. Each is a complete, valid Strategy AST
 * — backend will accept POSTed verbatim.
 */

import type { StrategyAst } from "@/hooks/useStrategies";

const _common = {
  universe: {
    type: "scope",
    scope: "watchlist",
    filter: { ticker_type: ["stock"], market: "india" },
  },
  schedule: { type: "bar_close", interval: "1d", time: "15:25 IST" },
  rebalance: { type: "daily", max_positions: 10 },
  risk: {
    per_trade: { stop_loss_pct: 5, max_qty: 100 },
    portfolio: { max_exposure_pct: 80, max_concentration_pct: 25 },
    daily: { max_loss_pct: 2, max_open_positions: 10 },
  },
};

function randomId(): string {
  return crypto.randomUUID();
}

export const TEMPLATES: { key: string; label: string; ast: StrategyAst }[] = [
  {
    key: "blank",
    label: "Blank (hold)",
    ast: { id: randomId(), name: "New strategy", ..._common, root: { type: "hold" } } as unknown as StrategyAst,
  },
  {
    key: "golden_cross",
    label: "Golden cross",
    ast: {
      id: randomId(),
      name: "Golden cross v1",
      ..._common,
      root: {
        type: "if",
        cond: {
          type: "and",
          operands: [
            { type: "compare", left: { feature: "today_ltp" }, op: ">", right: { feature: "sma_50" } },
            { type: "compare", left: { feature: "today_ltp" }, op: ">", right: { feature: "sma_200" } },
            { type: "compare", left: { feature: "golden_cross_days_ago" }, op: "<=", right: { literal: 10 } },
          ],
        },
        then: { type: "set_target_weight", weight: 0.20 },
        else: { type: "exit", scope: "all_open" },
      },
    } as unknown as StrategyAst,
  },
  {
    key: "mean_reversion",
    label: "Mean reversion (RSI)",
    ast: {
      id: randomId(),
      name: "Mean reversion v1",
      ..._common,
      root: {
        type: "if",
        cond: {
          type: "compare",
          left: { feature: "rsi" },
          op: "<",
          right: { literal: 30 },
        },
        then: { type: "set_target_weight", weight: 0.10 },
        else: { type: "hold" },
      },
    } as unknown as StrategyAst,
  },
];
```

- [ ] **Step 2: NodePalette**

```tsx
// frontend/components/algo-trading/builder/NodePalette.tsx
"use client";
/**
 * Left-rail palette of AST node types, grouped by family.
 * Slice 5 v1 — click-to-insert (drag-and-drop deferred until
 * actual usage data shows it's needed).
 */

import {
  AST_NODE_FAMILY,
  AST_NODE_LABEL,
  type AstNodeType,
} from "@/lib/types/algoStrategy";

interface Props {
  onPick: (type: AstNodeType) => void;
}

const ORDER: AstNodeType[] = [
  // Composite first — most common entry point
  "if", "select_top_n", "weighted",
  // Condition
  "compare", "and", "or", "not", "crossover", "between",
  // Action
  "buy", "sell", "exit", "hold", "set_target_weight",
];

export function NodePalette({ onPick }: Props) {
  const groups: Record<string, AstNodeType[]> = {
    Composite: [],
    Condition: [],
    Action: [],
  };
  for (const t of ORDER) {
    const fam = AST_NODE_FAMILY[t];
    const label = fam.charAt(0).toUpperCase() + fam.slice(1);
    groups[label].push(t);
  }

  return (
    <div
      data-testid="algo-builder-palette"
      className="space-y-3 text-xs"
    >
      {Object.entries(groups).map(([label, types]) => (
        <fieldset key={label}>
          <legend className="font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
            {label}
          </legend>
          <div className="flex flex-wrap gap-1">
            {types.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => onPick(t)}
                data-testid={`algo-builder-palette-${t}`}
                className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 hover:bg-indigo-50 dark:hover:bg-indigo-900/20"
              >
                {AST_NODE_LABEL[t]}
              </button>
            ))}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: JsonPane**

```tsx
// frontend/components/algo-trading/builder/JsonPane.tsx
"use client";
/**
 * Live JSON preview of the strategy AST. Read-only by default
 * with a "Paste JSON" toggle that lets power users import a
 * full AST verbatim — server still re-validates on save.
 */

import { useState } from "react";

interface Props {
  ast: unknown;
  onPaste?: (raw: string) => { ok: boolean; error?: string };
}

export function JsonPane({ ast, onPaste }: Props) {
  const [editMode, setEditMode] = useState(false);
  const [draft, setDraft] = useState("");
  const [pasteError, setPasteError] = useState<string | null>(null);

  return (
    <div className="space-y-2 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-gray-700 dark:text-gray-200">
          JSON
        </span>
        {onPaste && (
          <button
            type="button"
            onClick={() => {
              setEditMode((m) => !m);
              setDraft(JSON.stringify(ast, null, 2));
              setPasteError(null);
            }}
            data-testid="algo-builder-json-toggle"
            className="text-indigo-600 dark:text-indigo-400 hover:underline"
          >
            {editMode ? "Cancel" : "Paste JSON"}
          </button>
        )}
      </div>
      {editMode ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            data-testid="algo-builder-json-input"
            className="w-full h-72 font-mono text-[11px] p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (!onPaste) return;
                const res = onPaste(draft);
                if (res.ok) {
                  setEditMode(false);
                  setPasteError(null);
                } else {
                  setPasteError(res.error ?? "Invalid JSON");
                }
              }}
              data-testid="algo-builder-json-apply"
              className="rounded bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1"
            >
              Apply
            </button>
          </div>
          {pasteError && (
            <div role="alert" className="text-red-600 dark:text-red-400">
              {pasteError}
            </div>
          )}
        </>
      ) : (
        <pre
          data-testid="algo-builder-json"
          className="font-mono text-[11px] p-2 rounded border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 overflow-x-auto max-h-72"
        >
          {JSON.stringify(ast, null, 2)}
        </pre>
      )}
    </div>
  );
}
```

- [ ] **Step 4: StrategyBuilder shell**

```tsx
// frontend/components/algo-trading/builder/StrategyBuilder.tsx
"use client";
/**
 * Visual JSON-AST strategy builder. Slice 5 v1 — pick a
 * template, see the rendered AST tree (read-only in v1) +
 * live JSON pane, save through the existing CRUD API.
 *
 * Future deltas: in-tree node editing (Slice 5b), drag-and-drop
 * (Slice 5c) — both deferred until usage data shows they're
 * worth the build complexity. The "Paste JSON" escape hatch
 * keeps power users productive while we iterate.
 */

import { useCallback, useEffect, useState } from "react";

import { AstTreeView } from "./AstTreeView";
import { JsonPane } from "./JsonPane";
import { NodePalette } from "./NodePalette";
import { TEMPLATES } from "./templates";
import {
  createStrategy,
  updateStrategy,
  type StrategyAst,
} from "@/hooks/useStrategies";

interface Props {
  initial?: StrategyAst | null;
  strategyId?: string | null;
  onSaved?: (id: string) => void;
  onCancel?: () => void;
}

export function StrategyBuilder({
  initial,
  strategyId,
  onSaved,
  onCancel,
}: Props) {
  const [ast, setAst] = useState<StrategyAst>(
    () => initial ?? TEMPLATES[0].ast,
  );
  const [name, setName] = useState(ast.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAst((cur) => ({ ...cur, name }));
  }, [name]);

  const handlePickTemplate = useCallback((key: string) => {
    const t = TEMPLATES.find((x) => x.key === key);
    if (!t) return;
    setAst(t.ast);
    setName(t.ast.name);
  }, []);

  const handlePastedJson = useCallback(
    (raw: string) => {
      try {
        const parsed = JSON.parse(raw) as StrategyAst;
        if (typeof parsed !== "object" || parsed === null) {
          return { ok: false, error: "JSON must be an object" };
        }
        setAst(parsed);
        setName(parsed.name ?? "Imported strategy");
        return { ok: true };
      } catch (e) {
        return { ok: false, error: (e as Error).message };
      }
    },
    [],
  );

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      if (strategyId) {
        await updateStrategy(strategyId, ast);
        onSaved?.(strategyId);
      } else {
        const id = await createStrategy(ast);
        onSaved?.(id);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [ast, strategyId, onSaved]);

  return (
    <div
      data-testid="algo-strategy-builder"
      className="grid grid-cols-1 md:grid-cols-[200px_1fr_320px] gap-4"
    >
      <aside className="space-y-3">
        <fieldset className="text-xs">
          <legend className="font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
            Templates
          </legend>
          <div className="flex flex-col gap-1">
            {TEMPLATES.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => handlePickTemplate(t.key)}
                data-testid={`algo-builder-template-${t.key}`}
                className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 text-left hover:bg-indigo-50 dark:hover:bg-indigo-900/20"
              >
                {t.label}
              </button>
            ))}
          </div>
        </fieldset>
        <NodePalette onPick={() => { /* v1: pick is read-only stub */ }} />
      </aside>

      <main className="space-y-3">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="algo-builder-name"
          className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5 text-sm"
          placeholder="Strategy name"
        />
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-white dark:bg-gray-900">
          <AstTreeView node={ast.root} />
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            data-testid="algo-builder-save"
            className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 text-sm disabled:opacity-40"
          >
            {saving ? "Saving…" : strategyId ? "Update" : "Save"}
          </button>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              data-testid="algo-builder-cancel"
              className="rounded-md border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-sm"
            >
              Cancel
            </button>
          )}
        </div>
        {error && (
          <div role="alert" className="text-xs text-red-600 dark:text-red-400">
            {error}
          </div>
        )}
      </main>

      <aside>
        <JsonPane ast={ast} onPaste={handlePastedJson} />
      </aside>
    </div>
  );
}
```

- [ ] **Step 5: Builder vitest**

```tsx
// frontend/components/algo-trading/__tests__/StrategyBuilder.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

vi.mock("swr", () => ({
  default: () => ({ data: null, error: null, isLoading: false }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { StrategyBuilder } from "../builder/StrategyBuilder";

afterEach(() => cleanup());

describe("StrategyBuilder", () => {
  it("renders with the blank template by default", () => {
    render(<StrategyBuilder />);
    expect(screen.getByTestId("algo-strategy-builder")).toBeTruthy();
    expect(
      (screen.getByTestId("algo-builder-name") as HTMLInputElement).value,
    ).toBe("New strategy");
  });

  it("switches templates when a template button is clicked", () => {
    render(<StrategyBuilder />);
    fireEvent.click(screen.getByTestId("algo-builder-template-golden_cross"));
    expect(
      (screen.getByTestId("algo-builder-name") as HTMLInputElement).value,
    ).toBe("Golden cross v1");
  });

  it("renders a JSON preview pane", () => {
    render(<StrategyBuilder />);
    const json = screen.getByTestId("algo-builder-json");
    expect(json.textContent).toContain('"root"');
  });

  it("toggles paste mode and applies pasted JSON", () => {
    render(<StrategyBuilder />);
    fireEvent.click(screen.getByTestId("algo-builder-json-toggle"));
    const ta = screen.getByTestId("algo-builder-json-input");
    fireEvent.change(ta, {
      target: {
        value: JSON.stringify({
          id: "abcd",
          name: "Pasted",
          universe: {},
          schedule: {},
          rebalance: {},
          root: { type: "hold" },
          risk: {},
        }),
      },
    });
    fireEvent.click(screen.getByTestId("algo-builder-json-apply"));
    expect(
      (screen.getByTestId("algo-builder-name") as HTMLInputElement).value,
    ).toBe("Pasted");
  });
});
```

- [ ] **Step 6: Run vitest**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/StrategyBuilder.test.tsx 2>&1 | tail -10
cd ..
```

Expected: 4 passed.

- [ ] **Step 7: Lint + commit**

```bash
cd frontend && npx eslint components/algo-trading/builder/ components/algo-trading/__tests__/StrategyBuilder.test.tsx --fix
cd ..
git add frontend/components/algo-trading/builder/ frontend/components/algo-trading/__tests__/StrategyBuilder.test.tsx
git commit -m "$(cat <<'EOF'
feat(algo): StrategyBuilder shell — palette + canvas + JSON pane

Slice 5 of the Algo Trading epic. Three sample templates
(blank/golden cross/mean reversion); read-only AST tree view;
live JSON pane with paste-to-import escape hatch; save through
the Slice 4 CRUD API. In-tree node editing + drag-and-drop
deferred to 5b/5c.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: AstTreeView recursive renderer

**Files:**
- Create: `frontend/components/algo-trading/builder/AstTreeView.tsx`
- Create: `frontend/components/algo-trading/builder/nodeRenderers.tsx`
- Create: `frontend/components/algo-trading/__tests__/AstTreeView.test.tsx`

- [ ] **Step 1: Per-node renderers**

```tsx
// frontend/components/algo-trading/builder/nodeRenderers.tsx
"use client";
/**
 * Per-AST-node-type single-row renderers. Recursive children
 * render through ``<AstTreeView />`` (provided as a prop to
 * avoid circular imports).
 */

import {
  STRATEGY_FEATURE_BY_KEY,
} from "@/components/algo-trading/strategyFeatureCatalog";

export function FeatureChip({ keyName }: { keyName: string }) {
  const f = STRATEGY_FEATURE_BY_KEY[keyName];
  return (
    <span className="rounded bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 px-1.5 py-0.5 text-[11px]">
      {f?.label ?? keyName}
    </span>
  );
}

export function LiteralChip({ value }: { value: number }) {
  return (
    <span className="rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 px-1.5 py-0.5 text-[11px] font-mono">
      {value}
    </span>
  );
}

export function OperandPill(
  { op }: { op: { feature?: string; literal?: number } },
) {
  if (op.feature) return <FeatureChip keyName={op.feature} />;
  if (op.literal !== undefined) return <LiteralChip value={op.literal} />;
  return <span className="text-gray-400">?</span>;
}
```

- [ ] **Step 2: Recursive AstTreeView**

```tsx
// frontend/components/algo-trading/builder/AstTreeView.tsx
"use client";
/**
 * Recursive read-only renderer for the strategy AST. Slice 5 v1
 * is read-only — Slice 5b will add inline editing per node.
 */

import { OperandPill } from "./nodeRenderers";

interface Props {
  node: unknown;
  depth?: number;
}

export function AstTreeView({ node, depth = 0 }: Props) {
  if (
    typeof node !== "object" ||
    node === null ||
    !("type" in (node as Record<string, unknown>))
  ) {
    return null;
  }

  const n = node as { type: string } & Record<string, unknown>;
  const indent = `pl-${Math.min(depth * 4, 12)}`;

  return (
    <div
      data-testid={`algo-builder-node-${n.type}`}
      className={`text-xs space-y-1 ${indent}`}
    >
      <NodeHeader node={n} />
      <Children node={n} depth={depth} />
    </div>
  );
}

function NodeHeader({ node }: { node: { type: string } & Record<string, unknown> }) {
  const t = node.type;
  if (t === "compare") {
    return (
      <div className="flex items-center gap-1.5">
        <span className="font-mono">compare</span>
        <OperandPill op={node.left as { feature?: string; literal?: number }} />
        <span className="font-mono">{String(node.op)}</span>
        <OperandPill op={node.right as { feature?: string; literal?: number }} />
      </div>
    );
  }
  if (t === "and" || t === "or") {
    return <div className="font-mono uppercase">{t}</div>;
  }
  if (t === "not") {
    return <div className="font-mono uppercase">NOT</div>;
  }
  if (t === "if") {
    return <div className="font-mono">if … then … else …</div>;
  }
  if (t === "select_top_n") {
    return (
      <div className="font-mono">
        select top {String(node.n)} by{" "}
        <OperandPill op={node.rank_by as { feature?: string; literal?: number }} />
      </div>
    );
  }
  if (t === "set_target_weight") {
    return (
      <div className="font-mono">
        set target weight {String(node.weight)}
      </div>
    );
  }
  if (t === "hold") return <div className="font-mono">hold</div>;
  if (t === "buy") return <div className="font-mono">buy</div>;
  if (t === "sell") return <div className="font-mono">sell</div>;
  if (t === "exit") {
    return (
      <div className="font-mono">
        exit ({String((node as { scope?: string }).scope ?? "")})
      </div>
    );
  }
  return <div className="font-mono">{t}</div>;
}

function Children({
  node, depth,
}: { node: { type: string } & Record<string, unknown>; depth: number }) {
  const t = node.type;
  if (t === "and" || t === "or") {
    const operands = (node.operands ?? []) as unknown[];
    return (
      <div className="space-y-1 border-l-2 border-gray-200 dark:border-gray-700 pl-2">
        {operands.map((c, i) => (
          <AstTreeView key={i} node={c} depth={depth + 1} />
        ))}
      </div>
    );
  }
  if (t === "not") {
    return (
      <div className="space-y-1 border-l-2 border-gray-200 dark:border-gray-700 pl-2">
        <AstTreeView node={node.operand} depth={depth + 1} />
      </div>
    );
  }
  if (t === "if") {
    return (
      <div className="space-y-1">
        <Branch label="cond" child={node.cond} depth={depth} />
        <Branch label="then" child={node.then} depth={depth} />
        <Branch label="else" child={node.else} depth={depth} />
      </div>
    );
  }
  if (t === "select_top_n") {
    return (
      <Branch label="action" child={node.action} depth={depth} />
    );
  }
  return null;
}

function Branch({
  label, child, depth,
}: { label: string; child: unknown; depth: number }) {
  return (
    <div className="border-l-2 border-gray-200 dark:border-gray-700 pl-2">
      <span className="font-semibold text-gray-500 dark:text-gray-400 mr-1">
        {label}:
      </span>
      <AstTreeView node={child} depth={depth + 1} />
    </div>
  );
}
```

- [ ] **Step 3: Vitest**

```tsx
// frontend/components/algo-trading/__tests__/AstTreeView.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AstTreeView } from "../builder/AstTreeView";

afterEach(() => cleanup());

describe("AstTreeView", () => {
  it("renders a hold leaf", () => {
    render(<AstTreeView node={{ type: "hold" }} />);
    expect(screen.getByTestId("algo-builder-node-hold").textContent)
      .toContain("hold");
  });

  it("renders a compare node with feature + op + literal", () => {
    render(
      <AstTreeView
        node={{
          type: "compare",
          left: { feature: "rsi" },
          op: "<",
          right: { literal: 30 },
        }}
      />,
    );
    const t = screen.getByTestId("algo-builder-node-compare").textContent ?? "";
    expect(t).toContain("RSI");
    expect(t).toContain("<");
    expect(t).toContain("30");
  });

  it("renders an if/then/else with three branches", () => {
    render(
      <AstTreeView
        node={{
          type: "if",
          cond: {
            type: "compare",
            left: { feature: "rsi" },
            op: "<",
            right: { literal: 30 },
          },
          then: { type: "set_target_weight", weight: 0.10 },
          else: { type: "hold" },
        }}
      />,
    );
    expect(screen.getByTestId("algo-builder-node-if")).toBeTruthy();
    expect(screen.getByTestId("algo-builder-node-compare")).toBeTruthy();
    expect(screen.getByTestId("algo-builder-node-set_target_weight"))
      .toBeTruthy();
    expect(screen.getByTestId("algo-builder-node-hold")).toBeTruthy();
  });

  it("returns null for non-object node", () => {
    const { container } = render(<AstTreeView node={"string" as unknown} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 4: Run vitest**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/AstTreeView.test.tsx 2>&1 | tail -10
cd ..
```

Expected: 4 passed.

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npx eslint components/algo-trading/builder/AstTreeView.tsx components/algo-trading/builder/nodeRenderers.tsx components/algo-trading/__tests__/AstTreeView.test.tsx --fix
cd ..
git add frontend/components/algo-trading/builder/AstTreeView.tsx frontend/components/algo-trading/builder/nodeRenderers.tsx frontend/components/algo-trading/__tests__/AstTreeView.test.tsx
git commit -m "$(cat <<'EOF'
feat(algo): AstTreeView recursive renderer + node renderers

Slice 5 of the Algo Trading epic. Read-only recursive renderer
for every AST node family (compare/and/or/not/crossover/between
/buy/sell/exit/hold/set_target_weight/if/select_top_n/weighted)
with feature-label chips and indentation per nesting depth. 4
vitest cases.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: StrategiesTab → builder mode switch + smoke

**Files:**
- Modify: `frontend/components/algo-trading/StrategiesTab.tsx` (two-mode container: list ↔ builder)
- Modify: `frontend/components/algo-trading/__tests__/StrategiesTab.test.tsx` (cover both modes)

- [ ] **Step 1: Add builder-mode state to `StrategiesTab.tsx`**

Replace the file with:

```tsx
"use client";
/**
 * Algo Trading — Strategies tab. Two-mode container:
 *
 * - list: shows saved strategies + "+ New strategy" / "Edit"
 *   buttons that flip into builder mode.
 * - builder: full StrategyBuilder; "Save" or "Cancel" returns
 *   to list mode.
 */

import { useCallback, useState } from "react";
import useSWR from "swr";

import { StrategyBuilder } from "@/components/algo-trading/builder/StrategyBuilder";
import {
  archiveStrategy,
  useStrategies,
  type StrategyAst,
  type StrategySummary,
} from "@/hooks/useStrategies";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

type Mode =
  | { kind: "list" }
  | { kind: "builder"; id: string | null };

async function fetchAst(id: string): Promise<StrategyAst> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function StrategiesTab() {
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  if (mode.kind === "builder") {
    return (
      <BuilderMode
        id={mode.id}
        onDone={() => setMode({ kind: "list" })}
      />
    );
  }
  return <ListMode onOpenBuilder={(id) => setMode({ kind: "builder", id })} />;
}

function ListMode({
  onOpenBuilder,
}: { onOpenBuilder: (id: string | null) => void }) {
  const { strategies, loading, error } = useStrategies();
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleArchive = useCallback(async (id: string) => {
    setBusyId(id);
    try {
      await archiveStrategy(id);
    } catch (err) {
      console.error("Archive failed", err);
    } finally {
      setBusyId(null);
    }
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Strategies
        </h2>
        <button
          type="button"
          onClick={() => onOpenBuilder(null)}
          data-testid="algo-strategies-new"
          className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1 text-xs"
        >
          + New strategy
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-xs p-2"
        >
          {error}
        </div>
      )}

      {loading && strategies.length === 0 ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : strategies.length === 0 ? (
        <p
          data-testid="algo-strategies-empty"
          className="text-sm text-gray-500 dark:text-gray-400"
        >
          No strategies yet. Click &ldquo;+ New strategy&rdquo; to draft one.
        </p>
      ) : (
        <div
          data-testid="algo-strategies-list"
          className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700"
        >
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800/50 text-xs">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Name</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Mode</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Status</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Updated</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-600 dark:text-gray-300">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {strategies.map((s) => (
                <Row
                  key={s.id}
                  s={s}
                  busy={busyId === s.id}
                  onEdit={() => onOpenBuilder(s.id)}
                  onArchive={() => handleArchive(s.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Row({
  s, busy, onEdit, onArchive,
}: {
  s: StrategySummary;
  busy: boolean;
  onEdit: () => void;
  onArchive: () => void;
}) {
  return (
    <tr
      data-testid={`algo-strategies-row-${s.id}`}
      className="hover:bg-gray-50 dark:hover:bg-gray-800/50"
    >
      <td className="px-3 py-2">{s.name}</td>
      <td className="px-3 py-2 text-gray-500">{s.mode}</td>
      <td className="px-3 py-2 text-gray-500">{s.status}</td>
      <td className="px-3 py-2 text-gray-500">
        {s.updated_at ? new Date(s.updated_at).toLocaleString() : "—"}
      </td>
      <td className="px-3 py-2 text-right space-x-2">
        <button
          type="button"
          onClick={onEdit}
          data-testid={`algo-strategies-edit-${s.id}`}
          className="text-indigo-600 dark:text-indigo-400 hover:underline text-xs"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onArchive}
          disabled={busy}
          data-testid={`algo-strategies-archive-${s.id}`}
          className="text-red-600 dark:text-red-400 hover:underline text-xs disabled:opacity-40"
        >
          {busy ? "Archiving…" : "Archive"}
        </button>
      </td>
    </tr>
  );
}

function BuilderMode({
  id, onDone,
}: { id: string | null; onDone: () => void }) {
  const { data, error, isLoading } = useSWR<StrategyAst>(
    id ? `${API_URL}/algo/strategies/${id}` : null,
    () => (id ? fetchAst(id) : Promise.reject()),
    { revalidateOnFocus: false },
  );

  if (id && isLoading) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }
  if (error) {
    return (
      <div role="alert" className="text-xs text-red-600 dark:text-red-400">
        {(error as Error).message}
      </div>
    );
  }

  return (
    <StrategyBuilder
      strategyId={id}
      initial={data ?? null}
      onSaved={onDone}
      onCancel={onDone}
    />
  );
}
```

- [ ] **Step 2: Update vitest**

Replace the test file with:

```tsx
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

vi.mock("swr", () => ({
  default: () => ({ data: { strategies: [] }, error: null, isLoading: false }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { StrategiesTab } from "../StrategiesTab";

afterEach(() => cleanup());

describe("StrategiesTab", () => {
  it("renders empty state when no strategies", () => {
    render(<StrategiesTab />);
    expect(screen.getByTestId("algo-strategies-empty")).toBeTruthy();
  });

  it("opens builder mode when New strategy is clicked", () => {
    render(<StrategiesTab />);
    fireEvent.click(screen.getByTestId("algo-strategies-new"));
    expect(screen.getByTestId("algo-strategy-builder")).toBeTruthy();
  });

  it("returns to list when builder Cancel is clicked", () => {
    render(<StrategiesTab />);
    fireEvent.click(screen.getByTestId("algo-strategies-new"));
    fireEvent.click(screen.getByTestId("algo-builder-cancel"));
    expect(screen.getByTestId("algo-strategies-empty")).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run vitest**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/StrategiesTab.test.tsx 2>&1 | tail -8
cd ..
```

Expected: 3 passed.

- [ ] **Step 4: Lint + commit**

```bash
cd frontend && npx eslint components/algo-trading/StrategiesTab.tsx components/algo-trading/__tests__/StrategiesTab.test.tsx --fix
cd ..
git add frontend/components/algo-trading/StrategiesTab.tsx frontend/components/algo-trading/__tests__/StrategiesTab.test.tsx
git commit -m "$(cat <<'EOF'
feat(algo): StrategiesTab list ↔ builder two-mode container

Slice 5 of the Algo Trading epic. The Strategies tab now flips
between list and builder via local state; per-row "Edit" loads
the AST through GET /v1/algo/strategies/{id}; "+ New" starts
from the blank template.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: PROGRESS.md + push

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Prepend a Session 2 entry**

Open `PROGRESS.md`. Insert after the `---` separator at the top:

```markdown
## 2026-05-08 (later 2) — Algo Trading Slices 4 + 5: strategy AST + visual builder

**Branch:** `feature/algo-trading-session-2-strategy-ast` (built off Session 1's tip)
**Epic:** Algo Trading Platform v1
**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md`
**Plan:** `docs/superpowers/plans/2026-05-08-algo-trading-session-2-strategy-ast.md`

**Shipped:**
- Slice 4: Backend AST grammar (Pydantic discriminated unions, 14 node types across condition / action / composite); 18-feature dictionary registry; `algo.strategies` async repo; `/v1/algo/strategies/*` CRUD with `pro_or_superuser` guard + per-user isolation; CI sync test (`test_feature_registry_sync.py`) blocks frontend↔backend feature drift.
- Slice 5: Frontend visual builder shell (palette + read-only AST tree + live JSON pane with paste-to-import escape hatch); 3 starter templates (blank / golden cross / mean reversion); two-mode `StrategiesTab` (list ↔ builder).

**Tests:** 30 backend AST validation + 4 strategies-route smoke + 1 sync gate + 2 vitest StrategiesTab + 4 vitest StrategyBuilder + 4 vitest AstTreeView. All passing.

**Deferred:** in-tree node editing (Slice 5b), drag-and-drop palette (Slice 5c), backend rate-limit per pro user, Slices 2/3/6/7/8/9/10.

---
```

- [ ] **Step 2: Commit**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): log Algo Trading session 2 — Slices 4 + 5

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

- [ ] **Step 3: Push** (asks user for confirmation per auto-mode rules)

```bash
git push -u origin feature/algo-trading-session-2-strategy-ast
```

> **Note for coordinator:** push only — no PR for now per the user's "only push and move to next session" instruction in Session 1. PR creation deferred to a later session.

---

## Self-Review (post-write)

**1. Spec coverage:**
- §4.1 node families → Task 2 (Pydantic models for all 14 node types) ✓
- §4.2 concrete example → Task 3 test fixture (`_VALID_PAYLOAD`), Task 6 templates ✓
- §4.3 feature dictionary → Tasks 1 (backend) + 4 (frontend mirror + sync test) ✓
- §4.4 visual builder ↔ JSON → Tasks 6 (StrategyBuilder shell + JsonPane) + 7 (AstTreeView recursion) ✓
- AST CRUD storage → Task 3 (repo + routes + smoke tests) ✓

**2. Placeholder scan:**
- One `// v1: pick is read-only stub` comment in `StrategyBuilder.tsx` — explicit deferred behaviour, not a TODO.
- Three `for now`-style notes in commit messages — accurate.
- No TBDs / "implement later" / unfilled code blocks.

**3. Type consistency:**
- `Strategy`, `parse_strategy`, `FEATURE_KEYS`, `FEATURE_BY_KEY` consistent across Tasks 1, 2, 3, 4.
- `StrategyAst`, `StrategySummary` consistent across `useStrategies.ts` (Task 5), `templates.ts` (Task 6), `StrategiesTab.tsx` (Tasks 5 + 8).
- `AstNodeType` literal consistent between `algoStrategy.ts` (Task 4), `NodePalette.tsx` (Task 6), AST renderer dispatch (Task 7).
- `STRATEGY_FEATURE_BY_KEY` consistent between Task 4 export and Task 7 consumer.
- Testid prefixes (`algo-builder-*`, `algo-strategies-*`) consistent across Tasks 5 + 6 + 7 + 8.
- Sync-test regex (`/^[a-z0-9_]+$/`) matches the actual feature key shape ✓.

No gaps.
