"use client";

import { useUserBudget } from "@/hooks/useBudget";

export function BudgetPanel() {
  const { budget, isLoading, error } = useUserBudget();

  if (isLoading || !budget) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="budget-panel"
      >
        Loading budget…
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-md border border-rose-200 bg-rose-50 dark:bg-rose-950/30 p-4 text-sm text-rose-700"
        data-testid="budget-panel-error"
      >
        Budget unavailable
      </div>
    );
  }

  const allocated = Number(budget.allocated_inr);
  const open = Number(budget.open_pos_cost);
  const pending = Number(budget.active_reserved);
  const available = Number(budget.available);
  const kite = budget.kite_available
    ? Number(budget.kite_available)
    : null;

  if (allocated === 0) {
    return (
      <div
        className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-2"
        data-testid="budget-panel"
      >
        <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
          ⚠ Algo trading is paused — no budget allocated.
        </p>
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Set an algo allocation before enabling any
          strategy for live trading. (Allocation modal
          coming in next slice.)
        </p>
      </div>
    );
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-4 space-y-3"
      data-testid="budget-panel"
    >
      <h3 className="text-sm font-semibold">Budget</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile
          label="Allocated"
          value={`₹${allocated.toLocaleString("en-IN")}`}
          testid="budget-tile-allocated"
        />
        <Tile
          label="Open positions"
          value={`₹${open.toLocaleString("en-IN")}`}
          testid="budget-tile-open-positions"
        />
        <Tile
          label="Pending"
          value={`₹${pending.toLocaleString("en-IN")}`}
          testid="budget-tile-pending"
        />
        <Tile
          label="Available"
          value={`₹${available.toLocaleString("en-IN")}`}
          testid="budget-tile-available"
          accent="emerald"
        />
      </div>
      <div
        className="text-xs text-slate-500"
        data-testid="budget-kite-wallet-row"
      >
        Kite wallet:{" "}
        {kite != null
          ? `₹${kite.toLocaleString("en-IN")}`
          : "—"}{" "}
        ⓘ Live gate uses min(internal, Kite) = ₹
        {available.toLocaleString("en-IN")}
      </div>
    </div>
  );
}

function Tile({
  label, value, testid, accent,
}: {
  label: string;
  value: string;
  testid: string;
  accent?: "emerald";
}) {
  const valCls =
    accent === "emerald"
      ? "text-lg font-bold text-emerald-600 dark:text-emerald-400"
      : "text-lg font-semibold";
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 px-3 py-2"
      data-testid={testid}
    >
      <p className="text-[11px] uppercase text-slate-400">
        {label}
      </p>
      <p className={valCls}>{value}</p>
    </div>
  );
}
