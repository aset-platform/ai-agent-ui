/**
 * Unit tests for frontend/hooks/useChatSession.ts
 *
 * Verifies that the hook is exported and the module loads
 * correctly with mocked dependencies.
 */

import { describe, it, expect, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(() => Promise.resolve({ ok: true })),
}));
vi.mock("@/lib/config", () => ({
  API_URL: "http://test:8181/v1",
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useChatSession", () => {
  it("exports a function", async () => {
    const mod = await import("@/hooks/useChatSession");
    expect(typeof mod.useChatSession).toBe("function");
  });

  it("accepts three parameters (messages, sessionId, agentId)", async () => {
    const mod = await import("@/hooks/useChatSession");
    expect(mod.useChatSession.length).toBe(3);
  });
});
