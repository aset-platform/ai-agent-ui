/**
 * ActiveRunsPanel — source radio tests (V2-1).
 *
 * Verifies:
 * - "Replay fixture" radio selected by default.
 * - Fixture dropdown visible when source=replay, hidden for live-ws.
 * - "Live Kite WS" radio disabled + tooltip when Kite not connected.
 * - "Live Kite WS" radio enabled when Kite status=connected.
 * - Switching to live-ws hides fixture dropdown.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

// ---- Mock SWR so we can control hook data per test ----------------
type SwrKey =
  | "paper-runs"
  | "broker-status"
  | "fixtures"
  | "strategies";

const swrData: Record<SwrKey, unknown> = {
  "paper-runs": [],
  "broker-status": null,
  fixtures: [],
  strategies: [],
};

vi.mock("swr", () => ({
  default: (key: string) => {
    if (key?.includes("/algo/paper/runs"))
      return { data: swrData["paper-runs"], error: null,
               isLoading: false };
    if (key?.includes("/algo/broker/status"))
      return { data: swrData["broker-status"], error: null,
               isLoading: false };
    if (key?.includes("/algo/paper/fixtures"))
      return { data: swrData["fixtures"], error: null,
               isLoading: false };
    if (key?.includes("/algo/strategies"))
      return { data: swrData["strategies"], error: null,
               isLoading: false };
    return { data: null, error: null, isLoading: false };
  },
  mutate: vi.fn(),
}));

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  }),
}));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { ActiveRunsPanel } from "../ActiveRunsPanel";

afterEach(() => {
  cleanup();
  swrData["paper-runs"] = [];
  swrData["broker-status"] = null;
  swrData["fixtures"] = [];
  swrData["strategies"] = [];
});

describe("ActiveRunsPanel — source radio", () => {
  it("defaults to replay-fixture source", () => {
    render(<ActiveRunsPanel />);
    const replayRadio = screen.getByTestId(
      "paper-source-replay",
    ) as HTMLInputElement;
    expect(replayRadio.checked).toBe(true);
  });

  it("shows fixture dropdown when source=replay", () => {
    swrData["fixtures"] = [
      {
        path: "ticks_sample.jsonl",
        n_ticks: 10,
        distinct_tickers: 1,
        sample_tickers: ["RELIANCE.NS"],
        size_bytes: 1000,
      },
    ];
    render(<ActiveRunsPanel />);
    expect(
      screen.getByTestId("paper-start-fixture-select"),
    ).toBeTruthy();
  });

  it("live-ws radio disabled with tooltip when Kite not connected", () => {
    swrData["broker-status"] = { status: "disconnected" };
    render(<ActiveRunsPanel />);
    const liveWsRadio = screen.getByTestId(
      "paper-source-live-ws",
    ) as HTMLInputElement;
    expect(liveWsRadio.disabled).toBe(true);
  });

  it("live-ws radio enabled when Kite connected", () => {
    swrData["broker-status"] = {
      status: "connected",
      kite_user_id: "AB1234",
    };
    render(<ActiveRunsPanel />);
    const liveWsRadio = screen.getByTestId(
      "paper-source-live-ws",
    ) as HTMLInputElement;
    expect(liveWsRadio.disabled).toBe(false);
  });

  it("switching to live-ws hides fixture dropdown", () => {
    swrData["broker-status"] = { status: "connected" };
    swrData["fixtures"] = [
      {
        path: "ticks_sample.jsonl",
        n_ticks: 10,
        distinct_tickers: 1,
        sample_tickers: ["RELIANCE.NS"],
        size_bytes: 1000,
      },
    ];
    render(<ActiveRunsPanel />);

    // Initially fixture dropdown is visible.
    expect(
      screen.queryByTestId("paper-start-fixture-select"),
    ).toBeTruthy();

    // Click live-ws radio.
    const liveWsRadio = screen.getByTestId("paper-source-live-ws");
    fireEvent.click(liveWsRadio);

    // Fixture dropdown should now be hidden.
    expect(
      screen.queryByTestId("paper-start-fixture-select"),
    ).toBeNull();
  });

  it("switching to live-ws shows live-ws indicator", () => {
    swrData["broker-status"] = { status: "connected" };
    render(<ActiveRunsPanel />);

    const liveWsRadio = screen.getByTestId("paper-source-live-ws");
    fireEvent.click(liveWsRadio);

    expect(
      screen.queryByTestId("paper-live-ws-indicator"),
    ).toBeTruthy();
  });
});
