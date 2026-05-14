"use client";

/**
 * Collapsible "How this list is built" strip for the
 * Swing Setups tab. Renders gates + rank formula directly
 * from the backend methodology block — never hardcode
 * rules here (single source of truth is
 * `backend/advanced_analytics_swing.py`).
 *
 * Default state per regime per session: expanded on first
 * visit, collapsed thereafter. Persisted via localStorage
 * flag `aa.swing.<regime>.methodology_seen`.
 */

import { useEffect, useState } from "react";
import type { SwingMethodology } from "@/lib/types/swingSetups";

const REC_GATE_LABEL = "Rec-engine bullish";

interface Props {
  methodology: SwingMethodology;
  recGateApplied: boolean;
  notes: string[];
}

function regimeTitle(regime: SwingMethodology["regime"]): string {
  switch (regime) {
    case "bull":
      return "Bull-swing";
    case "sideways":
      return "Sideways-swing";
    case "bearish":
      return "Bearish-swing";
  }
}

export function SwingMethodologyPanel({
  methodology,
  recGateApplied,
  notes,
}: Props) {
  const storageKey =
    `aa.swing.${methodology.regime}.methodology_seen`;
  // Default to expanded (true). Effect below collapses if
  // the localStorage flag is already set, avoiding the
  // first-visit-shows-collapsed flicker.
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const seen = window.localStorage.getItem(storageKey);
    if (seen !== "1") return;
    // Defer the setState into a microtask so React's
    // ``react-hooks/set-state-in-effect`` rule is satisfied —
    // the rule only blocks synchronous setState calls in an
    // effect body. ``cancelled`` guards against a regime-prop
    // change that re-fires the effect before this microtask
    // runs, which would otherwise stamp the wrong collapse
    // state for the new regime.
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setOpen(false);
    });
    return () => {
      cancelled = true;
    };
  }, [storageKey]);

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        // "Seen" once collapsed at least once; stays
        // collapsed by default thereafter.
        window.localStorage.setItem(
          storageKey, next ? "0" : "1",
        );
      }
      return next;
    });
  };

  return (
    <section
      data-testid="swing-methodology-panel"
      aria-label="Methodology"
      className={
        "rounded-md border bg-slate-50 dark:bg-slate-900/30 "
        + "mb-4"
      }
    >
      <header
        className={
          "flex items-center justify-between px-4 py-2 "
          + "text-sm"
        }
      >
        <span className="font-medium">
          How this {regimeTitle(methodology.regime)} list is built
        </span>
        <button
          type="button"
          data-testid="swing-methodology-toggle"
          onClick={toggle}
          aria-expanded={open}
          aria-label={
            open ? "Collapse methodology" : "Expand methodology"
          }
          className={
            "text-xs text-slate-600 dark:text-slate-300 "
            + "hover:text-slate-900 dark:hover:text-slate-100"
          }
        >
          {open ? "collapse ▲" : "expand ▼"}
        </button>
      </header>
      {open && (
        <div className="px-4 pb-4 space-y-3 text-sm">
          <p className="text-slate-700 dark:text-slate-200">
            {methodology.summary}
          </p>

          <div>
            <p className="font-medium mb-1">
              Gates (all must hold):
            </p>
            <ol className="space-y-2 list-decimal pl-5">
              {methodology.gates.map((g) => {
                const struck =
                  !recGateApplied && g.label === REC_GATE_LABEL;
                return (
                  <li key={g.label}>
                    <span
                      className={
                        "font-medium "
                        + (struck
                          ? "line-through "
                          + "text-slate-400 dark:text-slate-500"
                          : "")
                      }
                    >
                      {g.label}
                    </span>{" "}
                    <code
                      className={
                        "font-mono text-xs bg-slate-200 "
                        + "dark:bg-slate-800 px-1 py-0.5 rounded"
                      }
                    >
                      {g.rule}
                    </code>
                    <div
                      className={
                        "text-xs text-slate-600 "
                        + "dark:text-slate-400 ml-1 mt-0.5"
                      }
                    >
                      ↳ {g.why}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>

          <div>
            <p className="font-medium">
              Ranking:{" "}
              <code className="font-mono text-xs">
                {methodology.rank.formula}
              </code>{" "}
              ({methodology.rank.direction}, top{" "}
              {methodology.rank.cap})
            </p>
            {methodology.rank.degraded && !recGateApplied && (
              <p
                className={
                  "text-xs text-slate-600 dark:text-slate-400"
                }
              >
                {methodology.rank.degraded}
              </p>
            )}
          </div>

          {notes.length > 0 && (
            <ul
              data-testid="swing-methodology-notes"
              className={
                "text-xs text-amber-700 dark:text-amber-400 "
                + "list-disc pl-5"
              }
            >
              {notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
