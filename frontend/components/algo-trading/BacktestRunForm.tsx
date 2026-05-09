"use client";

import { useState } from "react";

import { startBacktestRun } from "@/hooks/useBacktestRuns";
import { useStrategies } from "@/hooks/useStrategies";

interface Props {
  onSubmitted: (runId: string) => void;
}

function todayMinus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function BacktestRunForm({ onSubmitted }: Props) {
  const { strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");
  const [periodStart, setPeriodStart] = useState<string>(
    todayMinus(180),
  );
  const [periodEnd, setPeriodEnd] = useState<string>(todayMinus(1));
  const [capital, setCapital] = useState<string>("100000.00");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!strategyId) {
      setErr("Pick a strategy");
      return;
    }
    setErr(null);
    setSubmitting(true);
    try {
      const runId = await startBacktestRun(
        strategyId,
        periodStart,
        periodEnd,
        capital,
      );
      onSubmitted(runId);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "Failed to submit");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-wrap items-end gap-3 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 p-3"
      data-testid="backtest-run-form"
    >
      <Field label="Strategy">
        <select
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="backtest-strategy-select"
        >
          <option value="">Select…</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="From">
        <input
          type="date"
          value={periodStart}
          onChange={(e) => setPeriodStart(e.target.value)}
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          data-testid="backtest-period-start"
        />
      </Field>
      <Field label="To">
        <input
          type="date"
          value={periodEnd}
          onChange={(e) => setPeriodEnd(e.target.value)}
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          data-testid="backtest-period-end"
        />
      </Field>
      <Field label="Capital ₹">
        <input
          type="number"
          min={1000}
          step={1000}
          value={capital}
          onChange={(e) => setCapital(e.target.value)}
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm w-28"
          data-testid="backtest-capital"
        />
      </Field>
      <button
        type="submit"
        disabled={submitting}
        className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        data-testid="backtest-submit"
      >
        {submitting ? "Starting…" : "Run backtest"}
      </button>
      {err && (
        <span
          className="text-sm text-rose-600"
          data-testid="backtest-form-error"
        >
          {err}
        </span>
      )}
    </form>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-600 dark:text-slate-400">
      <span>{label}</span>
      {children}
    </label>
  );
}
