"use client";
/**
 * Hook for managing per-agent chat histories with localStorage persistence.
 *
 * Returns the message list for the active agent, a scoped setter, and the
 * full histories map.  Histories are loaded from localStorage on mount and
 * saved back whenever they change (debounced to avoid blocking the main
 * thread during streaming).
 */

import { useState, useEffect, useRef } from "react";
import type { Message } from "@/lib/constants";

export function useChatHistory(agentId: string) {
  // Hydrate from localStorage via lazy initializer (avoids setState-in-effect).
  const [histories, setHistories] = useState<Record<string, Message[]>>(() => {
    if (typeof window === "undefined") return { general: [], stock: [] };
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
        return revived;
      }
    } catch { /* ignore corrupt data */ }
    return { general: [], stock: [] };
  });

  // Fix #3: debounce localStorage writes — streaming triggers many rapid updates;
  // writing synchronously on every chunk blocks the main thread on large histories.
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      try {
        localStorage.setItem("chat_histories", JSON.stringify(histories));
      } catch { /* ignore quota errors */ }
    }, 1000);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
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
