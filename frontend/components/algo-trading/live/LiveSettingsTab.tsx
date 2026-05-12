"use client";
/**
 * LiveSettingsTab — `/algo-trading/live?tab=settings`.
 *
 * Live-only settings for the per-account safety surface:
 *   • KillSwitchToggle  — global kill switch
 *   • DriftThresholdInput — broker-vs-engine drift sensitivity
 *   • Per-strategy live arming card (strategy picker +
 *     LiveModeToggle 4-gate switch + LiveSafetyBeltsForm caps)
 *
 * DriftThresholdInput is kept local (was previously housed in the
 * deleted `algo-trading/SettingsTab.tsx`); plan §Slice 5 keeps this
 * tab self-contained.
 */

import { useEffect, useState } from "react";

import useSWR from "swr";

import { useStrategies } from "@/hooks/useStrategies";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

import { KillSwitchToggle } from "../KillSwitchToggle";
import { LiveModeToggle } from "../LiveModeToggle";
import { LiveSafetyBeltsForm } from "../LiveSafetyBeltsForm";

async function fetchThreshold(
  url: string,
): Promise<{ threshold_shares: number }> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

const THRESHOLD_KEY = `${API_URL}/algo/drift/threshold`;

function DriftThresholdInput() {
  const { data, mutate } = useSWR(
    THRESHOLD_KEY,
    fetchThreshold,
    { revalidateOnFocus: false },
  );
  const [value, setValue] = useState<number>(0);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (data !== undefined) {
      setValue(data.threshold_shares);
    }
  }, [data]);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    setSaveError(null);
    try {
      const r = await apiFetch(THRESHOLD_KEY, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threshold_shares: value }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await mutate({ threshold_shares: value }, { revalidate: false });
      setSaved(true);
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : "Save failed",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="rounded-md border border-slate-200 p-4 dark:border-slate-700"
      data-testid="drift-threshold-widget"
    >
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        Drift threshold
      </h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Minimum share discrepancy to flag as a position drift.
        0 = any non-zero difference triggers an alert.
      </p>
      <div className="mt-3 flex items-center gap-3">
        <input
          type="number"
          min={0}
          value={value}
          onChange={(e) =>
            setValue(Math.max(0, Number(e.target.value)))
          }
          className="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
          data-testid="drift-threshold-input"
        />
        <span className="text-xs text-slate-500">shares</span>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || data === undefined}
          className="rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="drift-threshold-save"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {saved && (
          <span className="text-xs text-green-600 dark:text-green-400">
            Saved
          </span>
        )}
        {saveError && (
          <span
            className="text-xs text-red-600 dark:text-red-400"
            data-testid="drift-threshold-error"
          >
            {saveError}
          </span>
        )}
      </div>
    </div>
  );
}

// ASETPLTFRM-378 — DryRunArmControl was removed from this tab.
// The Dry-Run toggle belongs only on Strategies → Dry-run tab.
// The Live page no longer reads the per-user Redis dry-run flag;
// slice C-backend pins dry_run=False explicitly at KiteClient
// construction time for mode="live" spawns.

export function LiveSettingsTab() {
  const { strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");
  // If a strategy is archived elsewhere while open, `selected` becomes
  // undefined and the per-strategy card silently hides. Acceptable for
  // now; revisit if multi-tab strategy lifecycle becomes a UX issue.
  const selected = strategies.find((s) => s.id === strategyId);

  return (
    <div className="space-y-4" data-testid="live-settings-tab">
      <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
        Live Settings
      </h2>

      <KillSwitchToggle />
      <DriftThresholdInput />

      <div
        className="rounded-md border border-slate-200 p-4 dark:border-slate-700"
        data-testid="live-arming-card"
      >
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Per-strategy live arming
        </h3>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Use the 4-gate toggle below to enable live order placement
          for a strategy. All four gates must pass server-side; the
          toggle is a convenience.
        </p>
        <label className="mt-3 flex flex-col gap-0.5">
          <span className="text-[11px] text-slate-500">Strategy</span>
          <select
            value={strategyId}
            onChange={(e) => setStrategyId(e.target.value)}
            className="w-64 rounded border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800"
            data-testid="live-settings-strategy-select"
          >
            <option value="">Select strategy…</option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        {strategyId && selected && (
          <div className="mt-3 space-y-3">
            <LiveModeToggle
              strategyId={strategyId}
              strategyName={selected.name}
            />
            <LiveSafetyBeltsForm strategyId={strategyId} />
          </div>
        )}
      </div>
    </div>
  );
}
