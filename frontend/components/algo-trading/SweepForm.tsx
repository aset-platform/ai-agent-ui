"use client";

import { useMemo, useState } from "react";
import { useSweepableFields }
  from "@/hooks/useSweepableFields";
import { useStrategies } from "@/hooks/useStrategies";
import {
  startSweepRun,
} from "@/hooks/useSweepRuns";
import type {
  SweepConfig, SweepableField,
} from "@/lib/types/algoSweep";

interface Props {
  onStarted: (sweepRunId: string) => void;
}

function parseValues(
  raw: string, field: SweepableField | undefined,
): { values: (number | string)[]; error: string | null } {
  if (!field) return { values: [], error: null };
  const parts = raw.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return { values: [], error: null };
  }
  if (field.field_type === "int") {
    const out: number[] = [];
    for (const p of parts) {
      const n = Number(p);
      if (!Number.isInteger(n)) {
        return {
          values: [],
          error: `'${p}' is not a valid integer`,
        };
      }
      const lo = Number(field.min_value);
      const hi = Number(field.max_value);
      if (n < lo || n > hi) {
        return {
          values: [],
          error: `${n} is out of range [${lo}, ${hi}]`,
        };
      }
      out.push(n);
    }
    if (new Set(out).size !== out.length) {
      return {
        values: [],
        error: "Duplicate values not allowed",
      };
    }
    return { values: out, error: null };
  }
  // decimal
  const out: string[] = [];
  for (const p of parts) {
    const n = Number(p);
    if (Number.isNaN(n)) {
      return {
        values: [],
        error: `'${p}' is not a valid number`,
      };
    }
    const lo = Number(field.min_value);
    const hi = Number(field.max_value);
    if (n < lo || n > hi) {
      return {
        values: [],
        error: `${n} is out of range [${lo}, ${hi}]`,
      };
    }
    out.push(p);
  }
  if (new Set(out).size !== out.length) {
    return {
      values: [],
      error: "Duplicate values not allowed",
    };
  }
  return { values: out, error: null };
}

export function SweepForm({ onStarted }: Props) {
  const { fields } = useSweepableFields();
  const { strategies } = useStrategies();

  const [strategyId, setStrategyId] = useState<string>("");
  const [periodFrom, setPeriodFrom] = useState<string>(
    "2025-11-23",
  );
  const [periodTo, setPeriodTo] = useState<string>(
    "2026-05-23",
  );
  const [trainDays, setTrainDays] = useState(60);
  const [testDays, setTestDays] = useState(30);
  const [stepDays, setStepDays] = useState(30);
  const [capital, setCapital] = useState("100000");
  const [regimeStratified, setRegimeStratified] = useState(
    false,
  );
  const [fieldKey, setFieldKey] = useState<string>("");
  const [valuesRaw, setValuesRaw] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(
    null,
  );

  const field = useMemo(
    () => fields.find((f) => f.key === fieldKey),
    [fields, fieldKey],
  );
  const { values: parsedValues, error: parseErr } =
    useMemo(() => parseValues(valuesRaw, field), [
      valuesRaw, field,
    ]);

  const canSubmit = Boolean(
    strategyId
    && fieldKey
    && parsedValues.length >= 2
    && parseErr == null
    && !submitting,
  );

  const runtimeEstimate = useMemo(() => {
    if (parsedValues.length < 2) return "—";
    const totalDays =
      (new Date(periodTo).getTime()
       - new Date(periodFrom).getTime())
      / (1000 * 60 * 60 * 24);
    const windows = Math.max(
      1, Math.floor(totalDays / stepDays),
    );
    const secPerWindow = 30;
    const totalSec =
      parsedValues.length * windows * secPerWindow;
    const min = Math.round(totalSec / 60);
    return `~${min} min for ${parsedValues.length} variants`;
  }, [parsedValues, periodFrom, periodTo, stepDays]);

  async function handleSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setFormErr(null);
    try {
      const cfg: SweepConfig = {
        base_strategy_id: strategyId,
        period_start: periodFrom,
        period_end: periodTo,
        train_days: trainDays,
        test_days: testDays,
        step_days: stepDays,
        initial_capital_inr: capital,
        regime_stratified: regimeStratified,
        swept_field: fieldKey,
        swept_values: parsedValues,
      };
      const { sweep_run_id } = await startSweepRun(cfg);
      onStarted(sweep_run_id);
    } catch (exc) {
      setFormErr(
        exc instanceof Error
          ? exc.message
          : "Failed to start sweep",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-4 space-y-3"
      data-testid="sweep-form"
    >
      <h3 className="text-sm font-semibold">
        Parameter sweep
      </h3>

      <label className="flex flex-col gap-1 text-xs">
        <span>Base strategy</span>
        <select
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="sweep-base-strategy-select"
          className="rounded border px-2 py-1"
        >
          <option value="">— select —</option>
          {strategies.map((s: { id: string; name: string }) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <div className="flex gap-2 flex-wrap">
        <label className="flex flex-col text-xs">
          <span>Period from</span>
          <input
            type="date"
            value={periodFrom}
            onChange={(e) => setPeriodFrom(e.target.value)}
            data-testid="sweep-period-from"
            className="rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Period to</span>
          <input
            type="date"
            value={periodTo}
            onChange={(e) => setPeriodTo(e.target.value)}
            data-testid="sweep-period-to"
            className="rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Train days</span>
          <input
            type="number"
            value={trainDays}
            onChange={(e) =>
              setTrainDays(Number(e.target.value))}
            data-testid="sweep-train-days"
            className="rounded border px-2 py-1 w-20"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Test days</span>
          <input
            type="number"
            value={testDays}
            onChange={(e) =>
              setTestDays(Number(e.target.value))}
            data-testid="sweep-test-days"
            className="rounded border px-2 py-1 w-20"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Step days</span>
          <input
            type="number"
            value={stepDays}
            onChange={(e) =>
              setStepDays(Number(e.target.value))}
            data-testid="sweep-step-days"
            className="rounded border px-2 py-1 w-20"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span>Capital ₹</span>
          <input
            type="number"
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
            className="rounded border px-2 py-1 w-28"
          />
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs mt-4">
          <input
            type="checkbox"
            checked={regimeStratified}
            onChange={(e) =>
              setRegimeStratified(e.target.checked)}
            data-testid="sweep-regime-stratified"
          />
          Regime-stratified
        </label>
      </div>

      <div className="border-t pt-3 space-y-2">
        <label className="flex flex-col gap-1 text-xs">
          <span>Sweep parameter</span>
          <select
            value={fieldKey}
            onChange={(e) => setFieldKey(e.target.value)}
            data-testid="sweep-field-select"
            className="rounded border px-2 py-1"
          >
            <option value="">— select —</option>
            {fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span>
            Values (comma-separated)
            {field && (
              <span className="ml-2 text-slate-400">
                ({field.field_type},
                {" "}{field.min_value}–{field.max_value})
              </span>
            )}
          </span>
          <input
            type="text"
            value={valuesRaw}
            onChange={(e) => setValuesRaw(e.target.value)}
            placeholder="3, 7, 14, 21, 28"
            data-testid="sweep-values-input"
            className="rounded border px-2 py-1"
          />
        </label>
        {parseErr && (
          <p className="text-xs text-rose-600">
            {parseErr}
          </p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={handleSubmit}
          data-testid="sweep-submit"
          className={
            "rounded bg-indigo-600 px-3 py-1.5 text-sm "
            + "font-medium text-white "
            + (canSubmit ? "" : "opacity-50 cursor-not-allowed")
          }
        >
          {submitting ? "Starting…" : "Run sweep"}
        </button>
        <span className="text-xs text-slate-500">
          Est. runtime: {runtimeEstimate}
        </span>
        {formErr && (
          <span className="text-xs text-rose-600">
            {formErr}
          </span>
        )}
      </div>
    </div>
  );
}
