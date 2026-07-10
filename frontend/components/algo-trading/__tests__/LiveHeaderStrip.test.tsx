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

// Header strip uses usePaperRuns for the truthful "armed" signal +
// useWsHealth (inside LiveWsHealthDot) for the WS tooltip. Mock
// both so the test isn't coupled to real fetch.
vi.mock("@/hooks/usePaperRuns", () => ({
  usePaperRuns: () => ({
    runs: [
      {
        user_id: "u",
        strategy_id: "s",
        strategy_name: "ITC RSI",
        started_at: "2026-05-12T05:31:48Z",
        status: "running",
        mode: "live",
        dry_run: false,
      },
    ],
    loading: false,
    error: null,
  }),
}));

// Mutable so a test can flip the WS into the escalated/"wedged"
// state that drives the persistent banner.
const wsHealthState = vi.hoisted(() => ({ wedgeEscalated: false }));

vi.mock("@/hooks/useWsHealth", () => ({
  useWsHealth: () => ({
    health: {
      connected: true,
      subscriber_count: 1,
      subscribed_tokens: 799,
      tick_age_seconds: 2,
      tick_count_today: 12345,
      last_tick_at: "2026-05-12T05:33:18+00:00",
      wedge_escalated: wsHealthState.wedgeEscalated,
    },
    loading: false,
    error: null,
  }),
}));

import { LiveHeaderStrip } from "../live/LiveHeaderStrip";

afterEach(() => {
  cleanup();
  wsHealthState.wedgeEscalated = false;
});

describe("LiveHeaderStrip", () => {
  it("renders KPI values + ARMED mode chip + WS health dot tooltip", () => {
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

    // Mode chip is ARMED (rose) because usePaperRuns returns a
    // live, non-dry-run run.
    const chip = screen.getByTestId("live-mode-chip");
    expect(chip.textContent ?? "").toContain("LIVE ARMED");

    // WS health dot now lives inside the live-ws-age wrapper, with
    // a tooltip surfacing subscribers / ticks-today / tick age.
    const wsArea = screen.getByTestId("live-ws-age");
    expect(wsArea.textContent ?? "").toContain("WS");
    const dot = screen.getByTestId("live-ws-health-dot");
    expect(dot.getAttribute("data-status")).toBe("green");
    const tooltip = dot.getAttribute("title") ?? "";
    expect(tooltip).toContain("Kite WS:");
    expect(tooltip).toContain("12345");      // ticks today
    expect(tooltip).toContain("Subscribers");
  });

  it("keeps the wedge banner inside the sticky header region", () => {
    // Reviewer Minor #1 (ASETPLTFRM-470): a "persistent" alert that
    // sits as a sibling BELOW the sticky strip scrolls out of view on
    // a long Live page. It must live INSIDE the sticky container so it
    // stays pinned while the user scrolls.
    wsHealthState.wedgeEscalated = true;
    render(<LiveHeaderStrip />);
    const strip = screen.getByTestId("live-header-strip");
    const banner = screen.getByTestId("live-ws-wedge-banner");
    expect(strip.contains(banner)).toBe(true);
  });
});
