/**
 * Session Management modal.
 *
 * Lists active sessions with device info, IP, and timestamps.
 * Each session card has a Revoke button; a top-level "Revoke All"
 * button clears every session at once.  The current session is
 * highlighted with a green accent.
 */

import { useState } from "react";
import type { SessionInfo } from "@/hooks/useSessionManagement";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface SessionManagementModalProps {
  isOpen: boolean;
  sessions: SessionInfo[];
  loading: boolean;
  revoking: string | null;
  revokingAll: boolean;
  error: string;
  currentSessionId: string | null;
  onClose: () => void;
  onRevoke: (sessionId: string) => Promise<void>;
  onRevokeAll: () => Promise<void>;
}

/** Parse the user-agent into a short device label + icon. */
function parseDevice(ua: string): {
  label: string;
  icon: "desktop" | "mobile" | "tablet" | "unknown";
} {
  const lower = ua.toLowerCase();

  // Detect device type
  let icon: "desktop" | "mobile" | "tablet" | "unknown" =
    "unknown";
  if (/ipad|tablet|kindle/i.test(lower)) icon = "tablet";
  else if (/mobile|iphone|android.*mobile/i.test(lower))
    icon = "mobile";
  else if (/windows|macintosh|linux|x11/i.test(lower))
    icon = "desktop";

  // Extract browser name
  let browser = "Unknown Browser";
  if (/edg\//i.test(ua)) browser = "Edge";
  else if (/opr\//i.test(ua) || /opera/i.test(ua))
    browser = "Opera";
  else if (/chrome\//i.test(ua) && !/edg/i.test(ua))
    browser = "Chrome";
  else if (/safari\//i.test(ua) && !/chrome/i.test(ua))
    browser = "Safari";
  else if (/firefox\//i.test(ua)) browser = "Firefox";

  // Extract OS
  let os = "";
  if (/windows/i.test(ua)) os = "Windows";
  else if (/macintosh|mac os/i.test(ua)) os = "macOS";
  else if (/linux/i.test(ua) && !/android/i.test(ua))
    os = "Linux";
  else if (/android/i.test(ua)) os = "Android";
  else if (/iphone|ipad/i.test(ua)) os = "iOS";

  const label = os ? `${browser} on ${os}` : browser;
  return { label, icon };
}

/** Format ISO timestamp to relative or absolute. */
function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);

  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;

  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year:
      d.getFullYear() !== now.getFullYear()
        ? "numeric"
        : undefined,
  });
}

function DeviceIcon({
  type,
}: {
  type: "desktop" | "mobile" | "tablet" | "unknown";
}) {
  const cls =
    "w-5 h-5 shrink-0 text-gray-400 dark:text-gray-500" +
    " group-hover:text-gray-500 dark:group-hover:text-gray-400" +
    " transition-colors";

  if (type === "mobile") {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className={cls}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
        <line x1="12" y1="18" x2="12.01" y2="18" />
      </svg>
    );
  }
  if (type === "tablet") {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className={cls}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="4" y="2" width="16" height="20" rx="2" ry="2" />
        <line x1="12" y1="18" x2="12.01" y2="18" />
      </svg>
    );
  }
  if (type === "desktop") {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className={cls}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
        <line x1="8" y1="21" x2="16" y2="21" />
        <line x1="12" y1="17" x2="12" y2="21" />
      </svg>
    );
  }
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className={cls}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

export function SessionManagementModal({
  isOpen,
  sessions,
  loading,
  revoking,
  revokingAll,
  error,
  currentSessionId,
  onClose,
  onRevoke,
  onRevokeAll,
}: SessionManagementModalProps) {
  // Confirm dialog state — hooks must be before
  // any early return (Rules of Hooks).
  const [revokeId, setRevokeId] =
    useState<string | null>(null);
  const [showRevokeAll, setShowRevokeAll] =
    useState(false);

  if (!isOpen) return null;

  const sorted = [...sessions].sort((a, b) => {
    // Current session first, then by created_at descending.
    if (a.session_id === currentSessionId) return -1;
    if (b.session_id === currentSessionId) return 1;
    return (
      new Date(b.created_at).getTime() -
      new Date(a.created_at).getTime()
    );
  });

  return (
    <div
      className={
        "fixed inset-0 z-50 flex items-center justify-center" +
        " bg-black/40"
      }
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={
          "bg-white dark:bg-gray-800 rounded-2xl shadow-xl" +
          " w-full max-w-lg mx-4 flex flex-col max-h-[80vh]" +
          " transition-colors"
        }
        data-testid="session-management-modal"
      >
        {/* -- Header ----------------------------------- */}
        <div
          className={
            "flex items-center justify-between px-6 pt-5" +
            " pb-4 border-b border-gray-100 dark:border-gray-700"
          }
        >
          <div>
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Active Sessions
            </h2>
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
              {sessions.length}{" "}
              {sessions.length === 1 ? "session" : "sessions"}{" "}
              active
            </p>
          </div>

          <div className="flex items-center gap-2">
            {sessions.length > 1 && (
              <button
                onClick={() => setShowRevokeAll(true)}
                disabled={revokingAll}
                className={
                  "text-xs font-medium px-3 py-1.5 rounded-lg" +
                  " text-red-600 dark:text-red-400" +
                  " border border-red-200 dark:border-red-800" +
                  " hover:bg-red-50 dark:hover:bg-red-900/20" +
                  " transition-colors" +
                  " disabled:opacity-50 disabled:cursor-not-allowed"
                }
              >
                {revokingAll ? "Revoking…" : "Revoke All"}
              </button>
            )}
            <button
              onClick={onClose}
              className={
                "w-8 h-8 flex items-center justify-center" +
                " rounded-lg text-gray-400 dark:text-gray-500" +
                " hover:text-gray-600 dark:hover:text-gray-300" +
                " hover:bg-gray-100 dark:hover:bg-gray-700" +
                " transition-colors"
              }
              aria-label="Close"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="w-4 h-4"
                viewBox="0 0 24 24"
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
          </div>
        </div>

        {/* -- Error ------------------------------------ */}
        {error && (
          <div className="px-6 pt-3">
            <p
              className={
                "text-sm text-red-500 bg-red-50" +
                " dark:bg-red-900/20 rounded-lg px-3 py-2"
              }
            >
              {error}
            </p>
          </div>
        )}

        {/* -- Body ------------------------------------- */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div
                className={
                  "w-6 h-6 border-2 border-indigo-200" +
                  " dark:border-indigo-800" +
                  " border-t-indigo-600 rounded-full animate-spin"
                }
              />
              <span className="ml-3 text-sm text-gray-400 dark:text-gray-500">
                Loading sessions…
              </span>
            </div>
          ) : sorted.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-sm text-gray-400 dark:text-gray-500">
                No active sessions found.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {sorted.map((session) => {
                const isCurrent =
                  session.session_id === currentSessionId;
                const device = parseDevice(session.user_agent);
                const isRevoking =
                  revoking === session.session_id;

                return (
                  <div
                    key={session.session_id}
                    className={
                      "group relative rounded-xl border" +
                      " px-4 py-3.5 transition-colors" +
                      (isCurrent
                        ? " border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-900/20"
                        : " border-gray-150 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50" +
                          " hover:bg-gray-50 dark:hover:bg-gray-700/50")
                    }
                  >
                    <div className="flex items-start gap-3">
                      {/* Device icon */}
                      <div
                        className={
                          "mt-0.5 w-9 h-9 rounded-lg flex" +
                          " items-center justify-center" +
                          (isCurrent
                            ? " bg-emerald-100 dark:bg-emerald-900/40"
                            : " bg-gray-100 dark:bg-gray-700" +
                              " group-hover:bg-gray-200/70 dark:group-hover:bg-gray-600/50")
                        }
                      >
                        <DeviceIcon type={device.icon} />
                      </div>

                      {/* Session info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span
                            className={
                              "text-sm font-medium" +
                              " text-gray-800 dark:text-gray-200"
                            }
                          >
                            {device.label}
                          </span>
                          {isCurrent && (
                            <span
                              className={
                                "text-[10px] font-semibold" +
                                " uppercase tracking-wider" +
                                " text-emerald-700 dark:text-emerald-400" +
                                " bg-emerald-100 dark:bg-emerald-900/40" +
                                " px-1.5 py-0.5 rounded"
                              }
                            >
                              Current
                            </span>
                          )}
                        </div>
                        <div
                          className={
                            "flex items-center gap-3 mt-1" +
                            " text-xs text-gray-400 dark:text-gray-500"
                          }
                        >
                          <span
                            className="font-mono"
                            title="IP Address"
                          >
                            {session.ip_address || "—"}
                          </span>
                          <span title="Created">
                            {formatTime(session.created_at)}
                          </span>
                        </div>
                      </div>

                      {/* Revoke button */}
                      {!isCurrent && (
                        <button
                          onClick={() =>
                            setRevokeId(session.session_id)
                          }
                          disabled={isRevoking || revokingAll}
                          className={
                            "text-xs font-medium px-3 py-1.5" +
                            " rounded-lg text-gray-500" +
                            " dark:text-gray-400" +
                            " border border-gray-200" +
                            " dark:border-gray-600" +
                            " hover:text-red-600" +
                            " dark:hover:text-red-400" +
                            " hover:border-red-200" +
                            " dark:hover:border-red-800" +
                            " hover:bg-red-50" +
                            " dark:hover:bg-red-900/20" +
                            " transition-colors" +
                            " disabled:opacity-50" +
                            " disabled:cursor-not-allowed"
                          }
                        >
                          {isRevoking ? "…" : "Revoke"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* -- Footer ----------------------------------- */}
        <div
          className={
            "px-6 py-3 border-t border-gray-100" +
            " dark:border-gray-700 flex justify-end"
          }
        >
          <button
            onClick={onClose}
            className={
              "px-4 py-2 text-sm text-gray-600" +
              " dark:text-gray-400 border border-gray-300" +
              " dark:border-gray-600 rounded-lg" +
              " hover:bg-gray-50 dark:hover:bg-gray-700"
            }
          >
            Close
          </button>
        </div>
      </div>

      {/* Revoke single session confirm */}
      <ConfirmDialog
        open={revokeId !== null}
        title="Revoke Session"
        message="Revoke this session? The device will be signed out."
        confirmLabel="Revoke"
        variant="danger"
        onConfirm={() => {
          if (revokeId) onRevoke(revokeId);
          setRevokeId(null);
        }}
        onCancel={() => setRevokeId(null)}
      />

      {/* Revoke all confirm */}
      <ConfirmDialog
        open={showRevokeAll}
        title="Revoke All Sessions"
        message="Sign out all other devices? You will stay signed in on this device."
        confirmLabel="Revoke All"
        variant="danger"
        onConfirm={() => {
          onRevokeAll();
          setShowRevokeAll(false);
        }}
        onCancel={() => setShowRevokeAll(false)}
      />
    </div>
  );
}
