"use client";
/**
 * One-click action button on a recommendation row.
 *
 * - Buy / accumulate  → "+" icon, routes to
 *   /dashboard?add={ticker} (opens the Add Holding
 *   modal pre-filled with the ticker).
 * - Sell / reduce / trim → pencil icon, routes to
 *   /dashboard?edit={ticker} (opens the Edit modal
 *   for the existing holding).
 * - Other actions (hold, watch, research, ...) render
 *   nothing so we don't clutter low-intent rows.
 *
 * When ``actedOn`` is true (the user has already
 * executed the rec), the button becomes a disabled
 * check pill so the call-to-action doesn't keep
 * screaming at them.
 *
 * ``onBeforeNavigate`` is invoked right before we
 * route — parents use it to close a surrounding
 * slide-over/modal so the Add/Edit modal doesn't
 * stack behind it on the dashboard.
 */

import { usePortfolioActions } from "@/providers/PortfolioActionsProvider";

const BUY_ACTIONS = new Set([
  "buy",
  "accumulate",
]);
const SELL_ACTIONS = new Set([
  "sell",
  "reduce",
  "trim",
]);

function PlusIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M10 3a.75.75 0 0 1 .75.75v5.5h5.5a.75.75 0 0 1 0 1.5h-5.5v5.5a.75.75 0 0 1-1.5 0v-5.5h-5.5a.75.75 0 0 1 0-1.5h5.5v-5.5A.75.75 0 0 1 10 3Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path d="M2.695 14.763 2 17.25l2.487-.695 10.28-10.28-1.79-1.792L2.695 14.763Zm12.035-11.238 1.544 1.544a1.5 1.5 0 0 1 0 2.121l-1.06 1.06-3.665-3.665 1.06-1.06a1.5 1.5 0 0 1 2.121 0Z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M16.704 5.29a1 1 0 0 1 .006 1.414l-7.07 7.14a1 1 0 0 1-1.425.005l-3.92-3.935a1 1 0 0 1 1.418-1.41l3.207 3.22 6.37-6.432a1 1 0 0 1 1.414-.002Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

interface Props {
  ticker: string;
  action: string;
  actedOn?: boolean;
  onBeforeNavigate?: () => void;
}

export function RecActionButton({
  ticker,
  action,
  actedOn = false,
  onBeforeNavigate,
}: Props) {
  const { openAdd, openEdit } = usePortfolioActions();
  const act = action.toLowerCase();
  const isBuy = BUY_ACTIONS.has(act);
  const isSell = SELL_ACTIONS.has(act);
  if (!isBuy && !isSell) return null;

  if (actedOn) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-400"
        title="You've already acted on this recommendation."
      >
        <CheckIcon />
        Acted
      </span>
    );
  }

  const label = isBuy ? "Add to portfolio" : "Edit holding";
  const cls = isBuy
    ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-400 dark:hover:bg-emerald-900/40"
    : "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-400 dark:hover:bg-amber-900/40";

  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onBeforeNavigate?.();
        if (isBuy) openAdd(ticker);
        else openEdit(ticker);
      }}
      className={
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold transition-colors " +
        cls
      }
    >
      {isBuy ? <PlusIcon /> : <PencilIcon />}
      {isBuy ? "Buy" : "Edit"}
    </button>
  );
}
