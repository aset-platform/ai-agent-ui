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

const _EMPTY: Record<string, Message[]> = { general: [], stock: [] };

function _readStorage(): Record<string, Message[]> {
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
  return _EMPTY;
}

export function useChatHistory(agentId: string) {
  // Always initialise with _EMPTY so server and client produce identical
  // HTML on first render (prevents Next.js hydration mismatch).
  const [histories, setHistories] = useState<Record<string, Message[]>>(_EMPTY);
  const hydrated = useRef(false);

  // Hydrate from localStorage once after mount.  The ref guard ensures
  // this runs exactly once and the subscription below handles persistence.
  // setState in effect is intentional — one-shot hydration from localStorage.
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const stored = _readStorage();
    if (Object.keys(stored).some((k) => (stored[k]?.length ?? 0) > 0)) {
      /* eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot hydration from external store (localStorage) */
      setHistories(stored);
    }
  }, []);

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
