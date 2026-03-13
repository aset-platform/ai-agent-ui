"use client";
/**
 * WebSocket connection management hook.
 *
 * State machine: DISCONNECTED → CONNECTING → AUTHENTICATING → READY.
 * Falls back gracefully — when `isConnected` is false the caller
 * should use the existing HTTP streaming path.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAccessToken,
  isTokenExpired,
  refreshAccessToken,
} from "@/lib/auth";
import { WS_URL } from "@/lib/config";

/** Events pushed from the server over the WebSocket. */
export interface WsEvent {
  type: string;
  [key: string]: unknown;
}

export interface UseWebSocketReturn {
  /** True when the WS is authenticated and ready for chat. */
  isConnected: boolean;
  /** Send a chat request over the WebSocket. */
  sendChat: (payload: Record<string, unknown>) => void;
  /** Last streaming event received from the server. */
  lastEvent: WsEvent | null;
  /** Force a reconnect. */
  reconnect: () => void;
  /** Register a callback for every incoming WS event. */
  onEvent: (cb: ((evt: WsEvent) => void) | null) => void;
}

type WsState =
  | "DISCONNECTED"
  | "CONNECTING"
  | "AUTHENTICATING"
  | "READY";

const PING_INTERVAL_MS = 30_000;
const MAX_BACKOFF_MS = 30_000;

export function useWebSocket(): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<WsEvent | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const stateRef = useRef<WsState>("DISCONNECTED");
  const retriesRef = useRef(0);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventCbRef = useRef<((evt: WsEvent) => void) | null>(null);
  const mountedRef = useRef(true);
  const connectRef = useRef<() => void>(() => {});

  /** Tear down the current WebSocket without setting React state. */
  const teardown = useCallback(() => {
    if (pingRef.current) {
      clearInterval(pingRef.current);
      pingRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onclose = null;
      wsRef.current.onmessage = null;
      wsRef.current.onerror = null;
      if (
        wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING
      ) {
        wsRef.current.close();
      }
      wsRef.current = null;
    }
    stateRef.current = "DISCONNECTED";
  }, []);

  const connect = useCallback(async () => {
    if (!mountedRef.current) return;
    teardown();

    // Ensure we have a valid token before connecting.
    let token = getAccessToken();
    if (!token || isTokenExpired(token)) {
      token = await refreshAccessToken();
    }
    if (!token) return;

    stateRef.current = "CONNECTING";
    const ws = new WebSocket(`${WS_URL}/ws/chat`);
    wsRef.current = ws;

    ws.onopen = () => {
      stateRef.current = "AUTHENTICATING";
      ws.send(JSON.stringify({ type: "auth", token }));
    };

    ws.onmessage = (evt) => {
      let data: WsEvent;
      try {
        data = JSON.parse(evt.data as string) as WsEvent;
      } catch {
        return;
      }

      if (
        data.type === "auth_ok" &&
        stateRef.current === "AUTHENTICATING"
      ) {
        stateRef.current = "READY";
        retriesRef.current = 0;
        setIsConnected(true);

        pingRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, PING_INTERVAL_MS);
        return;
      }

      setLastEvent(data);
      eventCbRef.current?.(data);
    };

    ws.onerror = () => {};

    ws.onclose = () => {
      teardown();
      setIsConnected(false);
      if (!mountedRef.current) return;

      const delay = Math.min(
        1000 * 2 ** retriesRef.current,
        MAX_BACKOFF_MS,
      );
      retriesRef.current += 1;
      setTimeout(() => {
        if (mountedRef.current) connectRef.current();
      }, delay);
    };
  }, [teardown]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // Connect on mount via microtask (avoids synchronous setState in effect).
  useEffect(() => {
    mountedRef.current = true;
    // Schedule connection outside the synchronous effect body.
    const id = setTimeout(() => connectRef.current(), 0);
    return () => {
      clearTimeout(id);
      mountedRef.current = false;
      teardown();
      setIsConnected(false);
    };
  }, [teardown]);

  const sendChat = useCallback(
    (payload: Record<string, unknown>) => {
      const ws = wsRef.current;
      if (!ws || stateRef.current !== "READY") return;
      ws.send(
        JSON.stringify({ type: "chat", ...payload }),
      );
    },
    [],
  );

  const reconnect = useCallback(() => {
    retriesRef.current = 0;
    connectRef.current();
  }, []);

  const onEvent = useCallback(
    (cb: ((evt: WsEvent) => void) | null) => {
      eventCbRef.current = cb;
    },
    [],
  );

  return { isConnected, sendChat, lastEvent, reconnect, onEvent };
}
