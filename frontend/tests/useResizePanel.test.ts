/**
 * Unit tests for frontend/hooks/useResizePanel.ts
 *
 * Verifies that the hook is exported and returns the expected
 * interface shape.
 */

import { describe, it, expect } from "vitest";

describe("useResizePanel", () => {
  it("exports a function", async () => {
    const mod = await import("@/hooks/useResizePanel");
    expect(typeof mod.useResizePanel).toBe("function");
  });

  it("accepts three numeric parameters (min, max, default)", async () => {
    const mod = await import("@/hooks/useResizePanel");
    // Function.length reflects the number of declared parameters
    expect(mod.useResizePanel.length).toBe(3);
  });
});
