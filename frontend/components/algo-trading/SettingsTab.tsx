// frontend/components/algo-trading/SettingsTab.tsx
"use client";
/**
 * Algo Trading — Settings tab. Slice 1 adds the Fee Preview
 * widget; later slices bring risk caps + the kill switch.
 */

import { FeePreviewWidget } from "./FeePreviewWidget";

export function SettingsTab() {
  return (
    <div className="space-y-4">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Settings
      </h2>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Fee model preview. Risk caps, fee-version pinning, and
        the kill switch will appear here as the epic progresses.
      </p>
      <FeePreviewWidget />
    </div>
  );
}
