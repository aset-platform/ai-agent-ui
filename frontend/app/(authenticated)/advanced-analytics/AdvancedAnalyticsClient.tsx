"use client";
/**
 * Advanced Analytics — client component (Sprint 9 AA-10).
 * Holds the tab strip + URL sync; per-tab body is the
 * appropriate ``<*Tab />`` from ``components/advanced-
 * analytics/`` (built in AA-11).
 *
 * URL sync mirrors `frontend/app/(authenticated)/admin/page.tsx:2098`:
 *
 *   `?tab=current-day-upmove`
 *
 * `useSearchParams` forces the inner subtree client-only —
 * wrap in `<Suspense>` at the page level w/ a static `<h1>`
 * fallback (§5.3 suspense-fallback-null-ssr-hole) so the
 * SSR HTML still has an LCP candidate.
 */

import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  ADVANCED_TAB_LABELS,
  ADVANCED_TAB_ORDER,
  type AdvancedReportResponse,
  type AdvancedTabId,
} from "@/lib/types/advancedAnalytics";

import { CurrentDayUpmoveTab } from "@/components/advanced-analytics/CurrentDayUpmoveTab";
import { HelpTab } from "@/components/advanced-analytics/HelpTab";
import { PreviousDayBreakoutTab } from "@/components/advanced-analytics/PreviousDayBreakoutTab";
import { MomVolumeDeliveryTab } from "@/components/advanced-analytics/MomVolumeDeliveryTab";
import { WowVolumeDeliveryTab } from "@/components/advanced-analytics/WowVolumeDeliveryTab";
import { TwoDayScanTab } from "@/components/advanced-analytics/TwoDayScanTab";
import { ThreeDayScanTab } from "@/components/advanced-analytics/ThreeDayScanTab";
import { SwingSetupsTab } from "@/components/advanced-analytics/SwingSetupsTab";
import { Top50DeliveryByQtyTab } from "@/components/advanced-analytics/Top50DeliveryByQtyTab";

interface AdvancedAnalyticsClientProps {
  /** First-tab response prefetched in the RSC for SWR fallbackData. */
  initialData?: AdvancedReportResponse;
}

function isValidTab(value: string | null): value is AdvancedTabId {
  if (!value) return false;
  return (ADVANCED_TAB_ORDER as readonly string[]).includes(value);
}

export default function AdvancedAnalyticsClient({
  initialData,
}: AdvancedAnalyticsClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const requestedTab = searchParams.get("tab");
  const initialTab: AdvancedTabId = isValidTab(requestedTab)
    ? requestedTab
    : ADVANCED_TAB_ORDER[0];

  const [tab, setTab] = useState<AdvancedTabId>(initialTab);

  const handleTabChange = useCallback(
    (next: AdvancedTabId) => {
      setTab(next);
      router.replace(`/advanced-analytics?tab=${next}`, {
        scroll: false,
      });
    },
    [router],
  );

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <header className="flex items-baseline justify-between gap-4">
        <h1
          className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-100"
          data-testid="advanced-analytics-heading"
        >
          Advanced Analytics
        </h1>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Pro &amp; superuser · 7 NSE-bhavcopy reports + column reference
        </p>
      </header>

      <div
        className="flex flex-wrap gap-1 border-b border-gray-200 dark:border-gray-700 pb-px"
        data-testid="advanced-analytics-tabs"
        role="tablist"
      >
        {ADVANCED_TAB_ORDER.map((id) => {
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              data-testid={`advanced-analytics-tab-${id}`}
              onClick={() => handleTabChange(id)}
              className={`whitespace-nowrap px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${
                active
                  ? "text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-600 dark:border-indigo-400 -mb-px"
                  : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              }`}
            >
              {ADVANCED_TAB_LABELS[id]}
            </button>
          );
        })}
      </div>

      <div
        className="min-h-[600px]"
        data-testid={`advanced-analytics-panel-${tab}`}
      >
        {tab === "current-day-upmove" && (
          <CurrentDayUpmoveTab initialData={initialData} />
        )}
        {tab === "previous-day-breakout" && <PreviousDayBreakoutTab />}
        {tab === "mom-volume-delivery" && <MomVolumeDeliveryTab />}
        {tab === "wow-volume-delivery" && <WowVolumeDeliveryTab />}
        {tab === "two-day-scan" && <TwoDayScanTab />}
        {tab === "three-day-scan" && <ThreeDayScanTab />}
        {tab === "top-50-delivery-by-qty" && <Top50DeliveryByQtyTab />}
        {tab === "swing-setups" && <SwingSetupsTab />}
        {tab === "help" && <HelpTab />}
      </div>
    </div>
  );
}
