"use client";
/**
 * Chat context provider — manages chat panel state, messages, agent
 * selection, and WebSocket connection for the side panel.
 *
 * Replaces the old prop-drilled chat state from page.tsx. The WebSocket
 * lives here so it stays connected even when the panel is closed.
 */

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import type { Message } from "@/lib/constants";
import {
  useWebSocket,
  type UseWebSocketReturn,
} from "@/hooks/useWebSocket";
import { useChatSession } from "@/hooks/useChatSession";

interface ChatContextValue {
  messages: Message[];
  setMessages: React.Dispatch<
    React.SetStateAction<Message[]>
  >;
  isOpen: boolean;
  togglePanel: () => void;
  closePanel: () => void;
  openPanel: () => void;
  agentId: string;
  sessionId: string;
  ws: UseWebSocketReturn;
  flush: () => Promise<void>;
  startFromSession: (
    oldSessionId: string,
    preview: string,
  ) => void;
}

const ChatContext = createContext<ChatContextValue | null>(
  null,
);

export function ChatProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const agentId = "general";
  // Generate sessionId only on client to avoid SSR
  // hydration mismatch (server vs client UUID).
  const [sessionId, setSessionId] = useState<string>(
    () =>
      typeof window !== "undefined"
        ? crypto.randomUUID()
        : "",
  );
  const ws = useWebSocket();
  const { flush } = useChatSession(
    messages,
    sessionId,
    agentId,
  );

  const togglePanel = useCallback(
    () => setIsOpen((v) => !v),
    [],
  );
  const closePanel = useCallback(() => {
    setIsOpen(false);
    flush();
  }, [flush]);
  const openPanel = useCallback(
    () => setIsOpen(true),
    [],
  );

  const startFromSession = useCallback(
    (oldSessionId: string, preview: string) => {
      // Flush current session before starting new
      flush();
      // Generate a fresh session ID
      setSessionId(crypto.randomUUID());
      // Clear messages and inject a system note
      setMessages([
        {
          role: "assistant" as const,
          content:
            `Continuing from a previous session. ` +
            `Context: ${preview.slice(0, 150)}`,
          timestamp: new Date(),
        },
      ]);
      setIsOpen(true);
    },
    [flush],
  );

  // Flush on tab close / browser close as last resort.
  // Sync via effect so we never write to a ref during render.
  const messagesRef = useRef<Message[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (messagesRef.current.length === 0) return;
      // Use fetch+keepalive (not sendBeacon) so we
      // can include the Authorization header.
      const url = `${
        process.env.NEXT_PUBLIC_BACKEND_URL
          ?? "http://127.0.0.1:8181"
      }/v1/audit/chat-sessions`;
      const token =
        typeof localStorage !== "undefined"
          ? localStorage.getItem("auth_access_token")
          : null;
      if (!token) return;
      const body = JSON.stringify({
        session_id: sessionId,
        messages: messagesRef.current.map((m) => ({
          role: m.role,
          content: m.content,
          timestamp: m.timestamp.toISOString(),
          agent_id: agentId,
        })),
      });
      fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body,
        keepalive: true,
      });
    };
    window.addEventListener(
      "beforeunload",
      handleBeforeUnload,
    );
    return () =>
      window.removeEventListener(
        "beforeunload",
        handleBeforeUnload,
      );
  }, [sessionId, agentId]);

  return (
    <ChatContext.Provider
      value={{
        messages,
        setMessages,
        isOpen,
        togglePanel,
        closePanel,
        openPanel,
        agentId,
        sessionId,
        ws,
        flush,
        startFromSession,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChatContext(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error(
      "useChatContext must be used within ChatProvider",
    );
  }
  return ctx;
}
