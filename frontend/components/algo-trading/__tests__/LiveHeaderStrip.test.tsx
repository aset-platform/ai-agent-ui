/**
 * LiveHeaderStrip — KPI strip rendered above all Live tabs.
 * Slice 4 of three-page split.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/hooks/useLiveDashboardSummary", () => ({
  useLiveDashboardSummary: () => ({
    summary: {
      today_pnl_inr: "1240.50",
      open_pnl_inr: "820.30",
      realised_pnl_inr: "420.20",
      cash_inr: "98432.10",
      open_position_count: 3,
      mode: "live",
      ws_age_seconds: 2,
      kill_switch_active: false,
    },
    loading: false,
    error: null,
    refresh: () => {},
  }),
}));

import { LiveHeaderStrip } from "../live/LiveHeaderStrip";

afterEach(() => {
  cleanup();
});

describe("LiveHeaderStrip", () => {
  it("renders KPI values + mode chip + WS age", () => {
    render(<LiveHeaderStrip />);
    const strip = screen.getByTestId("live-header-strip");
    const txt = strip.textContent ?? "";

    // INR-formatted KPIs — locale-tolerant regex so ICU variants
    // (comma vs NNBSP grouping separators) across environments
    // don't break the test.
    expect(txt).toMatch(/1[,.]?24[0-1]/); // today rounded ~1,241
    expect(txt).toContain("820");
    expect(txt).toContain("420");
    expect(txt).toMatch(/98[,.]?432/);

    // Mode chip + WS-age testids present
    expect(screen.getByTestId("live-mode-chip")).toBeTruthy();
    const wsAge = screen.getByTestId("live-ws-age");
    expect(wsAge.textContent ?? "").toContain("2s");
  });
});
