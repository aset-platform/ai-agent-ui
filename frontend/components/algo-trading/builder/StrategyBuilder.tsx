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
