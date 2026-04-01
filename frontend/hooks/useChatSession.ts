"use client";
/**
 * Chat session lifecycle hook.
 *
 * Replaces ``useChatHistory`` (localStorage persistence) with
 * in-memory-only storage. Session = login-to-logout. On logout
 * the ``flush`` function POSTs the transcript to the audit API.
 *
 * Uses raw ``fetch`` instead of ``apiFetch`` to avoid the 401
 * handler clearing tokens and redirecting mid-logout.
 */

import { useCallback } from "react";
import { getAccessToken } from "@/lib/auth";
import { API_URL } from "@/lib/config";
import type { Message } from "@/lib/constants";

export function useChatSession(
  messages: Message[],
  sessionId: string,
  agentId: string,
) {
  const flush = useCallback(async () => {
    if (messages.length === 0) return;
    const token = getAccessToken();
    if (!token) return; // already logged out
    try {
      await fetch(
        `${API_URL}/audit/chat-sessions`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            session_id: sessionId,
            messages: messages.map((m) => ({
              role: m.role,
              content: m.content,
              timestamp: m.timestamp.toISOString(),
              agent_id: agentId,
            })),
          }),
          credentials: "include",
        },
      );
    } catch {
      // fire-and-forget — don't block logout
    }
  }, [messages, sessionId, agentId]);

  return { flush };
}
