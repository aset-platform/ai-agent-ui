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
