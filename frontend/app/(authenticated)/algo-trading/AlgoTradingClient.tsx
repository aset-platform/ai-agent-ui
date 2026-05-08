// frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx
"use client";
/**
 * Algo Trading — client subtree. Renders the tab strip and
 * the active tab's content. URL-synced via ?tab=. Mirrors the
 * AdvancedAnalyticsClient pattern (single page, eight tabs).
 *
 * Slice 0 ships the scaffold + Settings tab; subsequent slices
 * replace each placeholder.
 */

import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  ALGO_TAB_LABELS,
  ALGO_TAB_ORDER,
  type AlgoTabId,
} from "@/lib/types/algoTrading";

import { SettingsTab } from "@/components/algo-trading/SettingsTab";

const DEFAULT_TAB: AlgoTabId = "settings";

function isValidTab(v: string | null): v is AlgoTabId {
  return v !== null && (ALGO_TAB_ORDER as readonly string[]).includes(v);
}

export default function AlgoTradingClient() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const active: AlgoTabId = isValidTab(raw) ? raw : DEFAULT_TAB;

  const handleSwitch = useCallback(
    (next: AlgoTabId) => {
      const params = new URLSearchParams(sp.toString());
      params.set("tab", next);
      router.replace(`/algo-trading?${params.toString()}`, {
        scroll: false,
      });
    },
    [router, sp],
  );

  const tabPanel = useMemo(() => {
    switch (active) {
      case "settings":
        return <SettingsTab />;
      default:
        return <PlaceholderTab id={active} />;
    }
  }, [active]);

  return (
    <div className="space-y-4 p-6">
      <h1
        className="text-xl font-semibold"
        data-testid="algo-trading-heading"
      >
        Algo Trading
      </h1>

      <div
        role="tablist"
        data-testid="algo-trading-tabs"
        className="flex flex-wrap items-center gap-1 border-b border-gray-200 dark:border-gray-700"
      >
        {ALGO_TAB_ORDER.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={id === active}
            data-testid={`algo-trading-tab-${id}`}
            onClick={() => handleSwitch(id)}
            className={`px-3 py-2 text-sm transition-colors ${
              id === active
                ? "border-b-2 border-indigo-500 text-indigo-600 dark:text-indigo-400 font-medium"
                : "text-gray-600 dark:text-gray-300 hover:text-indigo-600 dark:hover:text-indigo-400"
            }`}
          >
            {ALGO_TAB_LABELS[id]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        data-testid={`algo-trading-panel-${active}`}
        className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4"
      >
        {tabPanel}
      </div>
    </div>
  );
}

function PlaceholderTab({ id }: { id: AlgoTabId }) {
  return (
    <div className="space-y-2">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        {ALGO_TAB_LABELS[id]}
      </h2>
      <p className="text-sm text-gray-500 dark:text-gray-400">
        This tab will be implemented in a later slice of the
        Algo Trading epic.
      </p>
    </div>
  );
}
