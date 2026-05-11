"use client";

import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  STRATEGIES_TAB_LABELS,
  STRATEGIES_TAB_ORDER,
  type StrategiesTabId,
} from "@/lib/types/algoTrading";

import { BacktestTab } from "@/components/algo-trading/BacktestTab";
import { InstrumentsTab } from "@/components/algo-trading/InstrumentsTab";
import { PaperTab } from "@/components/algo-trading/PaperTab";
import { DryRunTab } from "@/components/algo-trading/dryrun/DryRunTab";
import { PerformanceTab } from "@/components/algo-trading/PerformanceTab";
import { ReplayTab } from "@/components/algo-trading/ReplayTab";
import { StrategiesSettingsTab } from "@/components/algo-trading/StrategiesSettingsTab";
import { StrategiesTab } from "@/components/algo-trading/StrategiesTab";

const DEFAULT_TAB: StrategiesTabId = "instruments";

function isValidTab(v: string | null): v is StrategiesTabId {
  return (
    v !== null &&
    (STRATEGIES_TAB_ORDER as readonly string[]).includes(v)
  );
}

export default function StrategiesClient() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const active: StrategiesTabId = isValidTab(raw) ? raw : DEFAULT_TAB;

  const handleSwitch = useCallback(
    (next: StrategiesTabId) => {
      const params = new URLSearchParams(sp.toString());
      params.set("tab", next);
      router.replace(
        `/algo-trading/strategies?${params.toString()}`,
        { scroll: false },
      );
    },
    [router, sp],
  );

  const tabPanel = useMemo(() => {
    switch (active) {
      case "instruments":
        return <InstrumentsTab />;
      case "strategies":
        return <StrategiesTab />;
      case "backtest":
        return <BacktestTab />;
      case "paper":
        return <PaperTab />;
      case "dryrun":
        return <DryRunTab />;
      case "performance":
        return <PerformanceTab />;
      case "replay":
        return <ReplayTab />;
      case "settings":
        return <StrategiesSettingsTab />;
    }
  }, [active]);

  return (
    <div className="space-y-4 p-6">
      <h1
        className="text-xl font-semibold"
        data-testid="algo-strategies-heading"
      >
        Strategies
      </h1>

      <div
        role="tablist"
        data-testid="algo-strategies-tabs"
        className="flex flex-wrap items-center gap-1 border-b
          border-gray-200 dark:border-gray-700"
      >
        {STRATEGIES_TAB_ORDER.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={id === active}
            data-testid={`algo-strategies-tab-${id}`}
            onClick={() => handleSwitch(id)}
            className={`px-3 py-2 text-sm transition-colors ${
              id === active
                ? "border-b-2 border-indigo-500 text-indigo-600 dark:text-indigo-400 font-medium"
                : "text-gray-600 dark:text-gray-300 hover:text-indigo-600 dark:hover:text-indigo-400"
            }`}
          >
            {STRATEGIES_TAB_LABELS[id]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        data-testid={`algo-strategies-panel-${active}`}
        className="rounded-lg border border-gray-200
          dark:border-gray-700 bg-white dark:bg-gray-900 p-4"
      >
        {tabPanel}
      </div>
    </div>
  );
}
