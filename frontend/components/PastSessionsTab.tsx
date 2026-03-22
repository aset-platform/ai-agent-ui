"use client";
/**
 * Past Sessions tab — browsable history of chat sessions.
 *
 * Shows a filterable list of past chat sessions with
 * expand/collapse to view full message history read-only.
 */

import { useState, useEffect, useCallback } from "react";
import { usePastSessions } from "@/hooks/usePastSessions";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { MarkdownContent } from "@/components/MarkdownContent";
import type { ChatSessionDetail } from "@/lib/types";

// ── Per-message renderer with markdown + raw toggle ──

function SessionMessage({
  msg,
}: {
  msg: { role: string; content: string };
}) {
  const [showRaw, setShowRaw] = useState(false);
  const isAssistant = msg.role === "assistant";

  const noop = useCallback(() => {}, []);

  return (
    <div
      className={`flex ${
        isAssistant ? "justify-start" : "justify-end"
      }`}
    >
      <div
        className={`max-w-[85%] px-3 py-2 rounded-xl text-xs ${
          isAssistant
            ? "bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded-bl-sm"
            : "bg-indigo-100 dark:bg-indigo-900/40 text-indigo-900 dark:text-indigo-100 rounded-br-sm"
        }`}
      >
        {isAssistant ? (
          <>
            <div className="prose prose-xs dark:prose-invert max-w-none">
              <MarkdownContent
                content={msg.content}
                onInternalLink={noop}
              />
            </div>
            {msg.content.length > 50 && (
              <button
                onClick={() => setShowRaw((v) => !v)}
                className="mt-1.5 text-[10px] text-gray-400 dark:text-gray-500 hover:text-indigo-500 dark:hover:text-indigo-400 transition-colors"
              >
                {showRaw ? "Hide raw" : "View raw"}
              </button>
            )}
            {showRaw && (
              <pre className="mt-1 text-[10px] leading-relaxed bg-gray-100 dark:bg-gray-800 rounded-lg p-2 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
                {msg.content}
              </pre>
            )}
          </>
        ) : (
          <span className="whitespace-pre-wrap">
            {msg.content}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main component ──────────────────────────────────

interface PastSessionsTabProps {
  showKeywordSearch?: boolean;
}

export function PastSessionsTab({
  showKeywordSearch = false,
}: PastSessionsTabProps) {
  const { sessions, loading, error, fetchSessions } =
    usePastSessions();

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [keyword, setKeyword] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(
    null,
  );
  const [detail, setDetail] =
    useState<ChatSessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Fetch on mount
  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleSearch = useCallback(() => {
    fetchSessions(
      startDate || undefined,
      endDate || undefined,
      keyword || undefined,
    );
    setExpandedId(null);
    setDetail(null);
  }, [fetchSessions, startDate, endDate, keyword]);

  const toggleExpand = useCallback(
    async (sessionId: string) => {
      if (expandedId === sessionId) {
        setExpandedId(null);
        setDetail(null);
        return;
      }
      setExpandedId(sessionId);
      setDetail(null);
      setDetailLoading(true);
      try {
        const res = await apiFetch(
          `${API_URL}/audit/chat-sessions/${sessionId}`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: ChatSessionDetail = await res.json();
        setDetail(data);
      } catch {
        setDetail(null);
      } finally {
        setDetailLoading(false);
      }
    },
    [expandedId],
  );

  const fmtDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  return (
    <div className="flex flex-col h-full px-4 py-4 space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-xs text-gray-500 dark:text-gray-400">
          From
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="mt-0.5 px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-indigo-500 outline-none"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-500 dark:text-gray-400">
          To
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="mt-0.5 px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-indigo-500 outline-none"
          />
        </label>
        {showKeywordSearch && (
          <label className="flex flex-col text-xs text-gray-500 dark:text-gray-400 flex-1 min-w-[120px]">
            Keyword
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="Search messages..."
              className="mt-0.5 px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-indigo-500 outline-none"
            />
          </label>
        )}
        <button
          onClick={handleSearch}
          className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors"
        >
          Search
        </button>
      </div>

      {/* Error */}
      {error && (
        <p className="text-xs text-red-500">{error}</p>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-8">
          <svg
            className="animate-spin h-5 w-5 text-indigo-500"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
        </div>
      )}

      {/* Empty */}
      {!loading && !error && sessions.length === 0 && (
        <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-8">
          No past sessions yet
        </p>
      )}

      {/* Session list */}
      {!loading && sessions.length > 0 && (
        <div className="flex-1 overflow-y-auto space-y-2">
          {sessions.map((s, idx) => {
            const isExpanded = expandedId === s.session_id;
            return (
              <div key={`${s.session_id}-${idx}`}>
                <button
                  onClick={() => toggleExpand(s.session_id)}
                  className={`w-full text-left px-3 py-3 rounded-xl border transition-colors ${
                    isExpanded
                      ? "bg-indigo-50 dark:bg-indigo-900/20 border-indigo-200 dark:border-indigo-700"
                      : "bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:border-indigo-300 dark:hover:border-indigo-600"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-gray-900 dark:text-gray-100">
                      {fmtDate(s.started_at)}
                    </span>
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      {s.message_count} msg
                      {s.message_count !== 1 ? "s" : ""}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 line-clamp-2">
                    {s.preview.slice(0, 150)}
                  </p>
                  {s.agent_ids_used.length > 0 && (
                    <div className="flex gap-1 mt-1.5">
                      {s.agent_ids_used.map((aid) => (
                        <span
                          key={aid}
                          className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                        >
                          {aid}
                        </span>
                      ))}
                    </div>
                  )}
                </button>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="mt-1 ml-2 mr-2 px-3 py-3 rounded-xl bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700 space-y-2 max-h-80 overflow-y-auto">
                    {detailLoading && (
                      <div className="flex justify-center py-4">
                        <svg
                          className="animate-spin h-4 w-4 text-indigo-500"
                          xmlns="http://www.w3.org/2000/svg"
                          fill="none"
                          viewBox="0 0 24 24"
                        >
                          <circle
                            className="opacity-25"
                            cx="12"
                            cy="12"
                            r="10"
                            stroke="currentColor"
                            strokeWidth="4"
                          />
                          <path
                            className="opacity-75"
                            fill="currentColor"
                            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                          />
                        </svg>
                      </div>
                    )}
                    {detail?.messages.map((msg, i) => (
                      <SessionMessage
                        key={`${msg.timestamp}-${i}`}
                        msg={msg}
                      />
                    ))}
                    {!detailLoading &&
                      detail?.messages.length === 0 && (
                        <p className="text-xs text-gray-400 text-center">
                          No messages in this session
                        </p>
                      )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
