/**
 * NOTE: this is an integration-level smoke. Next 16 server
 * redirects are tested via Playwright in Slice 6. The vitest
 * test below exercises the redirect-map pure function so logic
 * stays unit-testable.
 */
import { describe, expect, it } from "vitest";
import { mapLegacyTab } from "@/app/(authenticated)/algo-trading/redirectMap";

describe("mapLegacyTab", () => {
  it("connect -> /broker", () => {
    expect(mapLegacyTab("connect")).toBe("/algo-trading/broker");
  });
  it("instruments -> /strategies?tab=instruments", () => {
    expect(mapLegacyTab("instruments")).toBe(
      "/algo-trading/strategies?tab=instruments",
    );
  });
  it.each(["strategies", "backtest", "performance", "replay"])(
    "%s -> /strategies?tab=%s",
    (id) => {
      expect(mapLegacyTab(id)).toBe(
        `/algo-trading/strategies?tab=${id}`,
      );
    },
  );
  it("paper -> /strategies?tab=paper", () => {
    expect(mapLegacyTab("paper")).toBe(
      "/algo-trading/strategies?tab=paper",
    );
  });
  it("settings -> /strategies?tab=settings", () => {
    expect(mapLegacyTab("settings")).toBe(
      "/algo-trading/strategies?tab=settings",
    );
  });
  it("null -> /strategies (default)", () => {
    expect(mapLegacyTab(null)).toBe("/algo-trading/strategies");
  });
  it("unknown -> /strategies (safe default)", () => {
    expect(mapLegacyTab("ghost")).toBe("/algo-trading/strategies");
  });
});
