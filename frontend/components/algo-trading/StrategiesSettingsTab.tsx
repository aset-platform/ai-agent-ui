"use client";

import { FeePreviewWidget } from "./FeePreviewWidget";

/**
 * Strategies → Settings tab. Hosts ONLY the configuration
 * used by Backtest + Paper runs (fee preview, slippage). Live
 * risk knobs (kill switch, drift threshold, safety belts) live
 * on Live Trading → Settings instead.
 */
export function StrategiesSettingsTab() {
  return (
    <div className="space-y-4" data-testid="strategies-settings-tab">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Settings
      </h2>
      <FeePreviewWidget />
    </div>
  );
}
