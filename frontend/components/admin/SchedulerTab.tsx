"use client";

import { useState, useCallback, useEffect } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  useSchedulerJobs,
  useSchedulerPipelines,
  useSchedulerRuns,
  useSchedulerRunsFiltered,
  useSchedulerStats,
  type SchedulerJob,
  type SchedulerRun,
  type Pipeline,
} from "@/hooks/useSchedulerData";
import PipelineDAG from "./PipelineDAG";
import PipelineForm from "./PipelineForm";

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function fmtDuration(secs: number | null): string {
  if (secs == null) return "\u2014";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/**
 * Progress-counter label for a job.  The scheduler
 * re-uses `tickers_total`/`tickers_done` for every
 * job type, but recommendation jobs iterate *users*
 * (one pass per user per scope), not tickers.
 */
function progressUnit(
  jobType: string,
  total: number,
): string {
  const plural = total === 1 ? "" : "s";
  if (jobType === "recommendations")
    return `user${plural}`;
  return `ticker${plural}`;
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

function ActivityIcon({ className = "h-4 w-4" }) {
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
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}

function TrendingUpIcon({ className = "h-4 w-4" }) {
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
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
      <polyline points="17 6 23 6 23 12" />
    </svg>
  );
}

function StarIcon({ className = "h-4 w-4" }) {
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
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
}

function CheckCircleIcon({ className = "h-4 w-4" }) {
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
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function ZapIcon({ className = "h-4 w-4" }) {
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
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
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
    cancelled:
      "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-400",
    paused:
      "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  };
  const dotColors: Record<string, string> = {
    success: "bg-emerald-500",
    running: "bg-indigo-500 animate-pulse",
    failed: "bg-red-500",
    cancelled: "bg-orange-500",
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
        ? `${stats.last_run_tickers} processed`
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
// Split Run button (Run Now + Force Run dropdown)
// ---------------------------------------------------------------

function SplitRunButton({
  onRun,
  onForceRun,
  forceDisabled = false,
  forceDisabledHint,
}: {
  onRun: () => void;
  onForceRun: () => void;
  forceDisabled?: boolean;
  forceDisabledHint?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative shrink-0">
      <div className="flex items-stretch">
        <button
          onClick={onRun}
          className="rounded-l-lg border border-r-0
            border-gray-200 px-2.5 py-1.5
            text-[11px] font-semibold
            text-indigo-600 transition-all
            hover:bg-indigo-600 hover:text-white
            dark:border-gray-700
            dark:text-indigo-400
            dark:hover:bg-indigo-500
            dark:hover:text-white"
        >
          Run Now
        </button>
        <button
          onClick={() => setOpen(!open)}
          className="rounded-r-lg border
            border-gray-200 px-1 py-1.5
            text-indigo-600 transition-all
            hover:bg-indigo-600 hover:text-white
            dark:border-gray-700
            dark:text-indigo-400
            dark:hover:bg-indigo-500
            dark:hover:text-white"
        >
          <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none"
            stroke="currentColor" strokeWidth="2.5">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>
      {open && (
        <>
          <div
            className="fixed inset-0 z-[60]"
            onClick={() => setOpen(false)}
          />
          <div
            className="absolute right-0 top-full z-[70] mt-1
              bg-white dark:bg-zinc-800
              border border-zinc-200 dark:border-zinc-700
              rounded-lg shadow-xl py-1 min-w-[130px]"
          >
            {forceDisabled ? (
              <button
                type="button"
                disabled
                title={
                  forceDisabledHint ??
                  "Force Run is disabled for this job."
                }
                className="flex items-center gap-2 w-full
                  text-left px-3 py-1.5 text-[11px]
                  font-semibold text-gray-400
                  dark:text-gray-500
                  cursor-not-allowed
                  opacity-60"
              >
                <ZapIcon className="h-3 w-3" />
                Force Run
                <span className="ml-auto rounded bg-gray-200 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                  Off
                </span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onForceRun();
                }}
                className="flex items-center gap-2 w-full
                  text-left px-3 py-1.5 text-[11px]
                  font-semibold text-amber-600
                  dark:text-amber-400
                  hover:bg-amber-50
                  dark:hover:bg-amber-500/10
                  transition-colors"
              >
                <ZapIcon className="h-3 w-3" />
                Force Run
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Job list
// ---------------------------------------------------------------

function JobList({
  onCreateClick,
  onEditClick,
  runs,
}: {
  onCreateClick: () => void;
  onEditClick: (job: SchedulerJob) => void;
  runs: SchedulerRun[];
}) {
  const { jobs, mutate } = useSchedulerJobs();
  const [deleteJobId, setDeleteJobId] =
    useState<string | null>(null);

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

  const confirmDelete = useCallback(
    async () => {
      if (!deleteJobId) return;
      await apiFetch(
        `${API_URL}/admin/scheduler/jobs/${deleteJobId}`,
        { method: "DELETE" },
      );
      setDeleteJobId(null);
      mutate();
    },
    [deleteJobId, mutate],
  );

  const handleTrigger = useCallback(
    async (jobId: string, force = false) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/jobs/${jobId}/trigger`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force }),
        },
      );
      mutate();
    },
    [mutate],
  );

  const handleStop = useCallback(
    async (jobId: string) => {
      const running = runs.find(
        (r) =>
          r.job_id === jobId &&
          r.status === "running",
      );
      if (!running) return;
      await apiFetch(
        `${API_URL}/admin/scheduler/runs/${running.run_id}/cancel`,
        { method: "POST" },
      );
      mutate();
    },
    [runs, mutate],
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
              {j.last_run_status === "running" ? (
                <button
                  onClick={() => handleStop(j.job_id)}
                  className="shrink-0 rounded-lg border
                    border-red-300 bg-red-50 px-2.5
                    py-1.5 text-[11px] font-semibold
                    text-red-600 transition-all
                    hover:bg-red-600 hover:text-white
                    dark:border-red-700
                    dark:bg-red-500/10
                    dark:text-red-400
                    dark:hover:bg-red-500
                    dark:hover:text-white"
                >
                  Stop
                </button>
              ) : (
                <SplitRunButton
                  onRun={() => handleTrigger(j.job_id, false)}
                  onForceRun={() => handleTrigger(j.job_id, true)}
                  forceDisabled={
                    j.job_type === "recommendations"
                  }
                  forceDisabledHint="Force Run is disabled for Recommendations — use Admin → Recommendations → Force-refresh for per-user test runs."
                />
              )}
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
                onClick={() => setDeleteJobId(j.job_id)}
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

      <ConfirmDialog
        open={deleteJobId !== null}
        title="Delete Scheduled Job"
        message={
          `Are you sure you want to delete "${
            jobs.find(
              (j) => j.job_id === deleteJobId,
            )?.name ?? deleteJobId
          }"? This action cannot be undone.`
        }
        confirmLabel="Delete"
        variant="danger"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteJobId(null)}
      />
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
  const [jobType, setJobType] = useState("data_refresh");
  const [force, setForce] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activePreset, setActivePreset] = useState(0);

  // Pre-fill when editing
  useEffect(() => {
    if (editingJob) {
      setName(editingJob.name);
      setTime(editingJob.cron_time);
      setScope(editingJob.scope);
      setJobType(editingJob.job_type || "data_refresh");
      setForce(editingJob.force ?? false);
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
      setJobType("data_refresh");
      setForce(false);
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
      const typeLabelMap: Record<string, string> = {
        data_refresh: "Data Refresh",
        compute_analytics: "Compute Analytics",
        run_sentiment: "Run Sentiment",
        run_piotroski: "Piotroski F-Score",
        run_forecasts: "Run Forecasts",
        recommendations: "Recommendations",
        recommendation_outcomes: "Outcome Tracker",
        iceberg_maintenance: "Iceberg Maintenance",
      };
      const typeLabel = typeLabelMap[jobType]
        || "Data Refresh";
      const jobName = name.trim() ||
        `${typeLabel} - ${label}`;
      const payload = {
        name: jobName,
        job_type: jobType,
        cron_days:
          scheduleType === "weekly" ? days : [],
        cron_dates:
          scheduleType === "monthly"
            ? cronDates
            : [],
        cron_time: time,
        scope,
        force,
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
    time, scope, jobType, force, editingJob, onCreated,
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
            <button
              type="button"
              onClick={() => setJobType("data_refresh")}
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "data_refresh"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
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
            </button>
            <button
              type="button"
              onClick={() => setJobType("compute_analytics")}
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "compute_analytics"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-amber-100 text-amber-700
                  dark:bg-amber-500/15
                  dark:text-amber-400"
              >
                <ActivityIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Compute Analytics
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  Technical indicators
                </p>
              </div>
            </button>
            <button
              type="button"
              onClick={() => setJobType("run_sentiment")}
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "run_sentiment"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-teal-100 text-teal-700
                  dark:bg-teal-500/15
                  dark:text-teal-400"
              >
                <ZapIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Run Sentiment
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  LLM headline scoring
                </p>
              </div>
            </button>
            <button
              type="button"
              onClick={() => setJobType("run_forecasts")}
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "run_forecasts"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-purple-100 text-purple-700
                  dark:bg-purple-500/15
                  dark:text-purple-400"
              >
                <TrendingUpIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Run Forecasts
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  Prophet price forecasts
                </p>
              </div>
            </button>
            <button
              type="button"
              onClick={() => setJobType("run_piotroski")}
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "run_piotroski"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-amber-100 text-amber-700
                  dark:bg-amber-500/15
                  dark:text-amber-400"
              >
                <ActivityIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Piotroski F-Score
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  Fundamental scoring
                </p>
              </div>
            </button>
            <button
              type="button"
              onClick={() => setJobType("recommendations")}
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "recommendations"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-emerald-100 text-emerald-700
                  dark:bg-emerald-500/15
                  dark:text-emerald-400"
              >
                <StarIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Recommendations
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  LLM portfolio picks
                </p>
              </div>
            </button>
            <button
              type="button"
              onClick={() =>
                setJobType("recommendation_outcomes")
              }
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "recommendation_outcomes"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-sky-100 text-sky-700
                  dark:bg-sky-500/15
                  dark:text-sky-400"
              >
                <CheckCircleIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Outcome Tracker
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  30/60/90d checkpoints
                </p>
              </div>
            </button>
            <button
              type="button"
              onClick={() =>
                setJobType("iceberg_maintenance")
              }
              className={`flex items-center gap-2.5
                rounded-xl border p-3 text-left
                transition-all ${
                  jobType === "iceberg_maintenance"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/12"
                    : "border-gray-200 dark:border-gray-700"
                }`}
            >
              <div
                className="flex h-9 w-9 items-center
                  justify-center rounded-[10px]
                  bg-amber-100 text-amber-700
                  dark:bg-amber-500/15
                  dark:text-amber-400"
              >
                <ZapIcon />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                  Iceberg Maintenance
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500">
                  Compact + orphan sweep
                </p>
              </div>
            </button>
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

        {/* Force toggle */}
        <div className="flex items-center justify-between">
          <div>
            <label
              className="block text-[11px]
                font-semibold uppercase tracking-wide
                text-gray-400 dark:text-gray-500"
            >
              Force (skip cache)
            </label>
            <p className="text-[10px] text-gray-400 dark:text-gray-500">
              Skip freshness checks and CV cache
            </p>
          </div>
          <Toggle
            checked={force}
            onChange={setForce}
          />
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
  const [filterStatus, setFilterStatus] = useState("");
  const [filterJobType, setFilterJobType] = useState("");
  const [filterDays, setFilterDays] = useState(7);
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const {
    runs,
    total,
    mutate,
  } = useSchedulerRunsFiltered({
    status: filterStatus || undefined,
    job_type: filterJobType || undefined,
    days: filterDays,
    offset: page * pageSize,
    limit: pageSize,
  });

  const handleCancel = useCallback(
    async (runId: string) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/runs/${runId}/cancel`,
        { method: "POST" },
      );
      mutate();
    },
    [mutate],
  );

  // Compute metrics from visible runs
  const successCount = runs.filter(
    (r: SchedulerRun) => r.status === "success",
  ).length;
  const avgDuration =
    runs.length > 0
      ? runs.reduce(
          (sum: number, r: SchedulerRun) =>
            sum + (r.duration_secs ?? 0),
          0,
        ) / runs.length
      : 0;

  const dotColor: Record<string, string> = {
    success: "bg-emerald-500",
    running: "bg-indigo-500 animate-pulse",
    failed: "bg-red-500",
    cancelled: "bg-orange-500",
    skipped: "bg-zinc-400",
  };

  // Group pipeline runs, keep standalone runs flat
  type RunEntry =
    | { type: "solo"; run: SchedulerRun }
    | {
        type: "pipeline";
        id: string;
        steps: SchedulerRun[];
        started_at: string;
      };

  const grouped: RunEntry[] = [];
  const pipelineMap = new Map<string, SchedulerRun[]>();
  const soloRuns: SchedulerRun[] = [];

  for (const r of runs) {
    if (r.pipeline_run_id) {
      const list = pipelineMap.get(r.pipeline_run_id) ?? [];
      list.push(r);
      pipelineMap.set(r.pipeline_run_id, list);
    } else {
      soloRuns.push(r);
    }
  }

  // Build ordered list: insert pipeline groups at
  // the position of their earliest run
  const allEntries: {
    sortKey: string;
    entry: RunEntry;
  }[] = [];
  for (const [pid, steps] of pipelineMap) {
    steps.sort(
      (a, b) =>
        new Date(a.started_at).getTime() -
        new Date(b.started_at).getTime(),
    );
    allEntries.push({
      sortKey: steps[0].started_at,
      entry: {
        type: "pipeline",
        id: pid,
        steps,
        started_at: steps[0].started_at,
      },
    });
  }
  for (const r of soloRuns) {
    allEntries.push({
      sortKey: r.started_at,
      entry: { type: "solo", run: r },
    });
  }
  allEntries.sort(
    (a, b) =>
      new Date(b.sortKey).getTime() -
      new Date(a.sortKey).getTime(),
  );
  for (const { entry } of allEntries) grouped.push(entry);

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
        <div className="flex items-center justify-between">
          <h3 className="text-[15px] font-bold">
            Run History
          </h3>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="text-zinc-500 dark:text-zinc-400">
              Total: {total}
            </span>
            {total > 0 && (
              <>
                <span className="text-emerald-600 dark:text-emerald-400">
                  {Math.round(
                    (successCount / Math.max(runs.length, 1)) * 100,
                  )}% success
                </span>
                <span className="text-zinc-500 dark:text-zinc-400">
                  Avg: {fmtDuration(avgDuration)}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Filters */}
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <select
            value={filterStatus}
            onChange={(e) => {
              setFilterStatus(e.target.value);
              setPage(0);
            }}
            className="rounded-md border border-gray-200
              bg-white px-2 py-1 text-[11px]
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-300"
          >
            <option value="">All statuses</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="running">Running</option>
            <option value="cancelled">Cancelled</option>
            <option value="skipped">Skipped</option>
          </select>
          <select
            value={filterJobType}
            onChange={(e) => {
              setFilterJobType(e.target.value);
              setPage(0);
            }}
            className="rounded-md border border-gray-200
              bg-white px-2 py-1 text-[11px]
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-300"
          >
            <option value="">All job types</option>
            <option value="data_refresh">
              Data Refresh
            </option>
            <option value="compute_analytics">
              Compute Analytics
            </option>
            <option value="run_sentiment">
              Sentiment
            </option>
            <option value="run_piotroski">
              Piotroski F-Score
            </option>
            <option value="run_forecasts">
              Forecasts
            </option>
            <option value="recommendations">
              Recommendations
            </option>
            <option value="recommendation_outcomes">
              Outcome Tracker
            </option>
            <option value="iceberg_maintenance">
              Iceberg Maintenance
            </option>
          </select>
          <select
            value={filterDays}
            onChange={(e) => {
              setFilterDays(Number(e.target.value));
              setPage(0);
            }}
            className="rounded-md border border-gray-200
              bg-white px-2 py-1 text-[11px]
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-300"
          >
            <option value={1}>Last 24h</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
        </div>
      </div>

      {grouped.length === 0 ? (
        <div className="p-8 text-center text-sm text-gray-400 dark:text-gray-500">
          No runs match the selected filters.
        </div>
      ) : (
        <div className="px-5 py-5 space-y-3">
          {grouped.map((entry) =>
            entry.type === "pipeline" ? (
              <PipelineRunGroup
                key={entry.id}
                steps={entry.steps}
                started_at={entry.started_at}
                dotColor={dotColor}
                onCancel={handleCancel}
              />
            ) : (
              <SoloRunRow
                key={entry.run.run_id}
                run={entry.run}
                dotColor={dotColor}
                onCancel={handleCancel}
              />
            ),
          )}

          {/* Pagination */}
          {total > pageSize && (
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="rounded-md border border-gray-200
                  px-3 py-1 text-[11px] font-medium
                  disabled:opacity-40
                  dark:border-gray-700 dark:text-gray-300
                  hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                Previous
              </button>
              <span className="text-[11px] text-gray-500 dark:text-gray-400">
                {page * pageSize + 1}&ndash;
                {Math.min(
                  (page + 1) * pageSize,
                  total,
                )}{" "}
                of {total}
              </span>
              <button
                disabled={
                  (page + 1) * pageSize >= total
                }
                onClick={() => setPage((p) => p + 1)}
                className="rounded-md border border-gray-200
                  px-3 py-1 text-[11px] font-medium
                  disabled:opacity-40
                  dark:border-gray-700 dark:text-gray-300
                  hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                Next
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Pipeline Run Group (collapsible)
// ---------------------------------------------------------------

function PipelineRunGroup({
  steps,
  started_at,
  dotColor,
  onCancel,
}: {
  steps: SchedulerRun[];
  started_at: string;
  dotColor: Record<string, string>;
  onCancel: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);

  const allSuccess = steps.every(
    (s) => s.status === "success",
  );
  const anyFailed = steps.some(
    (s) => s.status === "failed",
  );
  const anyRunning = steps.some(
    (s) => s.status === "running",
  );
  const overallStatus = anyRunning
    ? "running"
    : anyFailed
      ? "failed"
      : allSuccess
        ? "success"
        : "pending";

  const totalDur = steps.reduce(
    (s, r) => s + (r.duration_secs ?? 0),
    0,
  );
  const scope = steps[0]?.scope ?? "";

  const scopeBadge: Record<string, string> = {
    india:
      "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-400",
    us: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  };

  return (
    <div
      className="rounded-xl border border-gray-200
        dark:border-gray-700 overflow-hidden"
    >
      {/* Header — click to toggle */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4
          py-3 text-left bg-gray-50 dark:bg-gray-800/50
          hover:bg-gray-100 dark:hover:bg-gray-800
          transition-colors"
      >
        <div
          className={`h-2.5 w-2.5 rounded-full shrink-0
            ${dotColor[overallStatus] ?? "bg-gray-400"}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-gray-900 dark:text-gray-100">
              Pipeline Run
            </span>
            {scope && scopeBadge[scope] && (
              <span
                className={`rounded-full px-1.5 py-0.5
                  text-[9px] font-bold ${scopeBadge[scope]}`}
              >
                {scope.toUpperCase()}
              </span>
            )}
            <StatusBadge status={overallStatus} />
            <span className="font-mono text-[11px] text-gray-400 dark:text-gray-500">
              {fmtDuration(totalDur)}
            </span>
          </div>
          <p className="font-mono text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
            {fmtTimestamp(started_at)}
            {" \u00B7 "}
            {steps.length} steps
          </p>
        </div>
        <svg
          viewBox="0 0 24 24"
          className={`h-4 w-4 text-gray-400
            transition-transform duration-200
            ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {/* Steps */}
      {open && (
        <div className="px-4 py-3 space-y-2.5 border-t
          border-gray-100 dark:border-gray-700/50"
        >
          {steps.map((r, i) => (
            <div
              key={r.run_id}
              className="flex items-start gap-3"
            >
              {/* Step connector */}
              <div className="flex flex-col items-center pt-1">
                <div
                  className={`h-2.5 w-2.5 rounded-full
                    ${dotColor[r.status] ?? "bg-gray-300"}`}
                />
                {i < steps.length - 1 && (
                  <div
                    className="w-0.5 flex-1 mt-1
                      bg-gray-200 dark:bg-gray-700
                      min-h-[20px]"
                  />
                )}
              </div>
              <div className="flex-1 min-w-0 pb-1">
                <div className="flex items-center gap-2">
                  <span
                    className="text-[10px] font-bold
                      text-gray-400 dark:text-gray-500"
                  >
                    {i + 1}
                  </span>
                  <span className="text-[12px] font-semibold text-gray-800 dark:text-gray-200">
                    {r.job_name}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2">
                  <StatusBadge status={r.status} />
                  <span className="font-mono text-[10px] text-gray-400">
                    {fmtDuration(r.duration_secs)}
                  </span>
                  <span className="font-mono text-[10px] text-gray-400">
                    {r.tickers_done}/{r.tickers_total}{" "}
                    {progressUnit(
                      r.job_type,
                      r.tickers_total,
                    )}
                  </span>
                  {r.status === "running" && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onCancel(r.run_id);
                      }}
                      className="rounded-full bg-red-100
                        px-2 py-0.5 text-[9px]
                        font-semibold text-red-700
                        dark:bg-red-500/15
                        dark:text-red-400"
                    >
                      Stop
                    </button>
                  )}
                </div>
                {r.error_message && (
                  <p
                    className="mt-0.5 text-[10px]
                      text-orange-600 dark:text-orange-400
                      line-clamp-1"
                    title={r.error_message}
                  >
                    {r.error_message}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Solo run row (non-pipeline)
// ---------------------------------------------------------------

function SoloRunRow({
  run: r,
  dotColor,
  onCancel,
}: {
  run: SchedulerRun;
  dotColor: Record<string, string>;
  onCancel: (id: string) => void;
}) {
  return (
    <div className="flex items-start gap-3 px-2 py-2">
      <div
        className={`mt-1 h-3 w-3 rounded-full shrink-0
          ${dotColor[r.status] ?? "bg-gray-400"}`}
      />
      <div className="flex-1 min-w-0">
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
            {progressUnit(
              r.job_type,
              r.tickers_total,
            )}
          </span>
          {r.status === "running" && (
            <button
              onClick={() => onCancel(r.run_id)}
              className="ml-1 rounded-full bg-red-100
                px-2.5 py-0.5 text-[10px] font-semibold
                text-red-700 hover:bg-red-200
                dark:bg-red-500/15 dark:text-red-400
                dark:hover:bg-red-500/25"
            >
              Stop
            </button>
          )}
        </div>
        {r.error_message && (
          <p
            className="mt-1 line-clamp-2 text-[10px]
              text-orange-600 dark:text-orange-400"
            title={r.error_message}
          >
            {r.error_message}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Main SchedulerTab
// ---------------------------------------------------------------

export function SchedulerTab() {
  const { mutate: mutateJobs } = useSchedulerJobs();
  const { runs, mutate: mutateRuns } =
    useSchedulerRuns();
  const { mutate: mutateStats } = useSchedulerStats();
  const {
    pipelines,
    mutate: mutatePipelines,
  } = useSchedulerPipelines();
  const [showForm, setShowForm] = useState(true);
  const [editingJob, setEditingJob] =
    useState<SchedulerJob | null>(null);
  const [showPipelineForm, setShowPipelineForm] =
    useState(false);
  const [editingPipeline, setEditingPipeline] =
    useState<Pipeline | null>(null);

  const refreshAll = useCallback(() => {
    mutateJobs();
    mutateRuns();
    mutateStats();
    mutatePipelines();
  }, [mutateJobs, mutateRuns, mutateStats, mutatePipelines]);

  // Auto-refresh every 30s for live run tracking
  useEffect(() => {
    const interval = setInterval(refreshAll, 30_000);
    return () => clearInterval(interval);
  }, [refreshAll]);

  return (
    <div className="space-y-5">
      {/* Stat cards */}
      <StatCards />

      {/* Pipeline DAG */}
      <PipelineDAG
        pipelines={pipelines}
        mutatePipelines={mutatePipelines}
        onEdit={(p) => {
          setEditingPipeline(p);
          setShowPipelineForm(true);
        }}
        onNewPipeline={() => {
          setEditingPipeline(null);
          setShowPipelineForm(true);
        }}
      />

      {/* Pipeline Form */}
      {showPipelineForm && (
        <PipelineForm
          editingPipeline={editingPipeline}
          onCreated={() => {
            setShowPipelineForm(false);
            setEditingPipeline(null);
            mutatePipelines();
          }}
          onCancel={() => {
            setShowPipelineForm(false);
            setEditingPipeline(null);
          }}
        />
      )}

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
          runs={runs}
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
