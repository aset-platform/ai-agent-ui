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
