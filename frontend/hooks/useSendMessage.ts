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
import { BACKEND_URL } from "@/lib/config";
import type { Message } from "@/lib/constants";
import { toolLabel } from "@/lib/constants";
import type { UseWebSocketReturn, WsEvent } from "@/hooks/useWebSocket";

interface UseSendMessageOptions {
  agentId: string;
  messages: Message[];
  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  setLoading: (v: boolean) => void;
  setStatusLine: (v: string) => void;
  input: string;
  setInput: (v: string) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  ws?: UseWebSocketReturn;
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
  ws,
}: UseSendMessageOptions) {
  // Fix #1: track in-flight stream so it can be aborted on unmount or new send
  const abortControllerRef = useRef<AbortController | null>(null);

  // Abort any in-flight request when the component unmounts
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  // ---------------------------------------------------------------
  // Shared event handler (used by both WS and HTTP paths)
  // ---------------------------------------------------------------
  const handleEvent = useCallback(
    (event: Record<string, unknown>, updatedMessages: Message[]) => {
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
        setLoading(false);
        textareaRef.current?.focus();
      } else if (event.type === "error" || event.type === "timeout") {
        setMessages([
          ...updatedMessages,
          { role: "assistant", content: `Error: ${event.message as string}`, timestamp: new Date() },
        ]);
        setStatusLine("");
        setLoading(false);
        textareaRef.current?.focus();
      }
    },
    [setLoading, setMessages, setStatusLine, textareaRef],
  );

  // ---------------------------------------------------------------
  // WebSocket send path
  // ---------------------------------------------------------------
  const sendViaWs = useCallback(
    (userMessage: Message, updatedMessages: Message[]) => {
      if (!ws) return;

      // Register per-message event listener.
      ws.onEvent((evt: WsEvent) => {
        handleEvent(evt as Record<string, unknown>, updatedMessages);
        // Deregister after terminal events.
        if (evt.type === "final" || evt.type === "error" || evt.type === "timeout") {
          ws.onEvent(null);
        }
      });

      ws.sendChat({
        message: userMessage.content,
        history: messages.map((m) => ({ role: m.role, content: m.content })),
        agent_id: agentId,
        user_id: getUserIdFromToken(),
      });
    },
    [ws, handleEvent, messages, agentId],
  );

  // ---------------------------------------------------------------
  // HTTP fallback send path
  // ---------------------------------------------------------------
  const sendViaHttp = useCallback(
    async (userMessage: Message, updatedMessages: Message[]) => {
      const controller = new AbortController();
      abortControllerRef.current = controller;

      try {
        const res = await apiFetch(`${BACKEND_URL}/chat/stream`, {
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
              handleEvent(event, updatedMessages);
            } catch { /* skip invalid JSON lines */ }
          }
        }
      } catch (err) {
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
    },
    [agentId, handleEvent, messages, setLoading, setMessages, setStatusLine, textareaRef],
  );

  // ---------------------------------------------------------------
  // Public sendMessage — routes to WS or HTTP
  // ---------------------------------------------------------------
  const sendMessage = useCallback(async () => {
    if (!input.trim()) return;

    abortControllerRef.current?.abort();

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

    if (ws?.isConnected) {
      sendViaWs(userMessage, updatedMessages);
    } else {
      await sendViaHttp(userMessage, updatedMessages);
    }
  }, [input, messages, setInput, setLoading, setMessages, setStatusLine, textareaRef, ws, sendViaWs, sendViaHttp]);

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
