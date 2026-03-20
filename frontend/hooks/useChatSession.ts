"use client";
/**
 * Chat session lifecycle hook.
 *
 * Replaces ``useChatHistory`` (localStorage persistence) with
 * in-memory-only storage. Session = login-to-logout. On logout
 * the ``flush`` function POSTs the transcript to the audit API.
 */

import { useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { Message } from "@/lib/constants";

export function useChatSession(
  messages: Message[],
  sessionId: string,
  agentId: string,
) {
  const flush = useCallback(async () => {
    if (messages.length === 0) return;
    try {
      await apiFetch(`${API_URL}/audit/chat-sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          messages: messages.map((m) => ({
            role: m.role,
            content: m.content,
            timestamp: m.timestamp.toISOString(),
            agent_id: agentId,
          })),
        }),
      });
    } catch {
      // fire-and-forget — don't block logout
    }
  }, [messages, sessionId, agentId]);

  return { flush };
}
