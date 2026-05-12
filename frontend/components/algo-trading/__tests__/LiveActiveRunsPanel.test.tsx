/**
 * LiveActiveRunsPanel — ASETPLTFRM-378.
 *
 * The Live page's Start / Stop control. Verifies:
 *  1. Strategy picker renders strategies from useStrategies().
 *  2. Start button calls startPaperRun(..., "live") literally.
 *  3. Only mode="live" && !dry_run rows appear in the list
 *     (paper + dry-run runs are filtered out).
 *  4. Stop button wires through to stopPaperRun.
 */
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

const swrData: Record<string, unknown> = {
  "paper-runs": [],
  "broker-status": null,
  strategies: [],
};

vi.mock("swr", () => ({
  default: (key: string) => {
    if (key?.includes("/algo/paper/runs")) {
      return {
        data: swrData["paper-runs"],
        error: null,
        isLoading: false,
      };
    }
    if (key?.includes("/algo/broker/status")) {
      return {
        data: swrData["broker-status"],
        error: null,
        isLoading: false,
      };
    }
    if (key?.includes("/algo/strategies")) {
      return {
        data: swrData["strategies"],
        error: null,
        isLoading: false,
      };
    }
    return { data: null, error: null, isLoading: false };
  },
  mutate: vi.fn(),
}));

const startPaperRunMock = vi.fn().mockResolvedValue(undefined);
const stopPaperRunMock = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/usePaperRuns", async () => {
  const actual = await vi.importActual<
    typeof import("@/hooks/usePaperRuns")
  >("@/hooks/usePaperRuns");
  return {
    ...actual,
    startPaperRun: (...args: unknown[]) =>
      startPaperRunMock(...args),
    stopPaperRun: (...args: unknown[]) =>
      stopPaperRunMock(...args),
    usePaperRuns: () => ({
      runs: (swrData["paper-runs"] as unknown[]) ?? [],
      loading: false,
      error: null,
    }),
  };
});

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  }),
}));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

// useStrategies expects { strategies: [...] } shape from SWR
// fetcher; the mock above returns that directly under
// the strategies key, but the hook also exposes them as
// .strategies. We override the hook to keep tests deterministic.
vi.mock("@/hooks/useStrategies", () => ({
  useStrategies: () => ({
    strategies: (swrData["strategies"] as unknown[]) ?? [],
    loading: false,
    error: null,
  }),
}));

import { LiveActiveRunsPanel } from "../live/LiveActiveRunsPanel";

afterEach(() => {
  cleanup();
  swrData["paper-runs"] = [];
  swrData["broker-status"] = null;
  swrData["strategies"] = [];
  startPaperRunMock.mockClear();
  stopPaperRunMock.mockClear();
});

describe("LiveActiveRunsPanel", () => {
  it("renders strategies from useStrategies()", () => {
    swrData["strategies"] = [
      { id: "s1", name: "Alpha" },
      { id: "s2", name: "Beta" },
    ];
    render(<LiveActiveRunsPanel />);
    const select = screen.getByTestId(
      "live-start-strategy-select",
    ) as HTMLSelectElement;
    // Includes the placeholder option + 2 real ones.
    expect(select.options.length).toBe(3);
    expect(select.options[1].textContent).toBe("Alpha");
    expect(select.options[2].textContent).toBe("Beta");
  });

  it("Start button calls startPaperRun with literal 'live'", async () => {
    swrData["strategies"] = [{ id: "s1", name: "Alpha" }];
    swrData["broker-status"] = { status: "connected" };
    render(<LiveActiveRunsPanel />);

    const select = screen.getByTestId(
      "live-start-strategy-select",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "s1" } });

    const startBtn = screen.getByTestId("live-start-btn");
    fireEvent.click(startBtn);

    // Tick to let the async handler resolve.
    await new Promise((r) => setTimeout(r, 0));

    expect(startPaperRunMock).toHaveBeenCalledTimes(1);
    const [strategyId, fixturePath, capital, source, mode] =
      startPaperRunMock.mock.calls[0];
    expect(strategyId).toBe("s1");
    expect(fixturePath).toBe("");
    expect(capital).toBe("100000.00");
    expect(source).toBe("live-ws");
    expect(mode).toBe("live"); // literal "live" — not aliased.
  });

  it("filters the active-runs list to mode=live && !dry_run", () => {
    swrData["strategies"] = [{ id: "s1", name: "Alpha" }];
    swrData["paper-runs"] = [
      {
        user_id: "u",
        strategy_id: "s-paper",
        strategy_name: "PaperOne",
        started_at: new Date().toISOString(),
        status: "running",
        mode: "paper",
        dry_run: false,
      },
      {
        user_id: "u",
        strategy_id: "s-dryrun",
        strategy_name: "DryOne",
        started_at: new Date().toISOString(),
        status: "running",
        mode: "live",
        dry_run: true,
      },
      {
        user_id: "u",
        strategy_id: "s-live",
        strategy_name: "LiveOne",
        started_at: new Date().toISOString(),
        status: "running",
        mode: "live",
        dry_run: false,
      },
    ];

    render(<LiveActiveRunsPanel />);

    // The live row must appear.
    expect(
      screen.queryByTestId("live-active-run-s-live"),
    ).toBeTruthy();
    // Paper and dry-run rows must NOT.
    expect(
      screen.queryByTestId("live-active-run-s-paper"),
    ).toBeNull();
    expect(
      screen.queryByTestId("live-active-run-s-dryrun"),
    ).toBeNull();
  });

  it("Stop button calls stopPaperRun with the row strategy_id", async () => {
    swrData["strategies"] = [{ id: "s-live", name: "LiveOne" }];
    swrData["paper-runs"] = [
      {
        user_id: "u",
        strategy_id: "s-live",
        strategy_name: "LiveOne",
        started_at: new Date().toISOString(),
        status: "running",
        mode: "live",
        dry_run: false,
      },
    ];
    render(<LiveActiveRunsPanel />);

    const stopBtn = screen.getByTestId("live-stop-btn-s-live");
    fireEvent.click(stopBtn);
    await new Promise((r) => setTimeout(r, 0));

    expect(stopPaperRunMock).toHaveBeenCalledWith("s-live");
  });

  it("disables Start when Kite is not connected", () => {
    swrData["strategies"] = [{ id: "s1", name: "Alpha" }];
    swrData["broker-status"] = { status: "disconnected" };
    render(<LiveActiveRunsPanel />);
    const startBtn = screen.getByTestId(
      "live-start-btn",
    ) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });
});
