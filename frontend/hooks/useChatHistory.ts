"use client";
/**
 * Hook for managing per-agent chat histories with localStorage persistence.
 *
 * Returns the message list for the active agent, a scoped setter, and the
 * full histories map.  Histories are loaded from localStorage on mount and
 * saved back whenever they change.
 */

import { useState, useEffect } from "react";
import type { Message } from "@/lib/constants";

export function useChatHistory(agentId: string) {
  const [histories, setHistories] = useState<Record<string, Message[]>>({
    general: [],
    stock: [],
  });

  // Load persisted histories from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("chat_histories");
      if (saved) {
        const parsed = JSON.parse(saved) as Record<
          string,
          { role: "user" | "assistant"; content: string; timestamp: string }[]
        >;
        const revived: Record<string, Message[]> = {};
        for (const [id, msgs] of Object.entries(parsed)) {
          revived[id] = msgs.map((m) => ({ ...m, timestamp: new Date(m.timestamp) }));
        }
        setHistories(revived);
      }
    } catch { /* ignore corrupt data */ }
  }, []);

  // Save histories to localStorage whenever they change
  useEffect(() => {
    try {
      localStorage.setItem("chat_histories", JSON.stringify(histories));
    } catch { /* ignore quota errors */ }
  }, [histories]);

  const messages = histories[agentId] ?? [];

  const setMessages = (updater: Message[] | ((prev: Message[]) => Message[])) => {
    setHistories((h) => ({
      ...h,
      [agentId]: typeof updater === "function" ? updater(h[agentId] ?? []) : updater,
    }));
  };

  return { messages, setMessages, histories, setHistories };
}
