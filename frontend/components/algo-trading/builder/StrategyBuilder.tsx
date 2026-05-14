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
import { CadenceProductPanel } from "./CadenceProductPanel";
import { JsonPane } from "./JsonPane";
import { NodePalette } from "./NodePalette";
import { StrategyLeversPanel } from "./StrategyLeversPanel";
import { TEMPLATES } from "./templates";
import { RegimeApplicabilityChips } from "../RegimeApplicabilityChips";
import { useRegimeCurrent } from "@/hooks/useRegime";
import {
  createStrategy,
  type StrategyAst,
  type StrategyMode,
} from "@/hooks/useStrategies";
import {
  upsertStrategyMetadata,
  useStrategyMetadata,
} from "@/hooks/useStrategyMetadata";
import {
  REGIME_LABELS,
  type RegimeLabel,
} from "@/lib/types/algoStrategy";

interface Props {
  initial?: StrategyAst | null;
  strategyId?: string | null;
  /** Current promotion-workflow mode (used to render the
   * auto-demote warning when editing a paper / live strategy). */
  currentMode?: StrategyMode | null;
  /** Active paper / live runtimes attached to this strategy. */
  activeRuntimeModes?: string[];
  /** Open position count from any active or recent runtime. */
  openPositionCount?: number;
  onSaved?: (id: string) => void;
  onCancel?: () => void;
}

export function StrategyBuilder({
  initial,
  strategyId,
  currentMode,
  activeRuntimeModes,
  openPositionCount,
  onSaved,
  onCancel,
}: Props) {
  const [ast, setAst] = useState<StrategyAst>(
    () => initial ?? TEMPLATES[0].ast,
  );
  const [name, setName] = useState(ast.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // REGIME-3 — applicable_regimes lives outside the AST (Strategy
  // Pydantic model has extra="forbid").  Default = all 3 regimes.
  const [applicableRegimes, setApplicableRegimes] = useState<
    RegimeLabel[]
  >(REGIME_LABELS);
  const { applicableRegimes: loadedRegimes } =
    useStrategyMetadata(strategyId ?? null);
  useEffect(() => {
    if (strategyId && loadedRegimes) {
      setApplicableRegimes(loadedRegimes);
    }
  }, [strategyId, loadedRegimes]);
  const { current: regimeCurrent } = useRegimeCurrent();
  const currentRegimeLower = regimeCurrent
    ? (regimeCurrent.regime_label.toLowerCase() as RegimeLabel)
    : undefined;

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
        // REGIME-3: pipe applicableRegimes through the same PUT
        // route — backend upserts metadata in the same session.
        await upsertStrategyMetadata(
          strategyId,
          applicableRegimes,
          ast as unknown as Record<string, unknown>,
        );
        onSaved?.(strategyId);
      } else {
        // Create flow: useStrategies.createStrategy() doesn't yet
        // accept metadata, so save AST first then PUT regimes if
        // they diverge from the default.
        const id = await createStrategy(ast);
        const isDefault =
          applicableRegimes.length === REGIME_LABELS.length &&
          REGIME_LABELS.every((r) => applicableRegimes.includes(r));
        if (!isDefault) {
          try {
            await upsertStrategyMetadata(
              id,
              applicableRegimes,
              ast as unknown as Record<string, unknown>,
            );
          } catch (metaErr) {
            // Metadata persistence failure is non-fatal — strategy
            // already exists with default (all-3) regimes.
            console.warn(
              "Strategy created but metadata upsert failed:",
              metaErr,
            );
          }
        }
        onSaved?.(id);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [ast, strategyId, onSaved, applicableRegimes]);

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
        {strategyId && currentMode && currentMode !== "draft" && (
          <DemoteWarningBanner
            currentMode={currentMode}
            activeRuntimeModes={activeRuntimeModes ?? []}
            openPositionCount={openPositionCount ?? 0}
          />
        )}
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="algo-builder-name"
          className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5 text-sm"
          placeholder="Strategy name"
        />
        <RegimeApplicabilityChips
          selected={applicableRegimes}
          onChange={setApplicableRegimes}
          currentRegime={currentRegimeLower}
        />
        <CadenceProductPanel ast={ast} onChange={setAst} />
        <StrategyLeversPanel ast={ast} onChange={setAst} />
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

function DemoteWarningBanner({
  currentMode,
  activeRuntimeModes,
  openPositionCount,
}: {
  currentMode: StrategyMode;
  activeRuntimeModes: string[];
  openPositionCount: number;
}) {
  const hasRuntime = activeRuntimeModes.length > 0;
  return (
    <div
      role="alert"
      data-testid="algo-builder-demote-warning"
      className="rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-3 text-xs space-y-1"
    >
      <div className="font-medium text-amber-800 dark:text-amber-300">
        This {currentMode} strategy will be demoted to draft on save.
      </div>
      <p className="text-amber-700 dark:text-amber-400">
        Run backtest + walk-forward (and a fresh paper run for live)
        to re-promote — or use the bypass card on the Promote dialog
        if this strategy has been live before.
      </p>
      {hasRuntime && (
        <p className="text-amber-700 dark:text-amber-400">
          Active {activeRuntimeModes.join(" + ")} runtime is using
          the previous AST in memory and will keep doing so until
          it is restarted. Your edits will not affect it.
        </p>
      )}
      {openPositionCount > 0 && (
        <p className="text-rose-700 dark:text-rose-400">
          ⚠ {openPositionCount} open position(s) tied to prior
          runtimes. A new runtime started after re-promotion may
          interact with them — close positions first if you want
          a clean slate.
        </p>
      )}
    </div>
  );
}
