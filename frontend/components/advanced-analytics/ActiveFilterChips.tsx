"use client";
/**
 * Read-only-but-removable chip strip rendered directly under
 * the AA toolbar. Each chip carries the catalog label and an
 * × button that drops the key from its bundle. "Clear all"
 * resets both bundles.
 */

import { FILTER_LABEL_BY_KEY } from "./filterCatalogs";

interface Props {
  tech: string[];
  fund: string[];
  onRemoveTech: (key: string) => void;
  onRemoveFund: (key: string) => void;
  onClearAll: () => void;
}

export function ActiveFilterChips({
  tech,
  fund,
  onRemoveTech,
  onRemoveFund,
  onClearAll,
}: Props) {
  if (tech.length === 0 && fund.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-gray-500 dark:text-gray-400">Active:</span>
      {tech.map((key) => (
        <Chip
          key={`tech-${key}`}
          testId={`aa-active-filter-chip-${key}`}
          xTestId={`aa-active-filter-chip-${key}-x`}
          label={FILTER_LABEL_BY_KEY[key] ?? key}
          tone="indigo"
          onRemove={() => onRemoveTech(key)}
        />
      ))}
      {fund.map((key) => (
        <Chip
          key={`fund-${key}`}
          testId={`aa-active-filter-chip-${key}`}
          xTestId={`aa-active-filter-chip-${key}-x`}
          label={FILTER_LABEL_BY_KEY[key] ?? key}
          tone="emerald"
          onRemove={() => onRemoveFund(key)}
        />
      ))}
      <button
        type="button"
        onClick={onClearAll}
        data-testid="aa-active-filter-clear-all"
        className="ml-1 text-indigo-600 dark:text-indigo-400 hover:underline"
      >
        Clear all
      </button>
    </div>
  );
}

interface ChipProps {
  label: string;
  testId: string;
  xTestId: string;
  tone: "indigo" | "emerald";
  onRemove: () => void;
}

function Chip({ label, testId, xTestId, tone, onRemove }: ChipProps) {
  const tones: Record<ChipProps["tone"], string> = {
    indigo:
      "bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/20" +
      " dark:text-indigo-300 dark:border-indigo-900/50",
    emerald:
      "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20" +
      " dark:text-emerald-300 dark:border-emerald-900/50",
  };
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${tones[tone]}`}
    >
      {label}
      <button
        type="button"
        aria-label={`Remove ${label}`}
        onClick={onRemove}
        data-testid={xTestId}
        className="hover:text-red-600 dark:hover:text-red-400 transition-colors"
      >
        ×
      </button>
    </span>
  );
}
