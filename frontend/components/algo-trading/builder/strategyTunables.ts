/**
 * Walk a strategy root AST and surface every numeric leaf the
 * user can sensibly tune without changing rule logic. Used by
 * StrategyLeversPanel to render a "Strategy Logic" group with
 * one number input per tunable.
 *
 * Recognised tunables:
 *   • set_target_weight.weight       (0..1, step 0.01)
 *   • buy.qty.shares                 (>= 1)
 *   • sell.qty.shares                (>= 1)
 *   • compare.right.literal          (numeric only - when the
 *     literal is a string/bool we skip; tweaking those would
 *     change rule semantics)
 *
 * Each tunable carries a JSON-pointer-ish ``path`` rooted at
 * ``ast.root`` so callers can deep-set the new value back.
 * Labels include a breadcrumb so users can tell which compare
 * node a given literal belongs to.
 */

export type TunableKind = "weight" | "shares" | "literal";

export interface Tunable {
  path: string;        // dot-path under ast.root, no leading dot
  label: string;       // human-readable
  kind: TunableKind;
  value: number;
  min?: number;
  max?: number;
  step?: number;
}

type Node = Record<string, unknown>;

function describeOperand(op: unknown): string {
  if (op && typeof op === "object") {
    const o = op as Record<string, unknown>;
    if (typeof o.feature === "string") return o.feature;
    if (o.literal !== undefined) return String(o.literal);
  }
  return "?";
}

export function walkTunables(
  root: Node,
  basePath = "",
): Tunable[] {
  const out: Tunable[] = [];
  visit(root, basePath, out);
  return out;
}

function visit(
  node: unknown,
  path: string,
  out: Tunable[],
): void {
  if (!node || typeof node !== "object") return;
  const n = node as Record<string, unknown>;
  const type = String(n.type ?? "");

  if (type === "set_target_weight" && typeof n.weight === "number") {
    out.push({
      path: `${path}.weight`.replace(/^\./, ""),
      label: "Target weight (× equity)",
      kind: "weight",
      value: n.weight,
      min: 0,
      max: 1,
      step: 0.01,
    });
  }
  if (type === "buy" || type === "sell") {
    const qty = n.qty;
    if (
      qty && typeof qty === "object"
      && typeof (qty as Node).shares === "number"
    ) {
      out.push({
        path: `${path}.qty.shares`.replace(/^\./, ""),
        label: `${type[0].toUpperCase()}${type.slice(1)} qty (shares)`,
        kind: "shares",
        value: (qty as { shares: number }).shares,
        min: 1,
        max: 100000,
        step: 1,
      });
    }
  }
  if (type === "compare") {
    const right = n.right;
    if (
      right && typeof right === "object"
      && typeof (right as Node).literal === "number"
    ) {
      const leftLabel = describeOperand(n.left);
      const op = String(n.op ?? "?");
      out.push({
        path: `${path}.right.literal`.replace(/^\./, ""),
        label: `${leftLabel} ${op} ?`,
        kind: "literal",
        value: (right as { literal: number }).literal,
        // Compare-literals are open-ended (RSI <= 30, PScore >= 7,
        // days_ago <= 10 are all sensible). No min/max bounds.
      });
    }
  }

  // Recurse into AST child slots.
  if (n.cond) visit(n.cond, `${path}.cond`, out);
  if (n.then) visit(n.then, `${path}.then`, out);
  if (n.else) visit(n.else, `${path}.else`, out);
  if (n.operand) visit(n.operand, `${path}.operand`, out);
  if (Array.isArray(n.operands)) {
    n.operands.forEach((c, i) =>
      visit(c, `${path}.operands[${i}]`, out),
    );
  }
}

/**
 * Return a shallow-cloned copy of ``root`` with the value at
 * ``path`` replaced. Path syntax: dot-separated keys with
 * ``[index]`` for array elements (matches walkTunables output).
 */
export function setByPath<T extends Node>(
  root: T,
  path: string,
  value: unknown,
): T {
  const tokens = parsePath(path);
  return mutate(root, tokens, 0, value) as T;
}

function mutate(
  cur: unknown,
  tokens: PathToken[],
  i: number,
  value: unknown,
): unknown {
  if (i >= tokens.length) return value;
  const t = tokens[i];
  if (t.kind === "key") {
    const node = (cur ?? {}) as Record<string, unknown>;
    return {
      ...node,
      [t.name]: mutate(node[t.name], tokens, i + 1, value),
    };
  }
  // index
  const arr = Array.isArray(cur) ? [...cur] : [];
  arr[t.index] = mutate(arr[t.index], tokens, i + 1, value);
  return arr;
}

type PathToken =
  | { kind: "key"; name: string }
  | { kind: "index"; index: number };

function parsePath(path: string): PathToken[] {
  const out: PathToken[] = [];
  // Tokenise "a.b.operands[2].right.literal" -> key, key, key,
  // index, key, key.
  const matches = path.matchAll(/([^.[\]]+)|\[(\d+)\]/g);
  for (const m of matches) {
    if (m[2] !== undefined) {
      out.push({ kind: "index", index: Number(m[2]) });
    } else {
      out.push({ kind: "key", name: m[1] });
    }
  }
  return out;
}
