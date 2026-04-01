"use client";

import { useState, useCallback, useEffect } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import {
  useSchedulerJobs,
  useSchedulerRuns,
  useSchedulerStats,
  type SchedulerJob,
  type SchedulerRun,
} from "@/hooks/useSchedulerData";

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function fmtDuration(secs: number | null): string {
  if (secs == null) return "\u2014";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function fmtCountdown(
  totalSecs: number | null,
): string {
  if (totalSecs == null || totalSecs <= 0) return "\u2014";
  const h = Math.floor(totalSecs / 3600);
  const m = Math.floor((totalSecs % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtTimestamp(ts: string | null): string {
  if (!ts) return "\u2014";
  try {
    const d = new Date(ts);
    return d.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return ts;
  }
}

function scheduleLabel(
  days: string[],
  time: string,
): string {
  const allDays = [
    "mon", "tue", "wed", "thu",
    "fri", "sat", "sun",
  ];
  const weekdays = allDays.slice(0, 5);
  const weekend = allDays.slice(5);

  const sorted = days
    .map((d) => d.toLowerCase().trim())
    .filter(Boolean);

  if (sorted.length === 7) {
    return `Every day at ${time} IST`;
  }
  if (
    sorted.length === 5 &&
    weekdays.every((d) => sorted.includes(d))
  ) {
    return `Weekdays at ${time} IST`;
  }
  if (
    sorted.length === 2 &&
    weekend.every((d) => sorted.includes(d))
  ) {
    return `Weekends at ${time} IST`;
  }
  const labels = sorted.map(
    (d) => d.charAt(0).toUpperCase() + d.slice(1),
  );
  return `${labels.join(", ")} at ${time} IST`;
}

// ---------------------------------------------------------------
// Icons
// ---------------------------------------------------------------

function RefreshIcon({ className = "h-4 w-4" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

function ClockIcon({ className = "h-4 w-4" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

function PlusIcon({ className = "h-4 w-4" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function TrashIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function PencilIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

// ---------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    success:
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
    running:
      "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
    failed:
      "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
    paused:
      "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  };
  const dotColors: Record<string, string> = {
    success: "bg-emerald-500",
    running: "bg-indigo-500 animate-pulse",
    failed: "bg-red-500",
    paused: "bg-amber-500",
  };
  return (
    <span
      className={`inline-flex items-center gap-1
        rounded-full px-2 py-0.5 text-[10px]
        font-semibold ${styles[status] ?? styles.paused}`}
    >
      <span
        className={`inline-block h-[5px] w-[5px]
          rounded-full ${dotColors[status] ?? dotColors.paused}`}
      />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

// ---------------------------------------------------------------
// Toggle switch
// ---------------------------------------------------------------

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`
        relative inline-flex h-5 w-9 shrink-0
        cursor-pointer rounded-full
        transition-colors duration-200
        ${
          checked
            ? "bg-emerald-500"
            : "bg-gray-300 dark:bg-gray-600"
        }
      `}
    >
      <span
        className={`
          pointer-events-none inline-block
          h-[14px] w-[14px] rounded-full bg-white
          shadow transition-transform duration-200
          mt-[3px]
          ${checked ? "translate-x-[17px] ml-0" : "translate-x-[3px]"}
        `}
      />
    </button>
  );
}

// ---------------------------------------------------------------
// Stat cards
// ---------------------------------------------------------------

function StatCards() {
  const { stats } = useSchedulerStats();

  const cards = [
    {
      label: "Active Jobs",
      value: String(stats?.active_jobs ?? 0),
      sub: `${stats?.runs_today_running ?? 0} running`,
    },
    {
      label: "Next Run",
      value: fmtCountdown(
        stats?.next_run_seconds ?? null,
      ),
      sub: stats?.next_run_label ?? "No jobs scheduled",
      accent: true,
    },
    {
      label: "Last Run",
      value: stats?.last_run_status
        ? stats.last_run_status.charAt(0).toUpperCase()
          + stats.last_run_status.slice(1)
        : "\u2014",
      sub: stats?.last_run_tickers
        ? `${stats.last_run_tickers} tickers`
        : "\u2014",
      success: stats?.last_run_status === "success",
    },
    {
      label: "Runs Today",
      value: String(stats?.runs_today ?? 0),
      sub: `${stats?.runs_today_success ?? 0} success, ${stats?.runs_today_failed ?? 0} failed`,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {cards.map((c) => (
        <div
          key={c.label}
          className="rounded-xl border border-gray-200
            bg-white p-4 transition-all
            hover:shadow-[0_4px_16px_rgba(0,0,0,0.06)]
            dark:border-gray-800 dark:bg-gray-900/80"
        >
          <p
            className="text-[11px] font-semibold
              uppercase tracking-wide text-gray-400
              dark:text-gray-500"
          >
            {c.label}
          </p>
          <p
            className={`mt-1 font-mono text-2xl
              font-bold tracking-tight
              ${
                c.accent
                  ? "text-indigo-600 dark:text-indigo-400"
                  : c.success
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-gray-900 dark:text-gray-100"
              }
            `}
          >
            {c.value}
          </p>
          <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
            {c.sub}
          </p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------
// Job list
// ---------------------------------------------------------------

function JobList({
  onCreateClick,
  onEditClick,
}: {
  onCreateClick: () => void;
  onEditClick: (job: SchedulerJob) => void;
}) {
  const { jobs, mutate } = useSchedulerJobs();

  const handleToggle = useCallback(
    async (jobId: string, enabled: boolean) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/jobs/${jobId}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ enabled }),
        },
      );
      mutate();
    },
    [mutate],
  );

  const handleDelete = useCallback(
    async (jobId: string) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/jobs/${jobId}`,
        { method: "DELETE" },
      );
      mutate();
    },
    [mutate],
  );

  const handleTrigger = useCallback(
    async (jobId: string) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/jobs/${jobId}/trigger`,
        { method: "POST" },
      );
      mutate();
    },
    [mutate],
  );

  return (
    <div
      className="overflow-hidden rounded-2xl border
        border-gray-200 bg-white
        dark:border-gray-800 dark:bg-gray-900/80"
    >
      {/* Header */}
      <div
        className="flex items-center justify-between
          border-b border-gray-200 px-5 py-3.5
          dark:border-gray-800"
      >
        <div className="flex items-center gap-2">
          <h3 className="text-[15px] font-bold">
            Scheduled Jobs
          </h3>
          <span
            className="rounded-full bg-indigo-50
              px-2 py-0.5 text-[10px] font-bold
              text-indigo-600
              dark:bg-indigo-500/12
              dark:text-indigo-400"
          >
            {jobs.length}
          </span>
        </div>
        <button
          onClick={onCreateClick}
          className="flex items-center gap-1.5 rounded-[10px]
            border border-indigo-600 px-3.5 py-[7px]
            text-xs font-semibold text-indigo-600
            transition-all hover:bg-indigo-600
            hover:text-white
            dark:border-indigo-500
            dark:text-indigo-400
            dark:hover:bg-indigo-500
            dark:hover:text-white"
        >
          <PlusIcon className="h-3.5 w-3.5" />
          New Schedule
        </button>
      </div>

      {/* Rows */}
      {jobs.length === 0 ? (
        <div className="px-5 py-10 text-center text-sm text-gray-400 dark:text-gray-500">
          No scheduled jobs yet. Create one to get started.
        </div>
      ) : (
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {jobs.map((j) => (
            <div
              key={j.job_id}
              className={`flex items-center gap-3.5
                px-5 py-3.5 transition-colors
                hover:bg-indigo-50/50
                dark:hover:bg-indigo-500/5
                ${!j.enabled ? "opacity-50" : ""}
              `}
            >
              <Toggle
                checked={j.enabled}
                onChange={(v) =>
                  handleToggle(j.job_id, v)
                }
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-semibold text-gray-900 dark:text-gray-100">
                  {j.name}
                </p>
                <p className="mt-0.5 font-mono text-[11px] text-gray-400 dark:text-gray-500">
                  {scheduleLabel(
                    j.cron_days,
                    j.cron_time,
                  )}
                </p>
              </div>
              <span
                className="shrink-0 rounded-md
                  bg-blue-50 px-2 py-0.5 text-[10px]
                  font-bold uppercase tracking-wide
                  text-blue-700
                  dark:bg-blue-500/12
                  dark:text-blue-400"
              >
                {j.job_type.replace("_", " ")}
              </span>
              <div className="flex shrink-0 flex-col items-end gap-1 min-w-[100px]">
                <span className="font-mono text-[11px] text-gray-500 dark:text-gray-400">
                  {j.enabled
                    ? j.next_run ?? "\u2014"
                    : "Paused"}
                </span>
                {j.last_run_status && (
                  <StatusBadge
                    status={j.last_run_status}
                  />
                )}
              </div>
              <button
                onClick={() => handleTrigger(j.job_id)}
                disabled={
                  j.last_run_status === "running"
                }
                className="shrink-0 rounded-lg border
                  border-gray-200 px-2.5 py-1.5
                  text-[11px] font-semibold
                  text-indigo-600 transition-all
                  hover:bg-indigo-600 hover:text-white
                  disabled:opacity-40
                  dark:border-gray-700
                  dark:text-indigo-400
                  dark:hover:bg-indigo-500
                  dark:hover:text-white"
              >
                Run Now
              </button>
              <button
                onClick={() => onEditClick(j)}
                className="shrink-0 flex h-[30px]
                  w-[30px] items-center justify-center
                  rounded-lg border border-gray-200
                  text-gray-400 transition-all
                  hover:border-indigo-400
                  hover:bg-indigo-50
                  hover:text-indigo-600
                  dark:border-gray-700
                  dark:text-gray-500
                  dark:hover:border-indigo-500
                  dark:hover:bg-indigo-500/10
                  dark:hover:text-indigo-400"
              >
                <PencilIcon />
              </button>
              <button
                onClick={() => handleDelete(j.job_id)}
                className="shrink-0 flex h-[30px]
                  w-[30px] items-center justify-center
                  rounded-lg border border-gray-200
                  text-gray-400 transition-all
                  hover:border-red-400 hover:bg-red-50
                  hover:text-red-600
                  dark:border-gray-700
                  dark:text-gray-500
                  dark:hover:border-red-500
                  dark:hover:bg-red-500/10
                  dark:hover:text-red-400"
              >
                <TrashIcon />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// New Schedule form
// ---------------------------------------------------------------

const PRESETS = [
  {
    label: "Daily 6 PM",
    days: [
      "mon", "tue", "wed", "thu",
      "fri", "sat", "sun",
    ],
    time: "18:00",
  },
  {
    label: "Weekdays 9:30 PM",
    days: ["mon", "tue", "wed", "thu", "fri"],
    time: "21:30",
  },
  {
    label: "Weekends 8 AM",
    days: ["sat", "sun"],
    time: "08:00",
  },
];

function NewScheduleForm({
  editingJob,
  onCreated,
  onCancel,
}: {
  editingJob: SchedulerJob | null;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [time, setTime] = useState("18:00");
  const [days, setDays] = useState<string[]>([
    "mon", "tue", "wed", "thu",
    "fri", "sat", "sun",
  ]);
  const [cronDates, setCronDates] = useState<number[]>(
    [],
  );
  const [scheduleType, setScheduleType] = useState<
    "weekly" | "monthly"
  >("weekly");
  const [scope, setScope] = useState("all");
  const [saving, setSaving] = useState(false);
  const [activePreset, setActivePreset] = useState(0);

  // Pre-fill when editing
  useEffect(() => {
    if (editingJob) {
      setName(editingJob.name);
      setTime(editingJob.cron_time);
      setScope(editingJob.scope);
      if (
        editingJob.cron_dates &&
        editingJob.cron_dates.length > 0
      ) {
        setScheduleType("monthly");
        setCronDates(editingJob.cron_dates);
        setDays([]);
      } else {
        setScheduleType("weekly");
        setDays(editingJob.cron_days);
        setCronDates([]);
      }
      setActivePreset(-1);
    } else {
      setName("");
      setTime("18:00");
      setDays([
        "mon", "tue", "wed", "thu",
        "fri", "sat", "sun",
      ]);
      setCronDates([]);
      setScheduleType("weekly");
      setScope("all");
      setActivePreset(0);
    }
  }, [editingJob]);

  const applyPreset = (idx: number) => {
    setActivePreset(idx);
    setDays(PRESETS[idx].days);
    setTime(PRESETS[idx].time);
    setScheduleType("weekly");
    setCronDates([]);
  };

  const handleSubmit = useCallback(async () => {
    setSaving(true);
    try {
      const label =
        scheduleType === "monthly"
          ? `Monthly ${cronDates.join(",")} at ${time}`
          : scheduleLabel(days, time);
      const jobName = name.trim() ||
        `Data Refresh - ${label}`;
      const payload = {
        name: jobName,
        job_type: "data_refresh",
        cron_days:
          scheduleType === "weekly" ? days : [],
        cron_dates:
          scheduleType === "monthly"
            ? cronDates
            : [],
        cron_time: time,
        scope,
      };
      if (editingJob) {
        await apiFetch(
          `${API_URL}/admin/scheduler/jobs/${editingJob.job_id}`,
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
          `${API_URL}/admin/scheduler/jobs`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
          },
        );
      }
      setName("");
      onCreated();
    } finally {
      setSaving(false);
    }
  }, [
    name, days, cronDates, scheduleType,
    time, scope, editingJob, onCreated,
  ]);

  return (
    <div
      className="overflow-hidden rounded-2xl border
        border-gray-200 bg-white
        dark:border-gray-800 dark:bg-gray-900/80"
    >
      <div
        className="border-b border-gray-200 px-5
          py-3.5 dark:border-gray-800"
      >
        <h3 className="text-[15px] font-bold">
          {editingJob ? "Edit Schedule" : "New Schedule"}
        </h3>
      </div>
      <div className="space-y-5 p-5">
        {/* Job name */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Job Name (optional)
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Daily Market Close"
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

        {/* Job type */}
        <div>
          <label
            className="mb-1.5 block text-[11px]
              font-semibold uppercase tracking-wide
              text-gray-400 dark:text-gray-500"
          >
            Job Type
          </label>
          <div className="grid grid-cols-2 gap-2">
            <div
              className="flex items-center gap-2.5
                rounded-xl border border-indigo-500
                bg-indigo-50 p-3
                dark:bg-indigo-500/12"
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-blue-100 text-blue-700
                  dark:bg-blue-500/15
                  dark:text-blue-400"
              >
                <RefreshIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Data Refresh
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  Refresh all tickers
                </p>
              </div>
            </div>
            <div
              className="flex cursor-not-allowed
                items-center gap-2.5 rounded-xl border
                border-gray-200 p-3 opacity-40
                dark:border-gray-700"
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-gray-100 text-gray-400
                  dark:bg-gray-800 dark:text-gray-500"
              >
                <ClockIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500">
                  More coming
                </p>
                <p className="text-[10px] text-gray-400">
                  Additional job types
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Presets */}
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
                    setActivePreset(-1);
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
            {PRESETS.map((p, i) => (
              <button
                key={p.label}
                onClick={() => applyPreset(i)}
                className={`rounded-full border px-3
                  py-1.5 text-[11px] font-semibold
                  transition-all
                  ${
                    activePreset === i
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-gray-200 text-gray-400 hover:border-indigo-500 hover:text-indigo-600 dark:border-gray-700 dark:text-gray-500"
                  }
                `}
              >
                {p.label}
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
            onChange={(e) => {
              setTime(e.target.value);
              setActivePreset(-1);
            }}
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

        {/* Submit + Cancel */}
        <div className="space-y-2">
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="w-full rounded-[10px]
              bg-indigo-600 py-3 text-sm font-bold
              text-white transition-all
              hover:bg-indigo-700
              hover:-translate-y-0.5
              hover:shadow-[0_4px_14px_rgba(79,70,229,0.2)]
              disabled:opacity-50"
          >
            {saving
              ? editingJob
                ? "Updating..."
                : "Creating..."
              : editingJob
                ? "Update Schedule"
                : "Create Schedule"}
          </button>
          {editingJob && (
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
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Run timeline
// ---------------------------------------------------------------

function RunTimeline() {
  const { runs } = useSchedulerRuns();

  if (runs.length === 0) {
    return (
      <div
        className="rounded-2xl border border-gray-200
          bg-white p-8 text-center text-sm
          text-gray-400
          dark:border-gray-800 dark:bg-gray-900/80
          dark:text-gray-500"
      >
        No run history yet.
      </div>
    );
  }

  const dotColor: Record<string, string> = {
    success: "bg-emerald-500",
    running: "bg-indigo-500 animate-pulse",
    failed: "bg-red-500",
  };

  return (
    <div
      className="overflow-hidden rounded-2xl border
        border-gray-200 bg-white
        dark:border-gray-800 dark:bg-gray-900/80"
    >
      <div
        className="border-b border-gray-200 px-5
          py-3.5 dark:border-gray-800"
      >
        <h3 className="text-[15px] font-bold">
          Run History
        </h3>
      </div>
      <div className="relative px-5 py-5 pl-10">
        {/* Vertical line */}
        <div
          className="absolute bottom-5 left-[26px]
            top-5 w-0.5 rounded-full bg-gray-200
            dark:bg-gray-700"
        />

        <div className="space-y-5">
          {runs.slice(0, 10).map(
            (r: SchedulerRun) => (
              <div
                key={r.run_id}
                className="relative pl-7"
              >
                {/* Dot */}
                <div
                  className={`absolute -left-[5px]
                    top-1 h-3.5 w-3.5 rounded-full
                    border-2 border-white
                    dark:border-gray-900
                    ${dotColor[r.status] ?? "bg-gray-400"}
                  `}
                />
                <p className="font-mono text-[11px] text-gray-400 dark:text-gray-500">
                  {fmtTimestamp(r.started_at)}
                </p>
                <p className="text-[13px] font-semibold text-gray-900 dark:text-gray-100">
                  {r.job_name}
                </p>
                <div className="mt-1 flex items-center gap-2">
                  <StatusBadge status={r.status} />
                  {r.trigger_type === "catchup" && (
                    <span
                      className="rounded-full bg-amber-100
                        px-2 py-0.5 text-[10px]
                        font-semibold text-amber-700
                        dark:bg-amber-500/15
                        dark:text-amber-400"
                    >
                      Catch-up
                    </span>
                  )}
                  {r.trigger_type === "manual" && (
                    <span
                      className="rounded-full bg-blue-100
                        px-2 py-0.5 text-[10px]
                        font-semibold text-blue-700
                        dark:bg-blue-500/15
                        dark:text-blue-400"
                    >
                      Manual
                    </span>
                  )}
                  <span className="font-mono text-[11px] text-gray-400 dark:text-gray-500">
                    {fmtDuration(r.duration_secs)}
                  </span>
                  <span className="font-mono text-[11px] text-gray-500 dark:text-gray-400">
                    {r.tickers_done}/{r.tickers_total}{" "}
                    tickers
                  </span>
                </div>
              </div>
            ),
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Main SchedulerTab
// ---------------------------------------------------------------

export function SchedulerTab() {
  const { mutate: mutateJobs } = useSchedulerJobs();
  const { mutate: mutateRuns } = useSchedulerRuns();
  const { mutate: mutateStats } = useSchedulerStats();
  const [showForm, setShowForm] = useState(true);
  const [editingJob, setEditingJob] =
    useState<SchedulerJob | null>(null);

  const refreshAll = useCallback(() => {
    mutateJobs();
    mutateRuns();
    mutateStats();
  }, [mutateJobs, mutateRuns, mutateStats]);

  // Auto-refresh every 30s for live run tracking
  useEffect(() => {
    const interval = setInterval(refreshAll, 30_000);
    return () => clearInterval(interval);
  }, [refreshAll]);

  return (
    <div className="space-y-5">
      {/* Stat cards */}
      <StatCards />

      {/* Two-panel: Jobs + Form */}
      <div
        className="grid grid-cols-1 gap-4
          lg:grid-cols-[1.4fr_1fr]"
      >
        <JobList
          onCreateClick={() => {
            setEditingJob(null);
            setShowForm(true);
          }}
          onEditClick={(job) => {
            setEditingJob(job);
            setShowForm(true);
          }}
        />
        {showForm && (
          <NewScheduleForm
            editingJob={editingJob}
            onCreated={() => {
              setEditingJob(null);
              refreshAll();
            }}
            onCancel={() => {
              setEditingJob(null);
            }}
          />
        )}
      </div>

      {/* Run History */}
      <RunTimeline />
    </div>
  );
}
