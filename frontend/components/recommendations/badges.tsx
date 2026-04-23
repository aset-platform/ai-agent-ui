"use client";
/**
 * Shared recommendation badges.
 *
 * Used by the user-facing Recommendation History
 * (insights tab) and the admin Recommendations tab
 * so visual language stays consistent.
 */

export function ScopeBadge({ scope }: { scope: string }) {
  if (scope === "india")
    return (
      <span
        className="px-1.5 py-0.5 rounded text-[10px]
          font-semibold bg-orange-100 text-orange-700
          dark:bg-orange-900/30 dark:text-orange-400"
      >
        India
      </span>
    );
  if (scope === "us")
    return (
      <span
        className="px-1.5 py-0.5 rounded text-[10px]
          font-semibold bg-blue-100 text-blue-700
          dark:bg-blue-900/30 dark:text-blue-400"
      >
        US
      </span>
    );
  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px]
        font-semibold bg-gray-100 text-gray-600
        dark:bg-gray-800 dark:text-gray-400"
    >
      All
    </span>
  );
}

export function RunTypeBadge({
  runType,
}: {
  runType: string;
}) {
  const styles: Record<string, string> = {
    scheduled:
      "bg-indigo-100 text-indigo-700 " +
      "dark:bg-indigo-900/30 dark:text-indigo-400",
    manual:
      "bg-teal-100 text-teal-700 " +
      "dark:bg-teal-900/30 dark:text-teal-400",
    chat:
      "bg-violet-100 text-violet-700 " +
      "dark:bg-violet-900/30 dark:text-violet-400",
    cli:
      "bg-gray-200 text-gray-700 " +
      "dark:bg-gray-700 dark:text-gray-300",
    admin:
      "bg-fuchsia-100 text-fuchsia-700 " +
      "dark:bg-fuchsia-900/30 dark:text-fuchsia-400",
    admin_test:
      "bg-amber-100 text-amber-800 " +
      "dark:bg-amber-900/30 dark:text-amber-400 " +
      "ring-1 ring-amber-300 dark:ring-amber-700",
  };
  const labels: Record<string, string> = {
    scheduled: "Scheduled",
    manual: "Manual",
    chat: "Chat",
    cli: "CLI",
    admin: "Admin",
    admin_test: "Test",
  };
  return (
    <span
      className={
        "px-1.5 py-0.5 rounded text-[10px] " +
        "font-semibold " +
        (styles[runType] ??
          "bg-gray-100 text-gray-600 " +
            "dark:bg-gray-800 dark:text-gray-400")
      }
    >
      {labels[runType] ?? runType}
    </span>
  );
}

export function TierBadge({ tier }: { tier: string }) {
  const styles: Record<string, string> = {
    portfolio:
      "bg-blue-100 text-blue-700 " +
      "dark:bg-blue-900/30 dark:text-blue-400",
    watchlist:
      "bg-emerald-100 text-emerald-700 " +
      "dark:bg-emerald-900/30 dark:text-emerald-400",
    discovery:
      "bg-purple-100 text-purple-700 " +
      "dark:bg-purple-900/30 dark:text-purple-400",
  };
  return (
    <span
      className={
        "px-1.5 py-0.5 rounded-full text-[10px] " +
        "font-semibold uppercase " +
        (styles[tier] ?? styles.discovery)
      }
    >
      {tier}
    </span>
  );
}

export function SeverityPill({
  severity,
}: {
  severity: string;
}) {
  const styles: Record<string, string> = {
    high:
      "bg-red-100 text-red-700 " +
      "dark:bg-red-900/30 dark:text-red-400",
    medium:
      "bg-amber-100 text-amber-700 " +
      "dark:bg-amber-900/30 dark:text-amber-400",
    low:
      "bg-blue-100 text-blue-700 " +
      "dark:bg-blue-900/30 dark:text-blue-400",
  };
  return (
    <span
      className={
        "px-1.5 py-0.5 rounded text-[10px] " +
        "font-semibold uppercase " +
        (styles[severity] ?? styles.low)
      }
    >
      {severity}
    </span>
  );
}

export function CategoryPill({
  category,
}: {
  category: string;
}) {
  const styles: Record<string, string> = {
    value: "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400",
    growth: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    dividend: "bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400",
    momentum: "bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-400",
    quality: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400",
    sector: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400",
    rebalance: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
    exit_reduce: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
    risk_alert: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
    hold_accumulate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  };
  const fallback =
    "bg-gray-100 text-gray-600 " +
    "dark:bg-gray-800 dark:text-gray-400";
  return (
    <span
      className={
        "px-1.5 py-0.5 rounded text-[10px] " +
        "font-medium " +
        (styles[category] ?? fallback)
      }
    >
      {category.replace(/_/g, " ")}
    </span>
  );
}

export function healthBadgeClass(score: number): string {
  if (score >= 80)
    return (
      "bg-emerald-100 text-emerald-700 " +
      "dark:bg-emerald-900/30 dark:text-emerald-400"
    );
  if (score >= 60)
    return (
      "bg-yellow-100 text-yellow-700 " +
      "dark:bg-yellow-900/30 dark:text-yellow-400"
    );
  return (
    "bg-red-100 text-red-700 " +
    "dark:bg-red-900/30 dark:text-red-400"
  );
}
