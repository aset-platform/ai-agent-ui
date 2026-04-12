"use client";
/**
 * Circular health score indicator with color coding.
 * Red (<30), amber (<60), green (<80), blue (>=80).
 */

interface HealthScoreBadgeProps {
  score: number; // 0-100
  label: string; // critical / needs_attention / healthy / excellent
}

function scoreColor(score: number) {
  if (score < 30)
    return {
      ring: "text-red-500 dark:text-red-400",
      bg: "bg-red-50 dark:bg-red-900/20",
      text: "text-red-700 dark:text-red-300",
    };
  if (score < 60)
    return {
      ring: "text-amber-500 dark:text-amber-400",
      bg: "bg-amber-50 dark:bg-amber-900/20",
      text: "text-amber-700 dark:text-amber-300",
    };
  if (score < 80)
    return {
      ring: "text-emerald-500 dark:text-emerald-400",
      bg: "bg-emerald-50 dark:bg-emerald-900/20",
      text: "text-emerald-700 dark:text-emerald-300",
    };
  return {
    ring: "text-blue-500 dark:text-blue-400",
    bg: "bg-blue-50 dark:bg-blue-900/20",
    text: "text-blue-700 dark:text-blue-300",
  };
}

function labelDisplay(label: string): string {
  return label
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function HealthScoreBadge({
  score,
  label,
}: HealthScoreBadgeProps) {
  const colors = scoreColor(score);
  // SVG circle params: r=36, circumference ~226
  const circumference = 2 * Math.PI * 36;
  const pct = Math.min(Math.max(score, 0), 100);
  const offset = circumference * (1 - pct / 100);

  return (
    <div className="flex items-center gap-3">
      <div className="relative w-14 h-14">
        <svg
          viewBox="0 0 80 80"
          className="w-full h-full -rotate-90"
        >
          {/* Background ring */}
          <circle
            cx="40"
            cy="40"
            r="36"
            fill="none"
            strokeWidth="6"
            className="stroke-gray-200 dark:stroke-gray-700"
          />
          {/* Score arc */}
          <circle
            cx="40"
            cy="40"
            r="36"
            fill="none"
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className={`stroke-current ${colors.ring}`}
          />
        </svg>
        {/* Score number */}
        <span
          className={
            "absolute inset-0 flex items-center " +
            "justify-center text-sm font-bold " +
            colors.text
          }
        >
          {Math.round(score)}
        </span>
      </div>
      <div className="flex flex-col">
        <span
          className={
            "text-xs font-semibold " + colors.text
          }
        >
          {labelDisplay(label)}
        </span>
        <span className="text-[10px] text-gray-500 dark:text-gray-400">
          Health Score
        </span>
      </div>
    </div>
  );
}
