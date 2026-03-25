"use client";
/**
 * Dismissible upgrade banner — shown when monthly quota is exhausted.
 *
 * Fetches subscription status via SWR (5-min revalidation) and
 * renders a compact amber banner when usage_remaining === 0.
 */

import { useState } from "react";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

interface SubscriptionInfo {
  tier: string;
  status: string;
  usage_count: number;
  usage_limit: number;
  usage_remaining: number | null;
}

interface UpgradeBannerProps {
  onUpgrade: () => void;
}

const fetcher = async (url: string) => {
  const res = await apiFetch(url);
  if (!res.ok) return null;
  return res.json();
};

export function UpgradeBanner({ onUpgrade }: UpgradeBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  const { data: sub } = useSWR<SubscriptionInfo | null>(
    `${API_URL}/subscription`,
    fetcher,
    { refreshInterval: 300_000, dedupingInterval: 60_000 },
  );

  // Don't show if: dismissed, loading, unlimited, or quota available
  if (
    dismissed ||
    !sub ||
    sub.usage_remaining === null ||
    sub.usage_remaining > 0
  ) {
    return null;
  }

  return (
    <div className="flex items-center justify-between gap-3 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800 px-4 py-2">
      <p className="text-sm text-amber-800 dark:text-amber-200">
        You&apos;ve used all{" "}
        <span className="font-semibold">{sub.usage_limit}</span>{" "}
        analyses this month.{" "}
        <button
          onClick={onUpgrade}
          className="font-semibold underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-100 transition-colors"
        >
          Upgrade your plan
        </button>{" "}
        for more.
      </p>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 p-1 text-amber-600 dark:text-amber-400 hover:text-amber-800 dark:hover:text-amber-200 transition-colors rounded"
        aria-label="Dismiss"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
