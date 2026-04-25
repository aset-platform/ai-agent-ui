"use client";

import { useState, useCallback, useRef } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Pipeline, PipelineStep } from "@/hooks/useSchedulerData";

// ---------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------

const STATUS_COLORS: Record<
  string,
  { bg: string; dot: string; border: string }
> = {
  success: {
    bg: "bg-emerald-50 dark:bg-emerald-500/10",
    dot: "bg-emerald-500",
    border: "border-emerald-300 dark:border-emerald-500/30",
  },
  running: {
    bg: "bg-blue-50 dark:bg-blue-500/10",
    dot: "bg-blue-500 animate-pulse",
    border: "border-blue-300 dark:border-blue-500/30",
  },
  failed: {
    bg: "bg-red-50 dark:bg-red-500/10",
    dot: "bg-red-500",
    border: "border-red-300 dark:border-red-500/30",
  },
  skipped: {
    bg: "bg-orange-50 dark:bg-orange-500/10",
    dot: "bg-orange-400",
    border: "border-orange-300 dark:border-orange-500/30",
  },
  pending: {
    bg: "bg-zinc-50 dark:bg-zinc-800/50",
    dot: "bg-zinc-300 dark:bg-zinc-600",
    border: "border-zinc-200 dark:border-zinc-700",
  },
};

function getStatusStyle(status: string | null) {
  return STATUS_COLORS[status ?? "pending"] ?? STATUS_COLORS.pending;
}

function fmtDur(secs: number | null): string {
  if (secs == null) return "";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

const SCOPE_BADGES: Record<string, string> = {
  india:
    "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-400",
  us: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  all: "bg-zinc-100 text-zinc-700 dark:bg-zinc-500/15 dark:text-zinc-400",
};

const JOB_LABELS: Record<string, string> = {
  data_refresh: "Data Refresh",
  compute_analytics: "Compute Analytics",
  run_sentiment: "Sentiment Scoring",
  run_piotroski: "Piotroski F-Score",
  run_forecasts: "Forecasts",
  recommendations: "Recommendations",
  recommendation_outcomes: "Outcome Tracker",
  iceberg_maintenance: "Iceberg Maintenance",
};

// ---------------------------------------------------------------
// Arrow SVG between nodes
// ---------------------------------------------------------------

function Arrow() {
  return (
    <div className="flex items-center px-1 shrink-0">
      <svg width="32" height="20" viewBox="0 0 32 20">
        <line
          x1="0"
          y1="10"
          x2="24"
          y2="10"
          className="stroke-zinc-400 dark:stroke-zinc-500"
          strokeWidth="2"
        />
        <polygon
          points="22,5 32,10 22,15"
          className="fill-zinc-400 dark:fill-zinc-500"
        />
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------
// Step Node
// ---------------------------------------------------------------

function StepNode({
  step,
  pipelineId,
  onResume,
}: {
  step: PipelineStep;
  pipelineId: string;
  onResume: (pipelineId: string, fromStep: number) => void;
}) {
  const [showMenu, setShowMenu] = useState(false);
  const [menuPos, setMenuPos] = useState({ top: 0, left: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const style = getStatusStyle(step.last_status);
  const statusLabel = step.last_status
    ? step.last_status.charAt(0).toUpperCase() + step.last_status.slice(1)
    : "Not run";

  const handleClick = () => {
    if (!showMenu && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      setMenuPos({
        top: rect.top - 4,
        left: rect.left,
      });
    }
    setShowMenu(!showMenu);
  };

  return (
    <div className="relative">
      <button
        ref={btnRef}
        type="button"
        onClick={handleClick}
        className={`
          relative flex flex-col items-start gap-1
          rounded-lg border px-4 py-3 min-w-[160px]
          transition-all duration-150
          hover:shadow-md cursor-pointer
          ${style.bg} ${style.border}
        `}
      >
        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
          Step {step.step_order}
        </span>
        <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          {JOB_LABELS[step.job_type] ?? step.job_type}
        </span>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className={`h-2 w-2 rounded-full ${style.dot}`} />
          <span className="text-[11px] text-zinc-600 dark:text-zinc-400">
            {statusLabel}
          </span>
          {step.last_duration != null && (
            <span className="text-[10px] text-zinc-400 dark:text-zinc-500 ml-1">
              {fmtDur(step.last_duration)}
            </span>
          )}
        </div>
        {step.error_message && step.last_status === "failed" && (
          <span
            className="text-[10px] text-red-600 dark:text-red-400 mt-0.5 max-w-[180px] truncate"
            title={step.error_message}
          >
            {step.error_message}
          </span>
        )}
      </button>

      {showMenu && (
        <>
          <div
            className="fixed inset-0 z-[60]"
            onClick={() => setShowMenu(false)}
          />
          <div
            className="fixed z-[70] bg-white dark:bg-zinc-800
              border border-zinc-200 dark:border-zinc-700
              rounded-xl shadow-xl py-1.5 min-w-[160px]
              animate-in fade-in slide-in-from-bottom-2
              duration-150"
            style={{
              top: menuPos.top,
              left: menuPos.left,
              transform: "translateY(-100%)",
            }}
          >
            <button
              type="button"
              className="flex items-center gap-2 w-full text-left
                px-3.5 py-2 text-[13px] font-medium
                hover:bg-indigo-50 dark:hover:bg-indigo-500/10
                text-zinc-700 dark:text-zinc-200
                transition-colors rounded-lg mx-0.5"
              style={{ width: "calc(100% - 4px)" }}
              onClick={() => {
                setShowMenu(false);
                onResume(pipelineId, step.step_order);
              }}
            >
              <svg
                viewBox="0 0 24 24"
                className="h-3.5 w-3.5 text-indigo-500"
                fill="currentColor"
              >
                <polygon points="5,3 19,12 5,21" />
              </svg>
              Run from here
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Pipeline Row
// ---------------------------------------------------------------

function PipelineRow({
  pipeline,
  onTrigger,
  onResume,
  onToggle,
  onEdit,
  onDelete,
}: {
  pipeline: Pipeline;
  onTrigger: (id: string, force?: boolean) => void;
  onResume: (id: string, step: number) => void;
  onToggle: (id: string, enabled: boolean) => void;
  onEdit?: (pipeline: Pipeline) => void;
  onDelete?: (id: string) => void;
}) {
  const [runMenuOpen, setRunMenuOpen] = useState(false);
  const scopeClass = SCOPE_BADGES[pipeline.scope] ?? SCOPE_BADGES.all;

  return (
    <div
      className="rounded-xl border border-zinc-200 dark:border-zinc-700
        bg-white dark:bg-zinc-900 p-4 space-y-3 overflow-visible"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {pipeline.name}
          </h3>
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${scopeClass}`}
          >
            {pipeline.scope.toUpperCase()}
          </span>
          {pipeline.is_running && (
            <span className="inline-flex items-center gap-1 text-[10px] text-blue-600 dark:text-blue-400">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
              Running
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {pipeline.next_run && (
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              Next: {pipeline.next_run}
            </span>
          )}
          <button
            type="button"
            onClick={() => onToggle(pipeline.pipeline_id, !pipeline.enabled)}
            className={`
              relative inline-flex h-5 w-9 shrink-0
              cursor-pointer rounded-full
              transition-colors duration-200
              ${pipeline.enabled ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}
            `}
          >
            <span
              className={`
                pointer-events-none inline-block h-4 w-4
                rounded-full bg-white shadow-sm
                transition-transform duration-200
                mt-0.5
                ${pipeline.enabled ? "translate-x-[18px]" : "translate-x-0.5"}
              `}
            />
          </button>
          {onEdit && (
            <button
              type="button"
              onClick={() => onEdit(pipeline)}
              className="flex h-[26px] w-[26px] items-center
                justify-center rounded-md border
                border-zinc-200 text-zinc-400
                transition-all hover:border-indigo-400
                hover:bg-indigo-50 hover:text-indigo-600
                dark:border-zinc-700 dark:text-zinc-500
                dark:hover:border-indigo-500
                dark:hover:bg-indigo-500/10
                dark:hover:text-indigo-400"
              title="Edit pipeline"
            >
              <svg viewBox="0 0 24 24" className="h-3 w-3"
                fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={() => onDelete(pipeline.pipeline_id)}
              className="flex h-[26px] w-[26px] items-center
                justify-center rounded-md border
                border-zinc-200 text-zinc-400
                transition-all hover:border-red-400
                hover:bg-red-50 hover:text-red-600
                dark:border-zinc-700 dark:text-zinc-500
                dark:hover:border-red-500
                dark:hover:bg-red-500/10
                dark:hover:text-red-400"
              title="Delete pipeline"
            >
              <svg viewBox="0 0 24 24" className="h-3 w-3"
                fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          )}
          <div className="relative">
            <div className="flex items-stretch">
              <button
                type="button"
                disabled={pipeline.is_running}
                onClick={() => onTrigger(pipeline.pipeline_id, false)}
                className="inline-flex items-center gap-1 rounded-l-md
                  bg-indigo-600 hover:bg-indigo-700
                  disabled:opacity-50 disabled:cursor-not-allowed
                  px-2.5 py-1 text-[11px] font-medium text-white
                  transition-colors"
              >
                <svg viewBox="0 0 24 24" className="h-3 w-3" fill="currentColor">
                  <polygon points="5,3 19,12 5,21" />
                </svg>
                Run All
              </button>
              <button
                type="button"
                disabled={pipeline.is_running}
                onClick={() => setRunMenuOpen(!runMenuOpen)}
                className="inline-flex items-center rounded-r-md
                  border-l border-indigo-500
                  bg-indigo-600 hover:bg-indigo-700
                  disabled:opacity-50 disabled:cursor-not-allowed
                  px-1 py-1 text-white transition-colors"
              >
                <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none"
                  stroke="currentColor" strokeWidth="2.5">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>
            </div>
            {runMenuOpen && (
              <>
                <div
                  className="fixed inset-0 z-[60]"
                  onClick={() => setRunMenuOpen(false)}
                />
                <div
                  className="absolute right-0 top-full z-[70] mt-1
                    bg-white dark:bg-zinc-800
                    border border-zinc-200 dark:border-zinc-700
                    rounded-lg shadow-xl py-1 min-w-[140px]"
                >
                  <button
                    type="button"
                    onClick={() => {
                      setRunMenuOpen(false);
                      onTrigger(pipeline.pipeline_id, true);
                    }}
                    className="flex items-center gap-2 w-full
                      text-left px-3 py-1.5 text-[11px]
                      font-semibold text-amber-600
                      dark:text-amber-400
                      hover:bg-amber-50
                      dark:hover:bg-amber-500/10
                      transition-colors"
                  >
                    <svg viewBox="0 0 24 24" className="h-3 w-3"
                      fill="none" stroke="currentColor" strokeWidth="2"
                      strokeLinecap="round" strokeLinejoin="round">
                      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                    </svg>
                    Force Run All
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* DAG flow */}
      <div className="flex items-center gap-0 overflow-x-auto overflow-y-visible py-1">
        {pipeline.steps.map((step, i) => (
          <div key={step.step_order} className="flex items-center">
            {i > 0 && <Arrow />}
            <StepNode
              step={step}
              pipelineId={pipeline.pipeline_id}
              onResume={onResume}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Main component
// ---------------------------------------------------------------

export default function PipelineDAG({
  pipelines,
  mutatePipelines,
  onEdit,
  onNewPipeline,
}: {
  pipelines: Pipeline[];
  mutatePipelines: () => void;
  onEdit?: (pipeline: Pipeline) => void;
  onNewPipeline?: () => void;
}) {
  const [deletePipelineId, setDeletePipelineId] =
    useState<string | null>(null);

  const handleTrigger = useCallback(
    async (id: string, force = false) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/pipelines/${id}/trigger`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force }),
        },
      );
      mutatePipelines();
    },
    [mutatePipelines],
  );

  const confirmDeletePipeline = useCallback(
    async () => {
      if (!deletePipelineId) return;
      await apiFetch(
        `${API_URL}/admin/scheduler/pipelines/${deletePipelineId}`,
        { method: "DELETE" },
      );
      setDeletePipelineId(null);
      mutatePipelines();
    },
    [deletePipelineId, mutatePipelines],
  );

  const handleResume = useCallback(
    async (id: string, fromStep: number) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/pipelines/${id}/resume`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from_step: fromStep }),
        },
      );
      mutatePipelines();
    },
    [mutatePipelines],
  );

  const handleToggle = useCallback(
    async (id: string, enabled: boolean) => {
      await apiFetch(
        `${API_URL}/admin/scheduler/pipelines/${id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        },
      );
      mutatePipelines();
    },
    [mutatePipelines],
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 uppercase tracking-wider">
          Pipelines
        </h2>
        {onNewPipeline && (
          <button
            onClick={onNewPipeline}
            className="flex items-center gap-1.5 rounded-[10px]
              border border-indigo-600 px-3 py-[6px]
              text-[11px] font-semibold text-indigo-600
              transition-all hover:bg-indigo-600
              hover:text-white
              dark:border-indigo-500
              dark:text-indigo-400
              dark:hover:bg-indigo-500
              dark:hover:text-white"
          >
            <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none"
              stroke="currentColor" strokeWidth="2.5"
              strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New Pipeline
          </button>
        )}
      </div>
      {pipelines.length === 0 ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-700
          bg-white dark:bg-zinc-900 p-8 text-center text-sm
          text-gray-400 dark:text-gray-500">
          No pipelines yet. Create one to chain jobs together.
        </div>
      ) : (
        pipelines.map((p) => (
          <PipelineRow
            key={p.pipeline_id}
            pipeline={p}
            onTrigger={handleTrigger}
            onResume={handleResume}
            onToggle={handleToggle}
            onEdit={onEdit}
            onDelete={setDeletePipelineId}
          />
        ))
      )}

      <ConfirmDialog
        open={deletePipelineId !== null}
        title="Delete Pipeline"
        message={
          `Are you sure you want to delete "${
            pipelines.find(
              (p) => p.pipeline_id === deletePipelineId,
            )?.name ?? deletePipelineId
          }"? This action cannot be undone.`
        }
        confirmLabel="Delete"
        variant="danger"
        onConfirm={confirmDeletePipeline}
        onCancel={() => setDeletePipelineId(null)}
      />
    </div>
  );
}
