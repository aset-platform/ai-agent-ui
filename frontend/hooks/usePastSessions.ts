"use client";
import { useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { ChatSessionSummary } from "@/lib/types";

export function usePastSessions() {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>(
    [],
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(
    async (
      startDate?: string,
      endDate?: string,
      keyword?: string,
    ) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (startDate) params.set("start_date", startDate);
        if (endDate) params.set("end_date", endDate);
        if (keyword) params.set("keyword", keyword);
        params.set("limit", "50");
        const qs = params.toString();
        const url = `${API_URL}/audit/chat-sessions${
          qs ? `?${qs}` : ""
        }`;
        const res = await apiFetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: ChatSessionSummary[] = await res.json();
        setSessions(data);
      } catch (err: unknown) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return { sessions, loading, error, fetchSessions: fetch_ };
}
