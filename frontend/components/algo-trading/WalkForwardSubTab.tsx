"use client";

import { useEffect, useMemo, useState } from "react";

import {
  startWalkForwardRun,
  useWalkForwardRun,
  useWalkForwardRuns,
  type WalkForwardAggregate,
  type WalkForwardResult,
  type WindowSummary,
} from "@/hooks/useWalkForwardRuns";
import { useStrategies } from "@/hooks/useStrategies";

import {
  WalkForwardEquityCurves,
  type WindowCurve,
} from "./WalkForwardEquityCurves";

function computeAggregate(
  rows: WindowSummary[],
): WalkForwardAggregate | null {
  if (rows.length === 0) return null;
  const completed = rows.filter((r) => r.status === "completed");
  const num = (s: string | null): number | null =>
    s == null ? null : Number(s);
  const pnl = completed
    .map((r) => num(r.total_pnl_pct))
    .filter((n): n is number => n != null);
  const wr = completed
    .map((r) => num(r.win_rate_pct))
    .filter((n): n is number => n != null);
  const dd = completed
    .map((r) => num(r.max_drawdown_pct))
    .filter((n): n is number => n != null);
  const mean = (xs: number[]) =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
  const std = (xs: number[]) => {
    if (xs.length < 2) return 0;
    const m = mean(xs);
    const v =
      xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length;
    return Math.sqrt(v);
  };
  return {
    avg_pnl_pct: mean(pnl).toFixed(2),
    avg_win_rate_pct: mean(wr).toFixed(2),
    avg_max_drawdown_pct: mean(dd).toFixed(2),
    std_pnl_pct: std(pnl).toFixed(2),
    window_count: rows.length,
    completed_count: completed.length,
  };
}

function todayMinus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function fmtPct(v: string | null | undefined): string {
  if (v == null) return "—";
  return `${Number(v).toFixed(2)}%`;
}

function AggCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const cls =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "bad"
        ? "text-rose-600 dark:text-rose-400"
        : "text-slate-900 dark:text-slate-100";
  return (
    <div className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2">
      <div className="text-xs text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div className={`text-lg font-semibold ${cls}`}>{value}</div>
    </div>
  );
}

// REGIME-5 ─ 5-gate traffic-light strip
const GATE_KEYS = [
  { key: "max_dd_ok", label: "Max DD" },
  { key: "recovery_ok", label: "Recovery" },
  { key: "per_regime_non_neg", label: "Per-regime ≥0" },
  { key: "dsr_ok", label: "DSR ≥ 0.95" },
  { key: "pbo_ok", label: "PBO ≤ 0.30" },
] as const;

function GateLight({
  k, label, passed, title,
}: {
  k: string;
  label: string;
  passed: boolean | undefined;
  title: string;
}) {
  const cls = passed === undefined
    ? "bg-slate-300 dark:bg-slate-600"
    : passed
      ? "bg-emerald-500"
      : "bg-rose-500";
  const text = passed === undefined
    ? "text-slate-400"
    : passed
      ? "text-emerald-700 dark:text-emerald-300"
      : "text-rose-700 dark:text-rose-300";
  return (
    <div
      className="flex items-center gap-1.5"
      data-testid={`walkforward-gate-light-${k}`}
      title={title}
    >
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${cls}`} />
      <span className={`text-[11px] font-medium ${text}`}>
        {label}
      </span>
    </div>
  );
}

function GateStrip({ aggregate }: { aggregate: WalkForwardAggregate }) {
  const gates = aggregate.gates_passed || {};
  const dsr = aggregate.dsr ?? "—";
  const pbo = aggregate.pbo ?? "—";
  const recoveryMo = aggregate.recovery_months ?? 0;
  const maxDd = aggregate.avg_max_drawdown_pct;
  const titles: Record<string, string> = {
    max_dd_ok: `Max DD: ${maxDd}%`,
    recovery_ok: `Recovery: ${recoveryMo}mo`,
    per_regime_non_neg: "All regimes non-negative",
    dsr_ok: `DSR: ${dsr}`,
    pbo_ok: `PBO: ${pbo}`,
  };
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-900/40"
      data-testid="walkforward-gates-strip"
    >
      <span className="text-[11px] font-semibold uppercase text-slate-500">
        Quality gates
      </span>
      {GATE_KEYS.map(({ key, label }) => (
        <GateLight
          key={key}
          k={key}
          label={label}
          passed={gates[key]}
          title={titles[key]}
        />
      ))}
    </div>
  );
}

function PerRegimeGrid({ rows }: {
  rows: NonNullable<WalkForwardAggregate["per_regime"]>;
}) {
  return (
    <div
      className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700"
      data-testid="walkforward-per-regime-grid"
    >
      <table className="min-w-full text-xs">
        <thead className="bg-slate-100 dark:bg-slate-800">
          <tr>
            {[
              "Regime", "Days", "Return %", "Sharpe",
              "Sortino", "Max DD %", "Hit %",
            ].map((h) => (
              <th
                key={h}
                className="px-2 py-1.5 text-left font-medium text-slate-600 dark:text-slate-300"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.regime} className="border-t border-slate-200 dark:border-slate-700">
              <td className="px-2 py-1.5 font-semibold">{r.regime}</td>
              <td className="px-2 py-1.5">{r.n_days}</td>
              <td className="px-2 py-1.5">{r.cum_return_pct}</td>
              <td className="px-2 py-1.5">{r.sharpe}</td>
              <td className="px-2 py-1.5">{r.sortino}</td>
              <td className="px-2 py-1.5">{r.max_dd_pct}</td>
              <td className="px-2 py-1.5">{r.hit_rate}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function AggregateCards({ agg }: { agg: WalkForwardAggregate }) {
  const pnlPositive = Number(agg.avg_pnl_pct) >= 0;
  return (
    <div
      className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6"
      data-testid="walkforward-aggregate-cards"
    >
      <AggCard
        label="Avg PnL %"
        value={fmtPct(agg.avg_pnl_pct)}
        tone={pnlPositive ? "good" : "bad"}
      />
      <AggCard
        label="Avg Win Rate"
        value={fmtPct(agg.avg_win_rate_pct)}
      />
      <AggCard
        label="Avg Max DD"
        value={fmtPct(agg.avg_max_drawdown_pct)}
        tone="bad"
      />
      <AggCard label="Std PnL %" value={fmtPct(agg.std_pnl_pct)} />
      <AggCard
        label="Windows"
        value={String(agg.window_count)}
      />
      <AggCard
        label="Completed"
        value={`${agg.completed_count}/${agg.window_count}`}
      />
    </div>
  );
}

function WindowTable({
  summaries,
}: {
  summaries: ReturnType<
    typeof useWalkForwardRun
  >["run"]["window_summaries"];
}) {
  if (!summaries || summaries.length === 0) return null;
  return (
    <div
      className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700"
      data-testid="walkforward-window-table"
    >
      <table className="w-full text-xs">
        <thead className="bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
          <tr>
            <th className="px-3 py-1.5 text-left">#</th>
            <th className="px-3 py-1.5 text-left">Test period</th>
            <th className="px-3 py-1.5 text-right">PnL %</th>
            <th className="px-3 py-1.5 text-right">Win rate</th>
            <th className="px-3 py-1.5 text-right">Max DD</th>
            <th className="px-3 py-1.5 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {summaries.map((w) => (
            <tr
              key={w.window_index}
              className="border-t border-slate-100 dark:border-slate-800"
              data-testid={`walkforward-window-row-${w.window_index}`}
            >
              <td className="px-3 py-1">{w.window_index + 1}</td>
              <td className="px-3 py-1">
                {w.test_start} → {w.test_end}
              </td>
              <td
                className={`px-3 py-1 text-right ${
                  w.total_pnl_pct != null &&
                  Number(w.total_pnl_pct) >= 0
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400"
                }`}
              >
                {fmtPct(w.total_pnl_pct)}
              </td>
              <td className="px-3 py-1 text-right">
                {fmtPct(w.win_rate_pct)}
              </td>
              <td className="px-3 py-1 text-right text-rose-600 dark:text-rose-400">
                {fmtPct(w.max_drawdown_pct)}
              </td>
              <td className="px-3 py-1 capitalize">{w.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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

const inputCls =
  "rounded border border-slate-300 dark:border-slate-600 " +
  "bg-white dark:bg-slate-800 px-2 py-1 text-sm";

export function WalkForwardSubTab() {
  const { strategies } = useStrategies();
  const { rows: history } = useWalkForwardRuns();

  const [strategyId, setStrategyId] = useState("");
  const [periodStart, setPeriodStart] = useState(todayMinus(730));
  const [periodEnd, setPeriodEnd] = useState(todayMinus(1));
  const [trainDays, setTrainDays] = useState(180);
  const [testDays, setTestDays] = useState(30);
  const [stepDays, setStepDays] = useState(30);
  const [capital, setCapital] = useState("100000.00");
  // Regime stratification is opt-in. Indian markets sit in
  // SIDEWAYS ~90%+ of the time; with BULL / BEAR rare, the
  // stratified gate (every regime must appear in each train
  // slice) wipes out every fold. Users turn it on only when
  // they're specifically testing a bull / bear strategy.
  const [regimeStratified, setRegimeStratified] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(
    () => {
      if (typeof window === "undefined") return null;
      const params = new URLSearchParams(
        window.location.search,
      );
      return params.get("walkforward_id");
    },
  );

  const effectiveRunId =
    activeRunId ?? history[0]?.run_id ?? null;
  const { run, error: runErr } = useWalkForwardRun(effectiveRunId);

  const [selectedIndices, setSelectedIndices] =
    useState<Set<number> | null>(null);

  // Reset selection when the run changes (new run = all windows on).
  useEffect(() => {
    setSelectedIndices(null);
  }, [effectiveRunId]);

  const filteredSummaries = useMemo(() => {
    const all = run?.window_summaries ?? [];
    if (selectedIndices == null) return all;
    return all.filter((w) => selectedIndices.has(w.window_index));
  }, [run, selectedIndices]);

  const computedAgg = useMemo(
    () => computeAggregate(filteredSummaries),
    [filteredSummaries],
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!strategyId) {
      setFormErr("Pick a strategy");
      return;
    }
    setFormErr(null);
    setSubmitting(true);
    try {
      const id = await startWalkForwardRun(
        strategyId,
        periodStart,
        periodEnd,
        trainDays,
        testDays,
        stepDays,
        capital,
        regimeStratified,
      );
      setActiveRunId(id);
    } catch (exc) {
      setFormErr(
        exc instanceof Error ? exc.message : "Failed to submit",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const curves: WindowCurve[] =
    run?.window_summaries?.map((w) => ({
      windowIndex: w.window_index,
      testStart: w.test_start,
      testEnd: w.test_end,
      status: w.status,
      points: w.equity_curve,
    })) ?? [];

  return (
    <div
      className="space-y-4"
      data-testid="walkforward-sub-tab"
    >
      {/* Config form */}
      <form
        onSubmit={handleSubmit}
        className="flex flex-wrap items-end gap-3 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 p-3"
        data-testid="walkforward-run-form"
      >
        <Field label="Strategy">
          <select
            className={inputCls}
            value={strategyId}
            onChange={(e) => setStrategyId(e.target.value)}
            data-testid="walkforward-strategy-select"
          >
            <option value="">Select…</option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Period from">
          <input
            type="date"
            value={periodStart}
            onChange={(e) => setPeriodStart(e.target.value)}
            className={inputCls}
            data-testid="walkforward-period-start"
          />
        </Field>
        <Field label="Period to">
          <input
            type="date"
            value={periodEnd}
            onChange={(e) => setPeriodEnd(e.target.value)}
            className={inputCls}
            data-testid="walkforward-period-end"
          />
        </Field>
        <Field label="Train days">
          <input
            type="number"
            min={1}
            step={1}
            value={trainDays}
            onChange={(e) => setTrainDays(Number(e.target.value))}
            className={`${inputCls} w-20`}
            data-testid="walkforward-train-days"
          />
        </Field>
        <Field label="Test days">
          <input
            type="number"
            min={1}
            step={1}
            value={testDays}
            onChange={(e) => setTestDays(Number(e.target.value))}
            className={`${inputCls} w-20`}
            data-testid="walkforward-test-days"
          />
        </Field>
        <Field label="Step days">
          <input
            type="number"
            min={1}
            step={1}
            value={stepDays}
            onChange={(e) => setStepDays(Number(e.target.value))}
            className={`${inputCls} w-20`}
            data-testid="walkforward-step-days"
          />
        </Field>
        <Field label="Capital ₹">
          <input
            type="number"
            min={1000}
            step={1000}
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
            className={`${inputCls} w-28`}
            data-testid="walkforward-capital"
          />
        </Field>
        <label
          className="inline-flex items-center gap-1.5 text-xs text-slate-700 dark:text-slate-300"
          title="When ON, each fold's train slice must contain every regime (BULL/SIDEWAYS/BEAR) present in the full period. Useful for regime-specific strategies; defaults OFF because Indian markets sit in SIDEWAYS most of the time and stratification can filter every fold."
        >
          <input
            type="checkbox"
            checked={regimeStratified}
            onChange={(e) => setRegimeStratified(e.target.checked)}
            data-testid="walkforward-regime-stratified"
          />
          Regime-stratified
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          data-testid="walkforward-submit"
        >
          {submitting ? "Starting…" : "Run walk-forward CV"}
        </button>
        {formErr && (
          <span
            className="text-sm text-rose-600"
            data-testid="walkforward-form-error"
          >
            {formErr}
          </span>
        )}
      </form>

      {/* History strip */}
      {history.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="text-slate-500">Recent:</span>
          {history.slice(0, 8).map((h) => (
            <button
              key={h.run_id}
              onClick={() => setActiveRunId(h.run_id)}
              className={`rounded px-2 py-0.5 border ${
                h.run_id === effectiveRunId
                  ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30"
                  : "border-slate-200 dark:border-slate-700"
              }`}
              data-testid={`walkforward-history-${h.run_id}`}
            >
              {h.period_start}…{h.period_end} · {h.status}
            </button>
          ))}
        </div>
      )}

      {/* Error banners */}
      {runErr && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="walkforward-load-error"
        >
          {runErr}
        </div>
      )}

      {run && run.status === "failed" && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="walkforward-run-error"
        >
          Run failed: {run.error_text ?? "unknown error"}
        </div>
      )}

      {run &&
        (run.status === "pending" || run.status === "running") && (
          <WalkForwardProgressBanner run={run} />
        )}

      {/* Results */}
      {run && run.status === "completed" && (
        <>
          {computedAgg && <AggregateCards agg={computedAgg} />}
          {run.aggregate && (
            <GateStrip aggregate={run.aggregate} />
          )}
          {run.aggregate?.per_regime
            && run.aggregate.per_regime.length > 0 && (
            <PerRegimeGrid rows={run.aggregate.per_regime} />
          )}
          <WalkForwardEquityCurves
            curves={curves}
            initialCapitalInr={capital}
            selectedIndices={selectedIndices ?? undefined}
            onSelectionChange={setSelectedIndices}
          />
          <WindowTable summaries={run.window_summaries} />
        </>
      )}
    </div>
  );
}

function WalkForwardProgressBanner({
  run,
}: {
  run: WalkForwardResult;
}) {
  // Hooks must precede any conditional return — React's
  // rules-of-hooks enforces a stable call order across renders.
  // The 2-second tick keeps the ETA fresh in step with the SWR
  // poll cadence so the banner updates roughly in lockstep with
  // each new ``done`` value from the backend.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 2_000);
    return () => clearInterval(id);
  }, []);
  const p = run.progress;
  // Pre-spawn (no child rows yet) — minimal "starting" state.
  if (!p || p.total_estimated === 0) {
    return (
      <div
        className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
        data-testid="walkforward-run-progress"
      >
        Walk-forward is {run.status}… preparing folds.
      </div>
    );
  }
  // Live progress + ETA.
  const done = p.done;
  const total = p.total_estimated;
  const running = p.running;
  const pct = Math.min(
    100,
    total > 0 ? Math.floor((done / total) * 100) : 0,
  );
  const startedMs = p.started_at ? Date.parse(p.started_at) : nowMs;
  const elapsedMs = Math.max(1, nowMs - startedMs);
  // Skip ETA until we have at least 2 folds completed (one-fold
  // samples are too noisy).
  let etaText = "";
  if (done >= 2 && done < total) {
    const avgMs = elapsedMs / done;
    const remainingMs = avgMs * (total - done);
    const remSec = Math.round(remainingMs / 1000);
    etaText =
      remSec > 90
        ? ` · ETA ~${Math.round(remSec / 60)} min`
        : ` · ETA ~${remSec}s`;
  }
  return (
    <div
      className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
      data-testid="walkforward-run-progress"
    >
      <div className="flex items-center justify-between">
        <span>
          Walk-forward is {run.status} — fold {done} of {total} done
          {running > 0 ? ` (1 running)` : ""}
          {etaText}
        </span>
        <span
          className="text-xs font-medium"
          data-testid="walkforward-run-progress-pct"
        >
          {pct}%
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded bg-amber-200/60"
        aria-hidden
      >
        <div
          className="h-full bg-amber-600 transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
