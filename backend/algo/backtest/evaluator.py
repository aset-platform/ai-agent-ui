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


class Evaluator:
    """Stateless dispatcher. Construct once per backtest run."""

    def eval_node(self, node: dict, ctx: EvalContext):  # noqa: ANN201
        t = node.get("type")
        if t == "compare":
            left = _resolve_operand(node["left"], ctx)
            right = _resolve_operand(node["right"], ctx)
            return _OPS[node["op"]](left, right)
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
        # Action nodes pass through verbatim — runner translates
        # to OrderIntents.
        if t in {
            "buy", "sell", "exit", "hold", "set_target_weight",
        }:
            return dict(node)
        # crossover / between / select_top_n / weighted are v2.
        # In v1 the evaluator returns "hold" so the runner
        # gracefully no-ops.
        return {"type": "hold"}
