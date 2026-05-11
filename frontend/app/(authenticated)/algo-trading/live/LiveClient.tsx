"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import { HoldingsTab } from "@/components/algo-trading/live/HoldingsTab";
import { LiveDashboard } from "@/components/algo-trading/live/LiveDashboard";
import { LiveHeaderStrip } from "@/components/algo-trading/live/LiveHeaderStrip";
import { LiveSettingsTab } from "@/components/algo-trading/live/LiveSettingsTab";
import { PositionsTab } from "@/components/algo-trading/live/PositionsTab";
import {
  LIVE_TAB_LABELS,
  LIVE_TAB_ORDER,
  type LiveTabId,
} from "@/lib/types/algoTrading";

const DEFAULT_TAB: LiveTabId = "live";

function isValidLiveTab(v: string | null): v is LiveTabId {
  return (
    v !== null && (LIVE_TAB_ORDER as readonly string[]).includes(v)
  );
}

export default function LiveClient() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const active: LiveTabId = isValidLiveTab(raw) ? raw : DEFAULT_TAB;

  const switchTo = useCallback(
    (next: LiveTabId) => {
      const params = new URLSearchParams(sp.toString());
      params.set("tab", next);
      router.replace(
        `/algo-trading/live?${params.toString()}`,
        { scroll: false },
      );
    },
    [router, sp],
  );

  const panel = useMemo(() => {
    switch (active) {
      case "live":
        return <LiveDashboard />;
      case "positions":
        return <PositionsTab />;
      case "holdings":
        return <HoldingsTab />;
      case "settings":
        return <LiveSettingsTab />;
    }
  }, [active]);

  return (
    <div className="flex flex-col" data-testid="algo-live-page">
      <LiveHeaderStrip />
      <div className="p-4 space-y-3">
        <div
          role="tablist"
          data-testid="algo-live-tabs"
          className="flex flex-wrap items-center gap-1 border-b
            border-gray-200 dark:border-gray-700"
        >
          {LIVE_TAB_ORDER.map((id) => (
            <button
              key={id}
              role="tab"
              type="button"
              aria-selected={id === active}
              data-testid={`algo-live-tab-${id}`}
              onClick={() => switchTo(id)}
              className={`px-3 py-2 text-sm transition-colors ${
                id === active
                  ? "border-b-2 border-rose-500 text-rose-600 dark:text-rose-400 font-medium"
                  : "text-gray-600 dark:text-gray-300 hover:text-rose-600 dark:hover:text-rose-400"
              }`}
            >
              {LIVE_TAB_LABELS[id]}
            </button>
          ))}
        </div>
        <div
          role="tabpanel"
          data-testid={`algo-live-panel-${active}`}
          className="rounded-lg border border-gray-200
            dark:border-gray-700 bg-white dark:bg-gray-900 p-4"
        >
          {panel}
        </div>
      </div>
    </div>
  );
}
