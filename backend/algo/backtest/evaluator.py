"""AST evaluator — per-bar dispatch.

Pure functions. Inputs: a node dict + an EvalContext (current
ticker + bar date + feature map + open position qty). Outputs:
a primitive (bool for conditions; an action dict for actions /
composites that resolve to actions).

The runner calls ``evaluator.eval_node(strategy_root, ctx)``
once per (ticker, bar) and translates returned action dicts
into OrderIntents.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class EvalContext:
    ticker: str
    bar_date: date
    features: dict[str, Decimal]
    open_qty: int  # current PositionTracker qty for this ticker


_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}


def _resolve_operand(op: dict, ctx: EvalContext) -> Decimal:
    if "feature" in op:
        feature = op["feature"]
        if feature not in ctx.features:
            raise KeyError(f"Feature not in context: {feature}")
        return ctx.features[feature]
    if "literal" in op:
        return Decimal(str(op["literal"]))
    raise ValueError(
        f"Operand has neither feature nor literal: {op}"
    )


def _resolve_operand_raw(op: dict, ctx: EvalContext):  # noqa: ANN201
    """REGIME-3 string-aware resolver.

    Returns the operand's underlying type without coercing to Decimal.
    String features (``regime_label``) and string literals come back
    as ``str``; everything else goes through the existing Decimal
    path of ``_resolve_operand``.
    """
    if "literal" in op:
        v = op["literal"]
        if isinstance(v, str):
            return v
        return Decimal(str(v))
    if "feature" in op:
        feature = op["feature"]
        if feature not in ctx.features:
            raise KeyError(f"Feature not in context: {feature}")
        v = ctx.features[feature]
        if isinstance(v, str):
            return v
        return v  # already Decimal-ish
    raise ValueError(
        f"Operand has neither feature nor literal: {op}"
    )


class Evaluator:
    """Stateless dispatcher. Construct once per backtest run."""

    def eval_node(self, node: dict, ctx: EvalContext):  # noqa: ANN201
        t = node.get("type")
        if t == "compare":
            # REGIME-3: string-aware fast-path for ``regime_label ==
            # "bull"`` style compares.  Both operands must be strings;
            # mixed types raise loudly to surface upstream typos.
            left_raw = _resolve_operand_raw(node["left"], ctx)
            right_raw = _resolve_operand_raw(node["right"], ctx)
            left_is_str = isinstance(left_raw, str)
            right_is_str = isinstance(right_raw, str)
            if left_is_str or right_is_str:
                if not (left_is_str and right_is_str):
                    raise ValueError(
                        "Mixed string/numeric compare: "
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
            # Numeric path — unchanged.
            return _OPS[node["op"]](left_raw, right_raw)
        if t == "and":
            return all(
                bool(self.eval_node(c, ctx))
                for c in node["operands"]
            )
        if t == "or":
            return any(
                bool(self.eval_node(c, ctx))
                for c in node["operands"]
            )
        if t == "not":
            return not bool(self.eval_node(node["operand"], ctx))
        if t == "if":
            cond = bool(self.eval_node(node["cond"], ctx))
            branch = (
                node["then"] if cond
                else node.get("else", {"type": "hold"})
            )
            return self.eval_node(branch, ctx)
        if t == "between":
            # Inclusive numeric range check used by regime templates.
            value = _resolve_operand(node["value"], ctx)
            low = _resolve_operand(node["low"], ctx)
            high = _resolve_operand(node["high"], ctx)
            return low <= value <= high
        # Action nodes pass through verbatim — runner translates
        # to OrderIntents.
        if t in {
            "buy", "sell", "exit", "hold", "set_target_weight",
        }:
            return dict(node)
        # crossover / select_top_n / weighted are v2. In v1 the
        # evaluator returns "hold" so the runner gracefully no-ops.
        return {"type": "hold"}
