// frontend/components/algo-trading/SettingsTab.tsx
"use client";
/**
 * Algo Trading — Settings tab. Slice 1 added the Fee Preview
 * widget; Slice 8b adds the Kill Switch toggle; V2-3 adds
 * the drift threshold lever.
 */

import { useEffect, useState } from "react";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

import { FeePreviewWidget } from "./FeePreviewWidget";
import { KillSwitchToggle } from "./KillSwitchToggle";

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

  useEffect(() => {
    if (data !== undefined) {
      setValue(data.threshold_shares);
    }
  }, [data]);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    try {
      const r = await apiFetch(`${API_URL}/algo/drift/threshold`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threshold_shares: value }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await mutate({ threshold_shares: value }, false);
      setSaved(true);
    } catch {
      // no-op; user retries
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
          disabled={saving}
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
      </div>
    </div>
  );
}

export function SettingsTab() {
  return (
    <div className="space-y-4">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Settings
      </h2>
      <KillSwitchToggle />
      <DriftThresholdInput />
      <FeePreviewWidget />
    </div>
  );
}
