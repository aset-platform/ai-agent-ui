"use client";
/**
 * Strategy Levers — non-technical edit surface for the tunable
 * parameters of a strategy AST. The visual builder + JSON pane
 * stay read-only; this panel exposes the numeric / categorical
 * "knobs" most users actually want to tweak between runs:
 *
 *   • Universe scope, market, ticker_type
 *   • Universe filter: min_adtv_inr, is_fno (optional)
 *   • Rebalance.max_positions
 *   • Risk → per_trade (stop_loss_pct, max_qty,
 *     max_holding_days, cooldown_after_failed_exit_days)
 *   • Risk → portfolio (max_exposure_pct, max_concentration_pct)
 *   • Risk → daily (max_loss_pct, max_open_positions)
 *   • Mid-trade regime exit (research opt-in toggle —
 *     mid_trade_regime_check)
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

// ASETPLTFRM-435 — canonical mid-trade regime exit condition.
// Mirrors the v4 research template (rsi2_connors_daily_v4_research_
// regime_exit.json) and the entry-time regime gate v3 uses: don't
// hold positions when NIFTY is below SMA200 OR has lost >5% in
// the last 30 days. Power users can override via the JSON pane.
const DEFAULT_MID_TRADE_REGIME_CHECK: ConditionNode = {
  type: "and",
  operands: [
    {
      type: "compare",
      left: { feature: "nifty_above_sma200" },
      op: ">=",
      right: { literal: 1 },
    },
    {
      type: "compare",
      left: { feature: "nifty_30d_return_pct" },
      op: ">",
      right: { literal: -5.0 },
    },
  ],
};

interface Props {
  ast: StrategyAst;
  onChange: (next: StrategyAst) => void;
}

// Narrowing helpers — the AST blocks come from the backend's
// Pydantic schema but TS sees them as `unknown` in StrategyAst.
type UniverseFilter = {
  ticker_type: string[];
  market: string;
  min_adtv_inr?: number | null;
  is_fno?: boolean;
};
type Universe = {
  type: "scope";
  scope: string;
  filter: UniverseFilter;
};
type Rebalance = { type: "daily"; max_positions: number };
type RiskPerTrade = {
  stop_loss_pct: number;
  max_qty: number;
  max_holding_days?: number | null;
  cooldown_after_failed_exit_days?: number | null;
};
// ConditionNode shape used by mid_trade_regime_check. The schema
// is recursive — at TS level we treat it as Record<string,unknown>
// since the lever UI toggles between null and a canonical default
// rather than letting users edit the tree directly. Power users
// who want a different threshold edit via the JSON pane.
type ConditionNode = Record<string, unknown>;
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
            <OptionalNumberField
              label="Min ADTV (₹/day)"
              value={universe.filter.min_adtv_inr ?? null}
              onChange={(v) =>
                patchFilter({ min_adtv_inr: v })
              }
              min={0}
              step={1000000}
              testId="lever-universe-min-adtv-inr"
              hint="Liquidity floor against stocks.universe_snapshot.adtv_inr_60d (latest snapshot). Blank disables."
            />
            <Field
              label="F&O 200 universe"
              hint="Backtest intersects with the F&O 200 whitelist. Paper/live require caps.allowed_tickers pre-population."
            >
              <label className="flex items-center gap-1 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={Boolean(universe.filter.is_fno)}
                  onChange={(e) =>
                    patchFilter({ is_fno: e.target.checked })
                  }
                  data-testid="lever-universe-is-fno"
                />
                Restrict to F&O 200
              </label>
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
            <OptionalNumberField
              label="Max holding days"
              value={risk.per_trade.max_holding_days ?? null}
              onChange={(v) =>
                patchRisk("per_trade", { max_holding_days: v })
              }
              min={1}
              max={365}
              step={1}
              testId="lever-risk-max-holding-days"
              hint="Force-exit after N calendar days. Blank disables. Pairs well with stop_loss_pct=0 for mean-reversion strategies."
            />
            <OptionalNumberField
              label="Cooldown after failed exit (days)"
              value={
                risk.per_trade.cooldown_after_failed_exit_days ?? null
              }
              onChange={(v) =>
                patchRisk("per_trade", {
                  cooldown_after_failed_exit_days: v,
                })
              }
              min={1}
              max={365}
              step={1}
              testId="lever-risk-cooldown-days"
              hint="Skip new entries on tickers with a recent stop_loss / time_stop / regime_exit within N days. Blank disables. Sweet spot 7–14 days for RSI(2) v3."
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

          {/* ASETPLTFRM-435 — opt-in research primitive.
              See backend/algo/strategy/templates/README.md for
              why mid-trade regime exit is anti-thesis for mean-
              reversion strategies (RSI(2) v4 negative result). */}
          <Group title="Mid-trade regime exit (research opt-in)">
            <Field
              label="Enable mid-trade regime exit"
              hint="WARNING: anti-thesis for mean-reversion strategies (RSI(2)-style). Only enable on trend-following / breakout strategies where regime-hostile = thesis broken. v4 triage documents the negative result on RSI(2). Power users: edit the condition tree via the JSON pane."
            >
              <label className="flex items-center gap-1 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={
                    (ast as { mid_trade_regime_check?: unknown })
                      .mid_trade_regime_check != null
                  }
                  onChange={(e) =>
                    patch({
                      mid_trade_regime_check: (
                        e.target.checked
                          ? DEFAULT_MID_TRADE_REGIME_CHECK
                          : null
                      ),
                    } as Partial<StrategyAst>)
                  }
                  data-testid="lever-mid-trade-regime-check-toggle"
                />
                Force-close all positions when NIFTY regime turns
                hostile
              </label>
            </Field>
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

// Mirrors NumberField but treats blank input as null (feature
// disabled). Used for optional risk fields (max_holding_days) and
// optional universe filters (min_adtv_inr) where None on the
// Pydantic side means "no filter".
function OptionalNumberField({
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
  value: number | null;
  onChange: (v: number | null) => void;
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
        value={value ?? ""}
        min={min}
        max={max}
        step={step}
        placeholder="(disabled)"
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            onChange(null);
            return;
          }
          const v = Number(raw);
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
