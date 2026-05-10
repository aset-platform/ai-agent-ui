/**
 * LiveWsHealthDot — unit tests (OBS-1).
 *
 * Verifies:
 *  - statusFromAge() boundary semantics
 *    (disconnected / no-tick / green / amber / red)
 *  - Component renders the colour matching the current status
 *  - Tooltip text mirrors the snapshot fields
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

afterEach(() => cleanup());

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));
vi.mock("@/lib/config", () => ({
  API_URL: "http://test/v1",
  BACKEND_URL: "http://test",
}));

import {
  LiveWsHealthDot,
  statusFromAge,
} from "../LiveWsHealthDot";
import type { WsHealth } from "@/hooks/useWsHealth";

const SNAP_GREEN: WsHealth = {
  connected: true,
  subscriber_count: 2,
  subscribed_tokens: 4,
  last_tick_at: "2026-05-10T03:00:00Z",
  tick_age_seconds: 5,
  tick_count_today: 17,
};

describe("statusFromAge", () => {
  it("returns disconnected when not connected", () => {
    expect(statusFromAge(0, false)).toBe("disconnected");
    expect(statusFromAge(null, false)).toBe("disconnected");
  });

  it("returns no-tick when connected but no tick yet", () => {
    expect(statusFromAge(null, true)).toBe("no-tick");
  });

  it("returns green for fresh ticks (≤ 30s)", () => {
    expect(statusFromAge(0, true)).toBe("green");
    expect(statusFromAge(15, true)).toBe("green");
    expect(statusFromAge(30, true)).toBe("green");
  });

  it("returns amber for stale ticks (31–120s)", () => {
    expect(statusFromAge(31, true)).toBe("amber");
    expect(statusFromAge(60, true)).toBe("amber");
    expect(statusFromAge(120, true)).toBe("amber");
  });

  it("returns red for very stale ticks (> 120s)", () => {
    expect(statusFromAge(121, true)).toBe("red");
    expect(statusFromAge(3600, true)).toBe("red");
  });
});

describe("LiveWsHealthDot — rendering", () => {
  it("renders a dot and a status-coloured class for green", () => {
    render(<LiveWsHealthDot health={SNAP_GREEN} />);
    const dot = screen.getByTestId("live-ws-health-dot");
    expect(dot.className).toContain("bg-emerald");
    expect(dot.getAttribute("title")).toContain("connected");
  });

  it("renders amber for stale tick", () => {
    render(
      <LiveWsHealthDot
        health={{ ...SNAP_GREEN, tick_age_seconds: 90 }}
      />,
    );
    const dot = screen.getByTestId("live-ws-health-dot");
    expect(dot.className).toContain("bg-amber");
  });

  it("renders red for very stale tick", () => {
    render(
      <LiveWsHealthDot
        health={{ ...SNAP_GREEN, tick_age_seconds: 600 }}
      />,
    );
    const dot = screen.getByTestId("live-ws-health-dot");
    expect(dot.className).toContain("bg-rose");
  });

  it("renders slate (disconnected) when not connected", () => {
    render(
      <LiveWsHealthDot
        health={{
          ...SNAP_GREEN,
          connected: false,
          tick_age_seconds: null,
        }}
      />,
    );
    const dot = screen.getByTestId("live-ws-health-dot");
    expect(dot.className).toContain("bg-slate");
    expect(dot.getAttribute("title")?.toLowerCase()).toContain(
      "disconnected",
    );
  });

  it("still renders a slate dot when health is null (loading)", () => {
    render(<LiveWsHealthDot health={null} />);
    const dot = screen.getByTestId("live-ws-health-dot");
    expect(dot.className).toContain("bg-slate");
  });

  it("tooltip mentions tick count and age when available", () => {
    render(<LiveWsHealthDot health={SNAP_GREEN} />);
    const dot = screen.getByTestId("live-ws-health-dot");
    const title = dot.getAttribute("title") ?? "";
    expect(title).toContain("17");
    expect(title.toLowerCase()).toContain("ago");
  });
});
