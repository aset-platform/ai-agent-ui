"use client";
/**
 * Compact pill showing a signal name + value with color coding.
 * Used inside RecommendationCard to display data_signals.
 */

interface SignalPillProps {
  label: string;
  value: string | number;
  quality?: "good" | "neutral" | "bad";
}

const qualityClasses: Record<string, string> = {
  good:
    "bg-emerald-100 text-emerald-700 " +
    "dark:bg-emerald-900/30 dark:text-emerald-400",
  neutral:
    "bg-gray-100 text-gray-600 " +
    "dark:bg-gray-800 dark:text-gray-400",
  bad:
    "bg-red-100 text-red-700 " +
    "dark:bg-red-900/30 dark:text-red-400",
};

export function SignalPill({
  label,
  value,
  quality = "neutral",
}: SignalPillProps) {
  const cls = qualityClasses[quality] ?? qualityClasses.neutral;
  const display =
    typeof value === "number"
      ? Number.isInteger(value)
        ? String(value)
        : value.toFixed(2)
      : value;

  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded-full " +
        "px-2 py-0.5 text-xs font-medium whitespace-nowrap " +
        cls
      }
    >
      <span className="text-[10px] opacity-70">{label}</span>
      <span>{display}</span>
    </span>
  );
}
