"use client";
/**
 * Hook for session management — list, revoke, and revoke-all.
 *
 * Calls the backend session API endpoints and exposes loading,
 * error, and session data for the SessionManagementModal.
 */

import { useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface SessionInfo {
  session_id: string;
  user_id: string;
  ip_address: string;
  user_agent: string;
  created_at: string;
  last_activity_at: string;
}

interface UseSessionManagementResult {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  sessions: SessionInfo[];
  loading: boolean;
  revoking: string | null;
  revokingAll: boolean;
  error: string;
  fetchSessions: () => Promise<void>;
  revokeSession: (sessionId: string) => Promise<void>;
  revokeAllSessions: () => Promise<void>;
}

export function useSessionManagement():
  UseSessionManagementResult {
  const [isOpen, setIsOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [revokingAll, setRevokingAll] = useState(false);
  const [error, setError] = useState("");

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch(
        `${API_URL}/auth/sessions`,
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(
          String(
            (body as Record<string, unknown>).detail
              ?? "Failed to load sessions.",
          ),
        );
        return;
      }
      const data = (await res.json()) as SessionInfo[];
      setSessions(data);
    } catch {
      setError("Network error — please try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  const open = useCallback(() => {
    setError("");
    setIsOpen(true);
    fetchSessions();
  }, [fetchSessions]);

  const close = () => {
    setError("");
    setIsOpen(false);
  };

  const revokeSession = useCallback(
    async (sessionId: string) => {
      setRevoking(sessionId);
      setError("");
      try {
        const res = await apiFetch(
          `${API_URL}/auth/sessions/${sessionId}`,
          { method: "DELETE" },
        );
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setError(
            String(
              (body as Record<string, unknown>).detail
                ?? "Revoke failed.",
            ),
          );
          return;
        }
        setSessions((prev) =>
          prev.filter((s) => s.session_id !== sessionId),
        );
      } catch {
        setError("Network error — please try again.");
      } finally {
        setRevoking(null);
      }
    },
    [],
  );

  const revokeAllSessions = useCallback(async () => {
    setRevokingAll(true);
    setError("");
    try {
      const res = await apiFetch(
        `${API_URL}/auth/sessions/revoke-all`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(
          String(
            (body as Record<string, unknown>).detail
              ?? "Revoke all failed.",
          ),
        );
        return;
      }
      setSessions([]);
    } catch {
      setError("Network error — please try again.");
    } finally {
      setRevokingAll(false);
    }
  }, []);

  return {
    isOpen,
    open,
    close,
    sessions,
    loading,
    revoking,
    revokingAll,
    error,
    fetchSessions,
    revokeSession,
    revokeAllSessions,
  };
}
