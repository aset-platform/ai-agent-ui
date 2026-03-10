"use client";
/**
 * Hook encapsulating the streaming chat message send flow.
 *
 * Handles the full lifecycle: optimistic update, NDJSON stream parsing,
 * status-line updates, and error states.
 */

import { useCallback, useEffect, useRef } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { getUserIdFromToken } from "@/lib/auth";
import type { Message } from "@/lib/constants";
import { toolLabel } from "@/lib/constants";

interface UseSendMessageOptions {
  agentId: string;
  messages: Message[];
  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  setLoading: (v: boolean) => void;
  setStatusLine: (v: string) => void;
  input: string;
  setInput: (v: string) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
}

export function useSendMessage({
  agentId,
  messages,
  setMessages,
  setLoading,
  setStatusLine,
  input,
  setInput,
  textareaRef,
}: UseSendMessageOptions) {
  // Fix #1: track in-flight stream so it can be aborted on unmount or new send
  const abortControllerRef = useRef<AbortController | null>(null);

  // Abort any in-flight request when the component unmounts
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  const sendMessage = useCallback(async () => {
    if (!input.trim()) return;

    // Cancel any previous in-flight stream before starting a new one
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const userMessage: Message = {
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    };

    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInput("");
    setLoading(true);
    setStatusLine("Thinking...");

    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    try {
      const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8181";
      const res = await apiFetch(`${backendUrl}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userMessage.content,
          history: messages.map((m) => ({ role: m.role, content: m.content })),
          agent_id: agentId,
          user_id: getUserIdFromToken(),
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errorContent =
          res.status === 504
            ? "Request timed out, please try again."
            : "Error connecting to server. Is the backend running?";
        setMessages([
          ...updatedMessages,
          { role: "assistant", content: errorContent, timestamp: new Date() },
        ]);
        return;
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const event = JSON.parse(trimmed) as Record<string, unknown>;
            if (event.type === "thinking") {
              const iter = event.iteration as number;
              setStatusLine(iter > 1 ? `Thinking... (step ${iter})` : "Thinking...");
            } else if (event.type === "tool_start") {
              setStatusLine(`${toolLabel(event.tool as string)}...`);
            } else if (event.type === "tool_done") {
              setStatusLine(`Got result from ${event.tool as string}...`);
            } else if (event.type === "warning") {
              setStatusLine("Max iterations reached, finalising...");
            } else if (event.type === "final") {
              setMessages([
                ...updatedMessages,
                { role: "assistant", content: event.response as string, timestamp: new Date() },
              ]);
              setStatusLine("");
            } else if (event.type === "error" || event.type === "timeout") {
              setMessages([
                ...updatedMessages,
                { role: "assistant", content: `Error: ${event.message as string}`, timestamp: new Date() },
              ]);
              setStatusLine("");
            }
          } catch { /* skip invalid JSON lines */ }
        }
      }
    } catch (err) {
      // Ignore AbortError — the request was cancelled intentionally
      if (err instanceof Error && err.name === "AbortError") return;
      setMessages([
        ...updatedMessages,
        { role: "assistant", content: "Error connecting to server. Is the backend running?", timestamp: new Date() },
      ]);
      setStatusLine("");
    } finally {
      setLoading(false);
      setStatusLine("");
      textareaRef.current?.focus();
    }
  }, [agentId, input, messages, setInput, setLoading, setMessages, setStatusLine, textareaRef]);

  // Fix #15: stable references — prevent unnecessary re-renders of ChatInput
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  const handleInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
  }, [setInput]);

  return { sendMessage, handleKeyDown, handleInput };
}
