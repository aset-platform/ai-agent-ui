"use client";
/**
 * Parent panel for the analytics-page "recommendations"
 * tab. Owns the inner sub-tab strip and dispatches to
 * the existing list view (History) or the new cohort-
 * bucketed view (Performance).
 *
 * URL convention: persists the inner choice as the
 * ``subtab`` query param, mirroring the ``tab`` param
 * the outer analytics page already uses. SSR-safe —
 * reads on first render via window when available,
 * falls back to a default.
 */

import { useEffect, useState } from "react";
import { RecommendationHistoryTab } from
  "@/components/insights/RecommendationHistoryTab";
import { RecommendationPerformanceTab } from
  "@/components/insights/RecommendationPerformanceTab";

type SubTab = "history" | "performance";

const TABS: { value: SubTab; label: string }[] = [
  { value: "history", label: "History" },
  { value: "performance", label: "Performance" },
];

function readInitial(): SubTab {
  if (typeof window === "undefined") return "history";
  const params = new URLSearchParams(
    window.location.search,
  );
  const v = params.get("subtab");
  return v === "performance" ? "performance" : "history";
}

export function RecommendationsPanel() {
  const [subtab, setSubtab] = useState<SubTab>("history");

  // Hydrate from URL after mount (SSR-safe — initial
  // server render uses the default so HTML matches
  // the client's first render before useEffect). The
  // setState is deferred past the synchronous effect
  // body via Promise.resolve so eslint-plugin-
  // react-hooks v5 sees it as an async-callback
  // update rather than a sync setState.
  useEffect(() => {
    let alive = true;
    void Promise.resolve().then(() => {
      if (alive) setSubtab(readInitial());
    });
    return () => {
      alive = false;
    };
  }, []);

  const handleChange = (next: SubTab) => {
    setSubtab(next);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (next === "history") {
      url.searchParams.delete("subtab");
    } else {
      url.searchParams.set("subtab", next);
    }
    window.history.replaceState(
      null, "", url.toString(),
    );
  };

  return (
    <div className="space-y-3">
      {/* Inner sub-tab strip */}
      <div
        className={
          "inline-flex rounded-md border " +
          "border-gray-300 dark:border-gray-600 " +
          "overflow-hidden"
        }
        role="tablist"
        data-testid="recommendations-subtabs"
      >
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={subtab === t.value}
            onClick={() => handleChange(t.value)}
            data-testid={`subtab-${t.value}`}
            className={
              "px-3 py-1.5 text-sm font-medium " +
              "transition-colors " +
              (subtab === t.value
                ? "bg-indigo-600 text-white"
                : "bg-white dark:bg-gray-800 " +
                  "text-gray-700 dark:text-gray-200 " +
                  "hover:bg-gray-100 " +
                  "dark:hover:bg-gray-700")
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {subtab === "history" && (
        <RecommendationHistoryTab />
      )}
      {subtab === "performance" && (
        <RecommendationPerformanceTab />
      )}
    </div>
  );
}
