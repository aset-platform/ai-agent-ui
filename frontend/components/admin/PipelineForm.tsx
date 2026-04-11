"use client";

import { useState, useCallback, useEffect } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { Pipeline } from "@/hooks/useSchedulerData";

// ---------------------------------------------------------------
// Constants
// ---------------------------------------------------------------

const JOB_TYPES = [
  { value: "data_refresh", label: "Data Refresh" },
  { value: "compute_analytics", label: "Compute Analytics" },
  { value: "run_sentiment", label: "Sentiment Scoring" },
  { value: "run_forecasts", label: "Forecasts" },
  { value: "run_piotroski", label: "Piotroski F-Score" },
] as const;

const ALL_DAYS = [
  { key: "mon", label: "Mon" },
  { key: "tue", label: "Tue" },
  { key: "wed", label: "Wed" },
  { key: "thu", label: "Thu" },
  { key: "fri", label: "Fri" },
  { key: "sat", label: "Sat" },
  { key: "sun", label: "Sun" },
] as const;

interface StepDraft {
  job_type: string;
  job_name: string;
}

// ---------------------------------------------------------------
// PipelineForm
// ---------------------------------------------------------------

interface PipelineFormProps {
  editingPipeline: Pipeline | null;
  onCreated: () => void;
  onCancel: () => void;
}

export default function PipelineForm({
  editingPipeline,
  onCreated,
  onCancel,
}: PipelineFormProps) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState("all");
  const [scheduleType, setScheduleType] = useState<
    "weekly" | "monthly"
  >("weekly");
  const [days, setDays] = useState<string[]>([
    "mon", "tue", "wed", "thu",
    "fri", "sat", "sun",
  ]);
  const [cronDates, setCronDates] = useState<number[]>(
    [],
  );
  const [time, setTime] = useState("08:00");
  const [steps, setSteps] = useState<StepDraft[]>([
    { job_type: "data_refresh", job_name: "Data Refresh" },
  ]);
  const [saving, setSaving] = useState(false);

  // Pre-fill when editing
  useEffect(() => {
    if (editingPipeline) {
      setName(editingPipeline.name);
      setScope(editingPipeline.scope);
      setTime(editingPipeline.cron_time);
      if (
        editingPipeline.cron_days &&
        editingPipeline.cron_days.length > 0
      ) {
        setScheduleType("weekly");
        setDays(editingPipeline.cron_days);
        setCronDates([]);
      } else {
        setScheduleType("monthly");
        setDays([]);
      }
      setSteps(
        editingPipeline.steps.map((s) => ({
          job_type: s.job_type,
          job_name: s.job_name,
        })),
      );
    } else {
      setName("");
      setScope("all");
      setScheduleType("weekly");
      setDays([
        "mon", "tue", "wed", "thu",
        "fri", "sat", "sun",
      ]);
      setCronDates([]);
      setTime("08:00");
      setSteps([
        {
          job_type: "data_refresh",
          job_name: "Data Refresh",
        },
      ]);
    }
  }, [editingPipeline]);

  const addStep = () => {
    setSteps((prev) => [
      ...prev,
      { job_type: "data_refresh", job_name: "Data Refresh" },
    ]);
  };

  const removeStep = (idx: number) => {
    setSteps((prev) => prev.filter((_, i) => i !== idx));
  };

  const updateStep = (
    idx: number,
    field: keyof StepDraft,
    value: string,
  ) => {
    setSteps((prev) =>
      prev.map((s, i) =>
        i === idx ? { ...s, [field]: value } : s,
      ),
    );
  };

  const handleSubmit = useCallback(async () => {
    if (!name.trim()) return;
    if (steps.length === 0) return;
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        scope,
        cron_days:
          scheduleType === "weekly" ? days : [],
        cron_dates:
          scheduleType === "monthly"
            ? cronDates
            : [],
        cron_time: time,
        steps: steps.map((s, i) => ({
          step_order: i + 1,
          job_type: s.job_type,
          job_name: s.job_name,
        })),
      };
      if (editingPipeline) {
        await apiFetch(
          `${API_URL}/admin/scheduler/pipelines/${editingPipeline.pipeline_id}`,
          {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
          },
        );
      } else {
        await apiFetch(
          `${API_URL}/admin/scheduler/pipelines`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
          },
        );
      }
      onCreated();
    } finally {
      setSaving(false);
    }
  }, [
    name, scope, scheduleType, days, cronDates,
    time, steps, editingPipeline, onCreated,
  ]);

  return (
    <div
      className="overflow-hidden rounded-2xl border
        border-gray-200 bg-white
        dark:border-gray-800 dark:bg-gray-900/80"
    >
      <div
        className="flex items-center justify-between
          border-b border-gray-200 px-5 py-3.5
          dark:border-gray-800"
      >
        <h3 className="text-[15px] font-bold">
          {editingPipeline ? "Edit Pipeline" : "New Pipeline"}
        </h3>
        <button
          onClick={onCancel}
          className="text-[11px] font-medium text-gray-400
            hover:text-gray-600 dark:text-gray-500
            dark:hover:text-gray-300"
        >
          Cancel
        </button>
      </div>

      <div className="space-y-5 p-5">
        {/* Pipeline name */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Pipeline Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Daily India Refresh"
            className="w-full rounded-[10px] border
              border-gray-200 bg-gray-50 px-3.5 py-2.5
              text-sm text-gray-900 placeholder-gray-400
              outline-none transition-all
              focus:border-indigo-500
              focus:ring-[3px] focus:ring-indigo-500/25
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-100
              dark:placeholder-gray-500"
          />
        </div>

        {/* Scope */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Scope
          </label>
          <div className="flex gap-1.5">
            {(
              [
                { key: "all", label: "All Tickers" },
                { key: "india", label: "India Only" },
                { key: "us", label: "US Only" },
              ] as const
            ).map((s) => (
              <button
                key={s.key}
                onClick={() => setScope(s.key)}
                className={`flex-1 rounded-[10px]
                  border px-3 py-2.5 text-xs
                  font-semibold transition-all
                  ${
                    scope === s.key
                      ? "border-indigo-500 bg-indigo-50 text-indigo-600 dark:bg-indigo-500/12 dark:text-indigo-400"
                      : "border-gray-200 text-gray-400 hover:border-gray-300 dark:border-gray-700 dark:text-gray-500"
                  }
                `}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Schedule */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Schedule
          </label>
          <div className="mb-2 flex gap-1.5">
            {(
              [
                { key: "weekly", label: "Weekly" },
                { key: "monthly", label: "Monthly" },
              ] as const
            ).map((t) => (
              <button
                key={t.key}
                onClick={() => {
                  setScheduleType(t.key);
                  if (t.key === "monthly") {
                    setDays([]);
                  } else {
                    setCronDates([]);
                  }
                }}
                className={`flex-1 rounded-[10px]
                  border px-3 py-2 text-xs
                  font-semibold transition-all
                  ${
                    scheduleType === t.key
                      ? "border-indigo-500 bg-indigo-50 text-indigo-600 dark:bg-indigo-500/12 dark:text-indigo-400"
                      : "border-gray-200 text-gray-400 hover:border-gray-300 dark:border-gray-700 dark:text-gray-500"
                  }
                `}
              >
                {t.label}
              </button>
            ))}
          </div>

          {scheduleType === "monthly" ? (
            <div className="grid grid-cols-7 gap-1.5">
              {Array.from(
                { length: 31 },
                (_, i) => i + 1,
              ).map((d) => (
                <button
                  key={d}
                  onClick={() =>
                    setCronDates((prev) =>
                      prev.includes(d)
                        ? prev.filter((x) => x !== d)
                        : [...prev, d].sort(
                            (a, b) => a - b,
                          ),
                    )
                  }
                  className={`rounded-lg border
                    px-1.5 py-1.5 text-[11px]
                    font-semibold transition-all
                    ${
                      cronDates.includes(d)
                        ? "border-indigo-500 bg-indigo-600 text-white"
                        : "border-gray-200 text-gray-400 hover:border-indigo-400 dark:border-gray-700 dark:text-gray-500"
                    }
                  `}
                >
                  {d}
                </button>
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {ALL_DAYS.map((d) => (
                <button
                  key={d.key}
                  onClick={() =>
                    setDays((prev) =>
                      prev.includes(d.key)
                        ? prev.filter((x) => x !== d.key)
                        : [...prev, d.key],
                    )
                  }
                  className={`rounded-full border px-3
                    py-1.5 text-[11px] font-semibold
                    transition-all
                    ${
                      days.includes(d.key)
                        ? "border-indigo-600 bg-indigo-600 text-white"
                        : "border-gray-200 text-gray-400 hover:border-indigo-500 hover:text-indigo-600 dark:border-gray-700 dark:text-gray-500"
                    }
                  `}
                >
                  {d.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Time */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Time (IST)
          </label>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="w-full rounded-[10px] border
              border-gray-200 bg-gray-50 px-3.5 py-2.5
              font-mono text-sm text-gray-900
              outline-none transition-all
              focus:border-indigo-500
              focus:ring-[3px] focus:ring-indigo-500/25
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-100"
          />
          <p className="mt-1 font-mono text-[11px] text-gray-400 dark:text-gray-500">
            Asia/Kolkata (UTC+5:30)
          </p>
        </div>

        {/* Steps */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Pipeline Steps
          </label>
          <div className="space-y-2">
            {steps.map((step, idx) => (
              <div
                key={idx}
                className="flex items-center gap-2
                  rounded-xl border border-gray-200
                  bg-gray-50 p-3
                  dark:border-gray-700
                  dark:bg-gray-800"
              >
                <span
                  className="flex h-6 w-6 shrink-0
                    items-center justify-center
                    rounded-full bg-indigo-100
                    text-[11px] font-bold
                    text-indigo-600
                    dark:bg-indigo-500/15
                    dark:text-indigo-400"
                >
                  {idx + 1}
                </span>
                <select
                  value={step.job_type}
                  onChange={(e) => {
                    const jt = e.target.value;
                    const label =
                      JOB_TYPES.find(
                        (j) => j.value === jt,
                      )?.label ?? jt;
                    updateStep(idx, "job_type", jt);
                    updateStep(idx, "job_name", label);
                  }}
                  className="flex-1 rounded-lg border
                    border-gray-200 bg-white px-2.5
                    py-1.5 text-[12px]
                    dark:border-gray-600
                    dark:bg-gray-700
                    dark:text-gray-200"
                >
                  {JOB_TYPES.map((j) => (
                    <option key={j.value} value={j.value}>
                      {j.label}
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  value={step.job_name}
                  onChange={(e) =>
                    updateStep(
                      idx,
                      "job_name",
                      e.target.value,
                    )
                  }
                  placeholder="Step name"
                  className="flex-1 rounded-lg border
                    border-gray-200 bg-white px-2.5
                    py-1.5 text-[12px]
                    placeholder-gray-400 outline-none
                    focus:border-indigo-500
                    dark:border-gray-600
                    dark:bg-gray-700
                    dark:text-gray-200
                    dark:placeholder-gray-500"
                />
                {steps.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeStep(idx)}
                    className="flex h-6 w-6 shrink-0
                      items-center justify-center
                      rounded-md text-gray-400
                      hover:bg-red-50 hover:text-red-500
                      dark:hover:bg-red-500/10
                      dark:hover:text-red-400
                      transition-colors"
                    title="Remove step"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      className="h-3.5 w-3.5"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              onClick={addStep}
              className="flex items-center gap-1.5
                rounded-xl border border-dashed
                border-gray-300 px-3 py-2 text-[11px]
                font-semibold text-gray-400
                transition-all hover:border-indigo-400
                hover:text-indigo-600
                dark:border-gray-600
                dark:text-gray-500
                dark:hover:border-indigo-500
                dark:hover:text-indigo-400"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-3.5 w-3.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
              Add Step
            </button>
          </div>
        </div>

        {/* Submit + Cancel */}
        <div className="space-y-2">
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim() || steps.length === 0}
            className="w-full rounded-[10px]
              bg-indigo-600 py-3 text-sm font-bold
              text-white transition-all
              hover:bg-indigo-700
              hover:-translate-y-0.5
              hover:shadow-[0_4px_14px_rgba(79,70,229,0.2)]
              disabled:opacity-50"
          >
            {saving
              ? editingPipeline
                ? "Updating..."
                : "Creating..."
              : editingPipeline
                ? "Update Pipeline"
                : "Create Pipeline"}
          </button>
          <button
            onClick={onCancel}
            className="w-full rounded-[10px] border
              border-gray-200 py-3 text-sm
              font-bold text-gray-500
              transition-all hover:bg-gray-50
              dark:border-gray-700
              dark:text-gray-400
              dark:hover:bg-gray-800"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
