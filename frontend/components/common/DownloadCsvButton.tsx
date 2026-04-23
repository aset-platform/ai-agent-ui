"use client";

import type { ButtonHTMLAttributes } from "react";

function DownloadIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path d="M10.75 2.75a.75.75 0 0 0-1.5 0v8.614L6.295 8.235a.75.75 0 1 0-1.09 1.03l4.25 4.5a.75.75 0 0 0 1.09 0l4.25-4.5a.75.75 0 0 0-1.09-1.03l-2.955 3.129V2.75Z" />
      <path d="M3.5 12.75a.75.75 0 0 0-1.5 0v2.5A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25v-2.5a.75.75 0 0 0-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5Z" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      className="h-3.5 w-3.5 animate-spin"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="4"
      />
      <path
        d="M4 12a8 8 0 018-8"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}

interface DownloadCsvButtonProps
  extends Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    "children"
  > {
  loading?: boolean;
}

/**
 * Matches the CSV button style in InsightsTable (Screener
 * page). Use next to pagination for consistency across
 * tabs.
 */
export function DownloadCsvButton({
  loading = false,
  disabled,
  className,
  ...rest
}: DownloadCsvButtonProps) {
  return (
    <button
      type="button"
      data-testid="download-csv"
      disabled={disabled || loading}
      className={
        "inline-flex items-center gap-1 rounded-md " +
        "border border-gray-300 dark:border-gray-600 " +
        "bg-white dark:bg-gray-800 px-2 py-0.5 text-xs " +
        "font-medium text-gray-600 dark:text-gray-300 " +
        "hover:bg-gray-100 dark:hover:bg-gray-700 " +
        "disabled:opacity-40 disabled:cursor-not-allowed " +
        "transition-colors " +
        (className ?? "")
      }
      {...rest}
    >
      {loading ? <SpinnerIcon /> : <DownloadIcon />}
      CSV
    </button>
  );
}
