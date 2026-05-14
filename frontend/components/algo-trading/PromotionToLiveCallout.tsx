"use client";
/**
 * Callout documenting what it takes to graduate a paper-stage
 * strategy to Live trading. Shown on Paper and Dry-run pages so
 * users see the criteria *while* they're testing — not buried in
 * the promote modal after they click.
 *
 * Hard gates (enforced by the backend; see
 * backend/algo/strategy/promotion.py::check_eligibility):
 *   • At least one completed paper run for this strategy,
 *     started AFTER the latest AST edit (staleness guard).
 *
 * Soft recommendations (not enforced; surfaced here to nudge
 * good practice):
 *   • Dry-run the strategy under live-runtime plumbing first to
 *     catch order-placement / gating issues.
 *   • Review fills, fees, drawdown, and trade reasons on the
 *     paper run before promoting.
 *   • Configure capital + position caps under Live → Settings
 *     before enabling live orders.
 */

import { useState } from "react";

interface Props {
  /** Which page is rendering the callout — used to tailor copy
   *  ("on this page" vs "on the Paper tab"). */
  surface: "paper" | "dryrun";
}

export function PromotionToLiveCallout({ surface }: Props) {
  const [open, setOpen] = useState(true);
  return (
    <div
      className="rounded-md border border-indigo-200 dark:border-indigo-800 bg-indigo-50/60 dark:bg-indigo-950/30 p-3 text-xs text-indigo-900 dark:text-indigo-100"
      data-testid={`promotion-callout-${surface}`}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 font-semibold text-indigo-900 dark:text-indigo-100"
        aria-expanded={open}
      >
        <span>
          What it takes to graduate this strategy to Live
        </span>
        <span aria-hidden className="text-[10px]">
          {open ? "▾ hide" : "▸ show"}
        </span>
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div>
            <div className="font-medium text-indigo-800 dark:text-indigo-200">
              Required gate (enforced)
            </div>
            <ul className="list-disc pl-4 mt-1 space-y-0.5">
              <li>
                At least one <strong>completed paper run</strong>{" "}
                for this strategy, started after the latest AST
                edit. (Editing the strategy invalidates prior
                paper runs.)
              </li>
              <li>
                The strategy is currently in <strong>paper</strong>{" "}
                mode (promote it from draft → paper first via the
                Strategies tab).
              </li>
            </ul>
          </div>
          <div>
            <div className="font-medium text-indigo-800 dark:text-indigo-200">
              Recommended before you flip the switch
            </div>
            <ul className="list-disc pl-4 mt-1 space-y-0.5">
              {surface === "paper" ? (
                <li>
                  Rehearse on the <strong>Dry-run</strong> tab —
                  same runtime plumbing as Live, but with
                  synthetic broker responses. Catches gating /
                  order-placement bugs without risking capital.
                </li>
              ) : (
                <li>
                  Confirm the strategy has a clean{" "}
                  <strong>Paper run</strong> before this dry-run
                  rehearsal — Dry-run validates plumbing, Paper
                  validates economics.
                </li>
              )}
              <li>
                Review the trade table: fees, win-rate, max
                drawdown, exit reasons. Reject if fees dominate
                returns or if most exits are MIS-square-off.
              </li>
              <li>
                Configure capital + per-trade / portfolio caps
                under <strong>Live → Settings</strong> for this
                strategy. Live trading is blocked until caps are
                set.
              </li>
              <li>
                Once promoted, the kill-switch on the Live page
                stops new orders and (for MIS) closes open
                positions. Know how to reach it before you go
                live.
              </li>
            </ul>
          </div>
          <div className="text-indigo-700 dark:text-indigo-300">
            Promote from the <strong>Strategies</strong> tab →
            Promote action → choose Live. Gate failures explain
            what's still missing.
          </div>
        </div>
      )}
    </div>
  );
}
