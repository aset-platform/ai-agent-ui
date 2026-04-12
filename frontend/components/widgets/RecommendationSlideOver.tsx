"use client";
/**
 * Slide-over panel for full recommendation list.
 *
 * Opens from the right with backdrop. Contains health
 * score, tier/severity filters, and scrollable list
 * of RecommendationCard components.
 */

import { useState, useEffect, useMemo, useCallback } from "react";
import { createPortal } from "react-dom";
import { HealthScoreBadge } from "./HealthScoreBadge";
import { RecommendationCard } from "./RecommendationCard";
import type {
  RecommendationResponse,
  RecommendationItem,
} from "@/lib/types";

/* ── Filter types ──────────────────────────────────── */

type TierFilter =
  | "all"
  | "portfolio"
  | "watchlist"
  | "discovery";
type SeverityFilter =
  | "all"
  | "high"
  | "medium"
  | "low";

const TIER_OPTIONS: { value: TierFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "portfolio", label: "Portfolio" },
  { value: "watchlist", label: "Watchlist" },
  { value: "discovery", label: "Discovery" },
];

const SEVERITY_OPTIONS: {
  value: SeverityFilter;
  label: string;
}[] = [
  { value: "all", label: "All" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

/* ── Pill button ───────────────────────────────────── */

function FilterPill<T extends string>({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: T;
  active: boolean;
  onClick: (v: T) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className={
        "rounded-full px-3 py-1 text-xs font-medium " +
        "transition-colors " +
        (active
          ? "bg-gray-900 text-white dark:bg-gray-100 " +
            "dark:text-gray-900"
          : "bg-gray-100 text-gray-600 " +
            "hover:bg-gray-200 " +
            "dark:bg-gray-800 dark:text-gray-400 " +
            "dark:hover:bg-gray-700")
      }
    >
      {label}
    </button>
  );
}

/* ── Props ─────────────────────────────────────────── */

interface SlideOverProps {
  open: boolean;
  onClose: () => void;
  data: RecommendationResponse | null;
}

/* ── Component ─────────────────────────────────────── */

export function RecommendationSlideOver({
  open,
  onClose,
  data,
}: SlideOverProps) {
  const [tier, setTier] = useState<TierFilter>("all");
  const [severity, setSeverity] =
    useState<SeverityFilter>("all");

  // Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () =>
      document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Lock body scroll when open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  // Reset filters on open
  useEffect(() => {
    if (open) {
      setTier("all");
      setSeverity("all");
    }
  }, [open]);

  const filtered = useMemo(() => {
    const recs = data?.recommendations ?? [];
    return recs.filter((r) => {
      if (tier !== "all" && r.tier !== tier) return false;
      if (severity !== "all" && r.severity !== severity)
        return false;
      return true;
    });
  }, [data, tier, severity]);

  if (!open) return null;

  const panel = (
    <div className="fixed inset-0 z-[60]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 transition-opacity"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        className={
          "absolute right-0 top-0 h-full w-full " +
          "max-w-[480px] bg-white dark:bg-gray-900 " +
          "shadow-2xl flex flex-col " +
          "transform transition-transform duration-300 " +
          "translate-x-0"
        }
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-800">
          <div>
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Portfolio Recommendations
            </h2>
            {data && (
              <div className="mt-2">
                <HealthScoreBadge
                  score={data.health_score}
                  label={data.health_label}
                />
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className={
              "rounded-lg p-2 text-gray-400 " +
              "hover:text-gray-600 hover:bg-gray-100 " +
              "dark:hover:text-gray-300 " +
              "dark:hover:bg-gray-800 transition-colors"
            }
          >
            <svg
              className="w-5 h-5"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
            </svg>
          </button>
        </div>

        {/* Filters */}
        <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-800 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {TIER_OPTIONS.map((o) => (
              <FilterPill
                key={o.value}
                label={o.label}
                value={o.value}
                active={tier === o.value}
                onClick={setTier}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {SEVERITY_OPTIONS.map((o) => (
              <FilterPill
                key={o.value}
                label={o.label}
                value={o.value}
                active={severity === o.value}
                onClick={setSeverity}
              />
            ))}
          </div>
        </div>

        {/* Health assessment */}
        {data?.health_assessment && (
          <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-800">
            <p className="text-xs text-gray-500 dark:text-gray-400 italic leading-relaxed">
              {data.health_assessment}
            </p>
          </div>
        )}

        {/* Cards */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {filtered.length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-sm text-gray-500 dark:text-gray-400">
                No recommendations match the filters.
              </p>
            </div>
          ) : (
            filtered.map((r) => (
              <RecommendationCard key={r.id} rec={r} />
            ))
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(panel, document.body);
}
