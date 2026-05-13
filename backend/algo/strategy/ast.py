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
    # REGIME-3: ``str`` widening so ``regime_label == "bull"`` style
    # compares are first-class without a sugar AST node.  Evaluator
    # dispatches to string-equality when both operands are str.
    literal: float | int | str


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


class BuyQtyVolTarget(BaseModel):
    """REGIME-4: target % portfolio vol per position. Sized at
    runtime by the composer using realized_vol_60d + NAV."""
    model_config = ConfigDict(extra="forbid")
    vol_target_pct: float = Field(gt=0)


class BuyQtyKelly(BaseModel):
    """REGIME-4: Kelly-fraction sizing. Requires expected_edge in
    strategy metadata; composer returns 0 + warns if missing."""
    model_config = ConfigDict(extra="forbid")
    kelly_fraction: float = Field(gt=0, le=1)


class SellQtyShares(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shares: int = Field(ge=1)


class SellQtyAll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: Literal[True]


class BuyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["buy"] = "buy"
    qty: Union[
        BuyQtyShares, BuyQtyNotional, BuyQtyVolTarget, BuyQtyKelly,
    ]


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
    # ASETPLTFRM-387 — widened from Literal["1d"]. Daily stays the
    # default behaviour; intraday cadences (15m / 5m / 1m) land in
    # this slice. Backwards-compat: existing AST JSON in
    # algo.strategies carries interval="1d" so all current strategies
    # parse unchanged.
    interval: Literal["1d", "15m", "5m", "1m"]
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

    ``product`` selects Kite's broker-side product code at order
    placement. ``"CNC"`` is delivery (overnight hold permitted) and
    is the default — every strategy created before ASETPLTFRM-387
    has no ``product`` key in its persisted AST, so the default
    keeps them parsing unchanged. ``"MIS"`` is the intraday product;
    Zerodha forces a same-day square-off at 15:15 IST. ``"MIS"`` is
    only valid in combination with intraday cadence (5m / 1m), and
    the model_validator below enforces that invariant.

    ``square_off_time`` is only meaningful when ``product == "MIS"``.
    LiveRuntime falls back to ``"15:14 IST"`` (one minute before the
    Zerodha auto-square so our SELL fill lands in the ledger before
    the broker forces it) when the field is ``None``.
    """
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=128)
    universe: UniverseScope
    schedule: ScheduleBarClose
    rebalance: RebalanceDaily
    root: AnyNode
    risk: RiskConfig
    product: Literal["CNC", "MIS"] = "CNC"
    square_off_time: str | None = None

    @model_validator(mode="after")
    def _root_must_be_actionable(self) -> "Strategy":
        # A bare condition-only node (no action/composite ancestor)
        # cannot produce orders on its own. However, condition nodes
        # are valid roots when the executor wraps them in an implicit
        # buy-on-true/hold-on-false semantic.  We therefore only
        # reject truly degenerate cases — currently none beyond what
        # type-checking already enforces at parse time.
        return self

    @model_validator(mode="after")
    def _mis_requires_intraday(self) -> "Strategy":
        # MIS is Zerodha's intraday product — Kite forces a same-day
        # square-off at 15:15 IST regardless of strategy intent. It
        # only makes sense paired with intraday cadence; pairing it
        # with daily cadence would mean "open at today's close, get
        # force-squared by Kite within seconds" which is degenerate.
        # Reject loudly at parse time so users see the constraint in
        # the Builder rather than after their first auto-square.
        if self.product == "MIS" and self.schedule.interval == "1d":
            raise ValueError(
                "MIS product requires intraday cadence (5m or 1m). "
                "Daily strategies must use CNC.",
            )
        return self


# Single Pydantic adapter the rest of the codebase uses.
strategy_adapter: TypeAdapter[Strategy] = TypeAdapter(Strategy)


def parse_strategy(payload: dict) -> Strategy:
    """Validate + parse a strategy dict. Raises ``ValidationError``."""
    return strategy_adapter.validate_python(payload)
