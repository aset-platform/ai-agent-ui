/**
 * Unit tests for hooks/useWebSocket.ts.
 *
 * Uses a mock WebSocket class to verify the connection state machine,
 * authentication flow, reconnection, and event routing.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------

type WsHandler = ((evt: { data: string }) => void) | null;

class MockWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instanceCount = 0;
  static latest: MockWebSocket | null = null;

  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: WsHandler = null;
  onerror: (() => void) | null = null;
  url: string;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instanceCount += 1;
    MockWebSocket.latest = this;
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(data: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  simulateClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/auth", () => ({
  getAccessToken: () => "mock.jwt.token",
  isTokenExpired: () => false,
  refreshAccessToken: vi.fn().mockResolvedValue("mock.jwt.token"),
}));

vi.mock("@/lib/config", () => ({
  WS_URL: "ws://localhost:8181",
  BACKEND_URL: "http://localhost:8181",
}));

vi.stubGlobal("WebSocket", MockWebSocket);

beforeEach(() => {
  MockWebSocket.latest = null;
  MockWebSocket.instanceCount = 0;
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

/** Wait for the hook's setTimeout(fn, 0) to fire and create a WS. */
async function waitForWs(): Promise<MockWebSocket> {
  await vi.advanceTimersByTimeAsync(10);
  if (!MockWebSocket.latest) throw new Error("No WebSocket created");
  return MockWebSocket.latest;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useWebSocket", () => {
  it("connects and authenticates", async () => {
    const mod = await import("@/hooks/useWebSocket");
    const { result } = renderHook(() => mod.useWebSocket());

    const ws = await waitForWs();
    act(() => ws.simulateOpen());

    expect(ws.sent.length).toBe(1);
    const authMsg = JSON.parse(ws.sent[0]);
    expect(authMsg.type).toBe("auth");

    act(() => ws.simulateMessage({ type: "auth_ok" }));
    expect(result.current.isConnected).toBe(true);
  });

  it("reconnects on close with backoff", async () => {
    const mod = await import("@/hooks/useWebSocket");
    renderHook(() => mod.useWebSocket());

    const ws = await waitForWs();
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "auth_ok" }));

    const countBefore = MockWebSocket.instanceCount;
    act(() => ws.simulateClose());

    await vi.advanceTimersByTimeAsync(1500);
    expect(MockWebSocket.instanceCount).toBeGreaterThan(countBefore);
  });

  it("routes events to lastEvent", async () => {
    const mod = await import("@/hooks/useWebSocket");
    const { result } = renderHook(() => mod.useWebSocket());

    const ws = await waitForWs();
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "auth_ok" }));

    act(() => {
      ws.simulateMessage({ type: "thinking", iteration: 1 });
    });

    expect(result.current.lastEvent).toEqual({
      type: "thinking",
      iteration: 1,
    });
  });

  it("sendChat sends over WebSocket when connected", async () => {
    const mod = await import("@/hooks/useWebSocket");
    const { result } = renderHook(() => mod.useWebSocket());

    const ws = await waitForWs();
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "auth_ok" }));

    act(() => {
      result.current.sendChat({
        message: "hello",
        agent_id: "general",
      });
    });

    const chatMsg = JSON.parse(ws.sent[ws.sent.length - 1]);
    expect(chatMsg.type).toBe("chat");
    expect(chatMsg.message).toBe("hello");
  });
});
