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
