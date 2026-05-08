// frontend/components/algo-trading/SettingsTab.tsx
"use client";
/**
 * Algo Trading — Settings tab. Slice 1 added the Fee Preview
 * widget; Slice 8b adds the Kill Switch toggle. Risk caps + fee-
 * version pinning land in later slices.
 */

import { FeePreviewWidget } from "./FeePreviewWidget";
import { KillSwitchToggle } from "./KillSwitchToggle";

export function SettingsTab() {
  return (
    <div className="space-y-4">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Settings
      </h2>
      <KillSwitchToggle />
      <FeePreviewWidget />
    </div>
  );
}
