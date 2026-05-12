"use client";
/**
 * KitePostbackPanel — OBS-4.
 *
 * Shows the last 50 Kite postback events for the authenticated user.
 * Mounts only in the Live segment of PaperTab (hidden in Paper /
 * Dry-run — postbacks require real Kite orders).
 *
 * UX pattern: mirrors ReconciliationDriftPanel (table + per-row
 * JSON expand + amber empty state with troubleshooting hint).
 */

import { useState } from "react";

import { formatIstDateTime, formatIstTime } from "@/lib/datetime";
import {
  useKitePostbacks,
  type KitePostback,
} from "@/hooks/useKitePostbacks";
import {
  useOrderSubmissions,
  type OrderSubmission,
} from "@/hooks/useOrderSubmissions";

// ── Status badge ────────────────────────────────────────────

type PostbackStatus = "COMPLETE" | "REJECTED" | "CANCELLED" | "UPDATE";

function StatusBadge({ status }: { status: string }) {
  const s = status.toUpperCase() as PostbackStatus;
  const cls =
    s === "COMPLETE"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
      : s === "REJECTED"
        ? "bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-300"
        : s === "CANCELLED"
          ? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
          : /* UPDATE */
            "bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300";

  return (
    <span
      className={`inline-flex items-center rounded-full px-1.5 py-0.5
        text-[10px] font-medium ${cls}`}
    >
      {s}
    </span>
  );
}

// IST formatting via shared helper (ASETPLTFRM-373).
const fmtTime = (iso: string) => formatIstTime(iso);
const fmtAbsolute = (iso: string) => formatIstDateTime(iso);

// ── Sub-components ───────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div
      className="space-y-1"
      aria-busy="true"
      aria-label="Loading postbacks"
    >
      {/* Text node so Lighthouse FCP fires — per CLAUDE.md §5.3. */}
      <p className="text-xs text-slate-500">Loading postbacks…</p>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-8 animate-pulse rounded bg-slate-100
            dark:bg-slate-800"
        />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50
        p-3 text-xs text-amber-800 dark:border-amber-700
        dark:bg-amber-950/20 dark:text-amber-300"
      data-testid="kite-postback-empty-state"
    >
      No postbacks received. Either no live orders placed today,
      postbacks not yet enabled (
      <code className="rounded bg-amber-100 px-1 dark:bg-amber-900/40">
        KITE_POSTBACK_ENABLED
      </code>
      ), or ngrok tunnel down — check{" "}
      <a
        href="http://localhost:4040"
        target="_blank"
        rel="noreferrer"
        className="underline"
      >
        http://localhost:4040
      </a>
      .
    </div>
  );
}

interface PostbackRowProps {
  postback: KitePostback;
  expanded: boolean;
  onToggle: () => void;
}

function PostbackRow({ postback, expanded, onToggle }: PostbackRowProps) {
  return (
    <>
      <tr
        className="border-b border-slate-100 hover:bg-slate-50
          dark:border-slate-800 dark:hover:bg-slate-800/50"
        data-testid="kite-postback-row"
      >
        <td
          className="px-3 py-1.5 text-[11px] text-slate-500"
          title={fmtAbsolute(postback.event_ts)}
        >
          {fmtTime(postback.event_ts)}
        </td>
        <td
          className="px-3 py-1.5 font-mono text-xs font-semibold
            text-slate-900 dark:text-slate-100"
        >
          {postback.tradingsymbol}
        </td>
        <td className="px-3 py-1.5">
          <StatusBadge status={postback.status} />
        </td>
        <td
          className="px-3 py-1.5 text-right text-xs text-slate-700
            dark:text-slate-300"
        >
          {postback.filled_quantity}
        </td>
        <td
          className="px-3 py-1.5 text-right text-xs text-slate-700
            dark:text-slate-300"
        >
          {postback.average_price > 0
            ? `₹${postback.average_price.toFixed(2)}`
            : "—"}
        </td>
        <td className="px-3 py-1.5 text-center">
          <button
            type="button"
            onClick={onToggle}
            className="text-[10px] text-slate-400 hover:text-slate-700
              dark:hover:text-slate-200"
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse payload" : "Expand payload"}
            data-testid="kite-postback-payload-toggle"
          >
            {expanded ? "▾" : "▸"}
          </button>
        </td>
      </tr>

      {expanded && (
        <tr className="bg-slate-50 dark:bg-slate-900/60">
          <td
            colSpan={6}
            className="px-3 py-2"
          >
            <pre
              className="overflow-x-auto whitespace-pre-wrap break-all
                rounded border border-slate-200 bg-white p-2
                text-[10px] leading-relaxed text-slate-700
                dark:border-slate-700 dark:bg-slate-900
                dark:text-slate-300"
            >
              {JSON.stringify(postback.raw, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Submissions tab sub-components ──────────────────────────

function SubmissionsEmptyState() {
  return (
    <div
      className="rounded-md border border-slate-300 bg-slate-50
        p-3 text-xs text-slate-600 dark:border-slate-700
        dark:bg-slate-900/40 dark:text-slate-300"
      data-testid="order-submissions-empty-state"
    >
      No submissions yet. Live or dry-run orders placed via the
      runtime will appear here with their full request payload.
    </div>
  );
}

function SubmissionsLoadingSkeleton() {
  return (
    <div
      className="space-y-1"
      aria-busy="true"
      aria-label="Loading order submissions"
    >
      <p className="text-xs text-slate-500">Loading submissions…</p>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-8 animate-pulse rounded bg-slate-100
            dark:bg-slate-800"
        />
      ))}
    </div>
  );
}

interface SubmissionRowProps {
  submission: OrderSubmission;
  expanded: boolean;
  onToggle: () => void;
}

function SideBadge({ side }: { side: string }) {
  const s = side.toUpperCase();
  const cls =
    s === "BUY"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
      : "bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-300";
  return (
    <span
      className={`inline-flex items-center rounded-full px-1.5 py-0.5
        text-[10px] font-medium ${cls}`}
    >
      {s}
    </span>
  );
}

function SubmissionRow({
  submission,
  expanded,
  onToggle,
}: SubmissionRowProps) {
  return (
    <>
      <tr
        className="border-b border-slate-100 hover:bg-slate-50
          dark:border-slate-800 dark:hover:bg-slate-800/50"
        data-testid="order-submission-row"
      >
        <td
          className="px-3 py-1.5 text-[11px] text-slate-500"
          title={fmtAbsolute(submission.event_ts)}
        >
          {fmtTime(submission.event_ts)}
        </td>
        <td
          className="px-3 py-1.5 font-mono text-xs font-semibold
            text-slate-900 dark:text-slate-100"
        >
          {submission.symbol}
        </td>
        <td className="px-3 py-1.5">
          <SideBadge side={submission.side} />
        </td>
        <td
          className="px-3 py-1.5 text-right text-xs text-slate-700
            dark:text-slate-300"
        >
          {submission.qty}
        </td>
        <td
          className="px-3 py-1.5 font-mono text-[11px] text-slate-500
            dark:text-slate-400"
          title={submission.kite_order_id}
        >
          {submission.kite_order_id.slice(0, 12)}
          {submission.dry_run && (
            <span
              className="ml-1 rounded-full bg-amber-100 px-1.5
                py-0.5 text-[9px] font-medium text-amber-800
                dark:bg-amber-950/50 dark:text-amber-300"
            >
              dry
            </span>
          )}
        </td>
        <td className="px-3 py-1.5 text-center">
          <button
            type="button"
            onClick={onToggle}
            className="text-[10px] text-slate-400 hover:text-slate-700
              dark:hover:text-slate-200"
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse payload" : "Expand payload"}
            data-testid="order-submission-payload-toggle"
          >
            {expanded ? "▾" : "▸"}
          </button>
        </td>
      </tr>

      {expanded && (
        <tr className="bg-slate-50 dark:bg-slate-900/60">
          <td
            colSpan={6}
            className="px-3 py-2"
          >
            <pre
              className="overflow-x-auto whitespace-pre-wrap break-all
                rounded border border-slate-200 bg-white p-2
                text-[10px] leading-relaxed text-slate-700
                dark:border-slate-700 dark:bg-slate-900
                dark:text-slate-300"
            >
              {JSON.stringify(submission.raw, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

function SubmissionsTab() {
  const { submissions, isLoading, error } = useOrderSubmissions();
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const handleToggle = (idx: number) => {
    setExpandedIdx((prev) => (prev === idx ? null : idx));
  };

  if (isLoading && submissions.length === 0) {
    return <SubmissionsLoadingSkeleton />;
  }
  if (error) {
    return (
      <p className="text-xs text-rose-600 dark:text-rose-400">
        {error}
      </p>
    );
  }
  if (submissions.length === 0) {
    return <SubmissionsEmptyState />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead>
          <tr
            className="border-b border-slate-100
              dark:border-slate-800"
          >
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Time (IST)
            </th>
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Symbol
            </th>
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Side
            </th>
            <th
              className="px-3 py-1.5 text-right text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Qty
            </th>
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Kite order id
            </th>
            <th
              className="px-3 py-1.5 text-center text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Raw
            </th>
          </tr>
        </thead>
        <tbody>
          {submissions.map((sub, idx) => (
            <SubmissionRow
              key={`${sub.event_ts}-${sub.kite_order_id || idx}`}
              submission={sub}
              expanded={expandedIdx === idx}
              onToggle={() => handleToggle(idx)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Postbacks tab (extracted from original main body) ───────

function PostbacksTab() {
  const { postbacks, isLoading, error } = useKitePostbacks();
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const handleToggle = (idx: number) => {
    setExpandedIdx((prev) => (prev === idx ? null : idx));
  };

  if (isLoading && postbacks.length === 0) {
    return <LoadingSkeleton />;
  }
  if (error) {
    return (
      <p className="text-xs text-rose-600 dark:text-rose-400">
        {error}
      </p>
    );
  }
  if (postbacks.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead>
          <tr
            className="border-b border-slate-100
              dark:border-slate-800"
          >
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Time (IST)
            </th>
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Symbol
            </th>
            <th
              className="px-3 py-1.5 text-left text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Status
            </th>
            <th
              className="px-3 py-1.5 text-right text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Filled qty
            </th>
            <th
              className="px-3 py-1.5 text-right text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Avg price
            </th>
            <th
              className="px-3 py-1.5 text-center text-[10px]
                font-medium uppercase tracking-wide text-slate-400"
            >
              Raw
            </th>
          </tr>
        </thead>
        <tbody>
          {postbacks.map((pb, idx) => (
            <PostbackRow
              key={`${pb.event_ts}-${pb.raw?.["order_id"] ?? idx}`}
              postback={pb}
              expanded={expandedIdx === idx}
              onToggle={() => handleToggle(idx)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────

type TabKey = "submissions" | "postbacks";

export function KitePostbackPanel() {
  const [activeTab, setActiveTab] = useState<TabKey>("submissions");
  const { postbacks } = useKitePostbacks();
  const { submissions } = useOrderSubmissions();

  const tabBtn = (key: TabKey, label: string, count: number) => {
    const active = activeTab === key;
    return (
      <button
        type="button"
        onClick={() => setActiveTab(key)}
        className={`px-3 py-1.5 text-[11px] font-semibold uppercase
          tracking-wide transition-colors
          ${
            active
              ? "border-b-2 border-emerald-500 text-slate-800 " +
                "dark:text-slate-100"
              : "text-slate-500 hover:text-slate-700 " +
                "dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        data-testid={`kite-postback-tab-${key}`}
        aria-selected={active}
        role="tab"
      >
        {label}
        {count > 0 && (
          <span className="ml-1.5 text-slate-400">({count})</span>
        )}
      </button>
    );
  };

  return (
    <div
      className="rounded-md border border-slate-200
        dark:border-slate-700"
      data-testid="kite-postback-panel"
    >
      <div
        className="flex items-center gap-1 border-b border-slate-200
          px-2 dark:border-slate-700"
        role="tablist"
      >
        {tabBtn("submissions", "Submissions", submissions.length)}
        {tabBtn("postbacks", "Postbacks", postbacks.length)}
      </div>

      <div className="p-3">
        {activeTab === "submissions" && <SubmissionsTab />}
        {activeTab === "postbacks" && <PostbacksTab />}
      </div>
    </div>
  );
}
