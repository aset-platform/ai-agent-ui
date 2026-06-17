/**
 * Visual condition editor — types + AST serialization.
 *
 * Covers the common "if (entry_cond) { entry_action } else { exit }"
 * pattern that most strategies use. Complex nested ASTs (select_top_n,
 * weighted, deeply nested ifs) remain JSON-only.
 */

import { STRATEGY_FEATURES } from "../strategyFeatureCatalog";

// ─── Visual types ───────────────────────────────────────────────

export type ConditionOp = "<" | "<=" | "==" | "!=" | ">=" | ">";

export interface ConditionRow {
  id: string;
  leftFeature: string;
  op: ConditionOp;
  rightType: "literal" | "feature";
  rightLiteral: string;
  rightFeature: string;
}

export interface ConditionGroup {
  combinator: "and" | "or";
  rows: ConditionRow[];
}

export interface EntryAction {
  type: "set_target_weight" | "buy";
  weight: string;
}

export interface ExitAction {
  type: "exit" | "hold";
  scope: "this_symbol" | "all_open";
}

export interface VisualSpec {
  entryGroup: ConditionGroup;
  entryAction: EntryAction;
  hasExitCondition: boolean;
  exitGroup: ConditionGroup;
  exitAction: ExitAction;
  fallbackAction: ExitAction;
}

// ─── Helpers ────────────────────────────────────────────────────

export function makeDefaultRow(): ConditionRow {
  return {
    id: crypto.randomUUID(),
    leftFeature: "rsi",
    op: "<",
    rightType: "literal",
    rightLiteral: "30",
    rightFeature: "",
  };
}

export function makeDefaultVisualSpec(): VisualSpec {
  return {
    entryGroup: { combinator: "and", rows: [makeDefaultRow()] },
    entryAction: { type: "set_target_weight", weight: "0.10" },
    hasExitCondition: false,
    exitGroup: { combinator: "and", rows: [makeDefaultRow()] },
    exitAction: { type: "exit", scope: "this_symbol" },
    fallbackAction: { type: "hold", scope: "this_symbol" },
  };
}

// ─── AST → VisualSpec ───────────────────────────────────────────

type Rec = Record<string, unknown>;

function parseCompareToRow(node: Rec): ConditionRow | null {
  if (node.type !== "compare") return null;
  const left = node.left as Rec | undefined;
  const right = node.right as Rec | undefined;
  if (!left || !("feature" in left)) return null;

  const rightType: "literal" | "feature" =
    right && "feature" in right ? "feature" : "literal";
  return {
    id: crypto.randomUUID(),
    leftFeature: String(left.feature),
    op: (node.op as ConditionOp) ?? ">",
    rightType,
    rightLiteral:
      rightType === "literal" ? String(right?.literal ?? "") : "",
    rightFeature:
      rightType === "feature" ? String((right as Rec).feature ?? "") : "",
  };
}

function parseCondToGroup(node: Rec): ConditionGroup | null {
  if (node.type === "compare") {
    const row = parseCompareToRow(node);
    return row ? { combinator: "and", rows: [row] } : null;
  }
  if (node.type === "and" || node.type === "or") {
    const operands = (node.operands as Rec[] | undefined) ?? [];
    const rows: ConditionRow[] = [];
    for (const op of operands) {
      if (op.type !== "compare") return null;
      const row = parseCompareToRow(op);
      if (!row) return null;
      rows.push(row);
    }
    return { combinator: node.type as "and" | "or", rows };
  }
  return null;
}

function parseEntryAction(node: Rec): EntryAction | null {
  if (node.type === "set_target_weight") {
    return { type: "set_target_weight", weight: String(node.weight ?? "0.10") };
  }
  if (node.type === "buy") {
    return { type: "buy", weight: "1" };
  }
  return null;
}

function parseExitAction(node: Rec): ExitAction {
  if (node.type === "exit") {
    return {
      type: "exit",
      scope: (node.scope as ExitAction["scope"]) ?? "this_symbol",
    };
  }
  return { type: "hold", scope: "this_symbol" };
}

export function astRootToVisualSpec(root: unknown): VisualSpec | null {
  if (typeof root !== "object" || root === null) return null;
  const node = root as Rec;
  if (node.type !== "if") return null;

  const entryGroup = parseCondToGroup(node.cond as Rec);
  if (!entryGroup) return null;

  const entryAction = parseEntryAction(node.then as Rec);
  if (!entryAction) return null;

  const elseNode = node.else as Rec | undefined;

  if (!elseNode) {
    return {
      entryGroup,
      entryAction,
      hasExitCondition: false,
      exitGroup: { combinator: "and", rows: [makeDefaultRow()] },
      exitAction: { type: "exit", scope: "this_symbol" },
      fallbackAction: { type: "hold", scope: "this_symbol" },
    };
  }

  if (elseNode.type === "if") {
    const exitGroup = parseCondToGroup(elseNode.cond as Rec);
    if (!exitGroup) return null;
    return {
      entryGroup,
      entryAction,
      hasExitCondition: true,
      exitGroup,
      exitAction: parseExitAction(elseNode.then as Rec),
      fallbackAction: parseExitAction((elseNode.else as Rec) ?? { type: "hold" }),
    };
  }

  return {
    entryGroup,
    entryAction,
    hasExitCondition: false,
    exitGroup: { combinator: "and", rows: [makeDefaultRow()] },
    exitAction: parseExitAction(elseNode),
    fallbackAction: { type: "hold", scope: "this_symbol" },
  };
}

// ─── VisualSpec → AST ───────────────────────────────────────────

function rowToAstCompare(row: ConditionRow): Rec {
  const feat = STRATEGY_FEATURES.find((f) => f.key === row.leftFeature);
  const isString = feat?.type === "string";

  const right: Rec =
    row.rightType === "feature"
      ? { feature: row.rightFeature }
      : isString
        ? { literal: row.rightLiteral }
        : { literal: parseFloat(row.rightLiteral) || 0 };

  return {
    type: "compare",
    left: { feature: row.leftFeature },
    op: row.op,
    right,
  };
}

function condGroupToAst(group: ConditionGroup): Rec {
  const valid = group.rows.filter((r) => r.leftFeature);
  if (valid.length === 0) return { type: "hold" };
  if (valid.length === 1) return rowToAstCompare(valid[0]);
  return {
    type: group.combinator,
    operands: valid.map(rowToAstCompare),
  };
}

function entryActionToAst(action: EntryAction): Rec {
  if (action.type === "set_target_weight") {
    return {
      type: "set_target_weight",
      weight: parseFloat(action.weight) || 0.1,
    };
  }
  return { type: "buy" };
}

function exitActionToAst(action: ExitAction): Rec {
  if (action.type === "exit") {
    return { type: "exit", scope: action.scope ?? "this_symbol" };
  }
  return { type: "hold" };
}

export function visualSpecToAstRoot(spec: VisualSpec): Rec {
  const cond = condGroupToAst(spec.entryGroup);
  const then = entryActionToAst(spec.entryAction);

  let elseNode: Rec;
  if (spec.hasExitCondition && spec.exitGroup.rows.length > 0) {
    elseNode = {
      type: "if",
      cond: condGroupToAst(spec.exitGroup),
      then: exitActionToAst(spec.exitAction),
      else: exitActionToAst(spec.fallbackAction),
    };
  } else {
    elseNode = exitActionToAst(spec.exitAction);
  }

  return { type: "if", cond, then, else: elseNode };
}
