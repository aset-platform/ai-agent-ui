"use client";
/**
 * Strategy Levers — non-technical edit surface for the tunable
 * parameters of a strategy AST. The visual builder + JSON pane
 * stay read-only; this panel exposes the numeric / categorical
 * "knobs" most users actually want to tweak between runs:
 *
 *   • Universe scope, market, ticker_type
 *   • Rebalance.max_positions
 *   • Risk → per_trade (stop_loss_pct, max_qty)
 *   • Risk → portfolio (max_exposure_pct, max_concentration_pct)
 *   • Risk → daily (max_loss_pct, max_open_positions)
 *
 * Reads + writes the same StrategyAst object the builder uses,
 * so toggling a lever immediately reflects in the JSON pane.
 * Saving still goes through the existing Update button on
 * StrategyBuilder.
 */

import { useMemo, useState } from "react";

import type { StrategyAst } from "@/hooks/useStrategies";

import {
  setByPath,
  walkTunables,
  type Tunable,
} from "./strategyTunables";

const SCOPES = ["discovery", "watchlist", "portfolio"] as const;
const MARKETS = ["india", "us", "all"] as const;
const TICKER_TYPES = ["stock", "etf"] as const;

interface Props {
  ast: StrategyAst;
  onChange: (next: StrategyAst) => void;
}

// Narrowing helpers — the AST blocks come from the backend's
// Pydantic schema but TS sees them as `unknown` in StrategyAst.
type UniverseFilter = {
  ticker_type: string[];
  market: string;
};
type Universe = {
  type: "scope";
  scope: string;
  filter: UniverseFilter;
};
type Rebalance = { type: "daily"; max_positions: number };
type RiskPerTrade = { stop_loss_pct: number; max_qty: number };
type RiskPortfolio = {
  max_exposure_pct: number;
  max_concentration_pct: number;
};
type RiskDaily = {
  max_loss_pct: number;
  max_open_positions: number;
};
type Risk = {
  per_trade: RiskPerTrade;
  portfolio: RiskPortfolio;
  daily: RiskDaily;
};

function asUniverse(u: unknown): Universe {
  return (u as Universe) ?? {
    type: "scope",
    scope: "watchlist",
    filter: { ticker_type: ["stock"], market: "india" },
  };
}
function asRebalance(r: unknown): Rebalance {
  return (r as Rebalance) ?? { type: "daily", max_positions: 1 };
}
function asRisk(r: unknown): Risk {
  return (r as Risk) ?? {
    per_trade: { stop_loss_pct: 5, max_qty: 100 },
    portfolio: {
      max_exposure_pct: 80,
      max_concentration_pct: 25,
    },
    daily: { max_loss_pct: 2, max_open_positions: 10 },
  };
}

export function StrategyLeversPanel({ ast, onChange }: Props) {
  const [open, setOpen] = useState(true);
  const universe = asUniverse(ast.universe);
  const rebalance = asRebalance(ast.rebalance);
  const risk = asRisk(ast.risk);
  const tunables: Tunable[] = useMemo(
    () =>
      walkTunables((ast.root ?? {}) as Record<string, unknown>),
    [ast.root],
  );

  function patch(partial: Partial<StrategyAst>) {
    onChange({ ...ast, ...partial });
  }

  function patchTunable(path: string, value: number) {
    const nextRoot = setByPath(
      (ast.root ?? {}) as Record<string, unknown>,
      path,
      value,
    );
    patch({ root: nextRoot } as Partial<StrategyAst>);
  }

  function patchUniverse(u: Partial<Universe>) {
    patch({ universe: { ...universe, ...u } });
  }
  function patchFilter(f: Partial<UniverseFilter>) {
    patch({
      universe: {
        ...universe,
        filter: { ...universe.filter, ...f },
      },
    });
  }
  function patchRebalance(r: Partial<Rebalance>) {
    patch({ rebalance: { ...rebalance, ...r } });
  }
  function patchRisk(group: keyof Risk, body: object) {
    patch({
      risk: { ...risk, [group]: { ...risk[group], ...body } },
    });
  }

  return (
    <section
      className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900"
      data-testid="strategy-levers-panel"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800"
        aria-expanded={open}
        data-testid="strategy-levers-toggle"
      >
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Strategy Levers
        </span>
        <span className="text-xs text-slate-500">
          {open ? "Hide ▲" : "Show ▼"}
        </span>
      </button>

      {open && (
        <div
          className="space-y-4 border-t border-slate-200 dark:border-slate-700 px-3 py-3"
          data-testid="strategy-levers-body"
        >
          {tunables.length > 0 && (
            <Group title="Strategy logic">
              {tunables.map((t) => (
                <NumberField
                  key={t.path}
                  label={`${t.label}${
                    t.kind === "weight" ? ` (now ${t.value})` : ""
                  }`}
                  value={t.value}
                  min={t.min}
                  max={t.max}
                  step={t.step ?? 1}
                  onChange={(v) => patchTunable(t.path, v)}
                  testId={`lever-tunable-${t.path.replace(
                    /[.[\]]/g,
                    "_",
                  )}`}
                />
              ))}
            </Group>
          )}

          <Group title="Universe">
            <Field label="Scope">
              <select
                className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
                value={universe.scope}
                onChange={(e) =>
                  patchUniverse({ scope: e.target.value })
                }
                data-testid="lever-universe-scope"
              >
                {SCOPES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </Field>
            <Field label="Market">
              <select
                className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
                value={universe.filter.market}
                onChange={(e) =>
                  patchFilter({ market: e.target.value })
                }
                data-testid="lever-universe-market"
              >
                {MARKETS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Ticker types">
              <div className="flex gap-3 text-sm">
                {TICKER_TYPES.map((t) => {
                  const checked = universe.filter.ticker_type.includes(t);
                  return (
                    <label
                      key={t}
                      className="flex items-center gap-1 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const cur = new Set(
                            universe.filter.ticker_type,
                          );
                          if (checked) {
                            cur.delete(t);
                          } else {
                            cur.add(t);
                          }
                          // Schema requires min_length=1.
                          if (cur.size === 0) return;
                          patchFilter({
                            ticker_type: Array.from(cur),
                          });
                        }}
                        data-testid={`lever-ticker-type-${t}`}
                      />
                      {t}
                    </label>
                  );
                })}
              </div>
            </Field>
          </Group>

          <Group title="Rebalance">
            <NumberField
              label="Max positions"
              value={rebalance.max_positions}
              onChange={(v) =>
                patchRebalance({ max_positions: v })
              }
              min={1}
              max={50}
              testId="lever-rebalance-max-positions"
              hint="Cap on how many tickers the strategy can hold concurrently."
            />
          </Group>

          <Group title="Risk · per-trade">
            <NumberField
              label="Stop-loss %"
              value={risk.per_trade.stop_loss_pct}
              onChange={(v) =>
                patchRisk("per_trade", { stop_loss_pct: v })
              }
              min={0.1}
              max={50}
              step={0.5}
              testId="lever-risk-stop-loss-pct"
            />
            <NumberField
              label="Max qty / trade"
              value={risk.per_trade.max_qty}
              onChange={(v) =>
                patchRisk("per_trade", { max_qty: v })
              }
              min={1}
              max={100000}
              testId="lever-risk-max-qty"
              hint="Per-fill share cap. Hits as MAX_QTY rejection."
            />
          </Group>

          <Group title="Risk · portfolio">
            <NumberField
              label="Max exposure %"
              value={risk.portfolio.max_exposure_pct}
              onChange={(v) =>
                patchRisk("portfolio", { max_exposure_pct: v })
              }
              min={0}
              max={100}
              step={5}
              testId="lever-risk-max-exposure-pct"
              hint="Total notional across all open positions."
            />
            <NumberField
              label="Max concentration %"
              value={risk.portfolio.max_concentration_pct}
              onChange={(v) =>
                patchRisk(
                  "portfolio",
                  { max_concentration_pct: v },
                )
              }
              min={0}
              max={100}
              step={5}
              testId="lever-risk-max-concentration-pct"
              hint="Per-ticker cap (% of equity)."
            />
          </Group>

          <Group title="Risk · daily">
            <NumberField
              label="Max loss %"
              value={risk.daily.max_loss_pct}
              onChange={(v) =>
                patchRisk("daily", { max_loss_pct: v })
              }
              min={0}
              max={50}
              step={0.5}
              testId="lever-risk-max-loss-pct"
              hint="Halts new BUYs once intraday loss exceeds this %."
            />
            <NumberField
              label="Max open positions"
              value={risk.daily.max_open_positions}
              onChange={(v) =>
                patchRisk("daily", { max_open_positions: v })
              }
              min={1}
              max={50}
              testId="lever-risk-max-open-positions"
            />
          </Group>
        </div>
      )}
    </section>
  );
}

function Group({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {title}
      </h4>
      <div className="mt-1.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {children}
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-xs text-slate-600 dark:text-slate-400">
        {label}
      </span>
      {children}
      {hint && (
        <span className="text-[11px] text-slate-500">{hint}</span>
      )}
    </label>
  );
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  hint,
  testId,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  testId?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isNaN(v)) return;
          if (min !== undefined && v < min) return;
          if (max !== undefined && v > max) return;
          onChange(v);
        }}
        data-testid={testId}
        className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
      />
    </Field>
  );
}
