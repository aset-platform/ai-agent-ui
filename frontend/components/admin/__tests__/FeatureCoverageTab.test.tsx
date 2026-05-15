/**
 * Vitest unit tests for FeatureCoverageTab (FE-14).
 *
 * Hook + downloadCsv are mocked so tests don't hit the network
 * or touch the DOM clipboard. Verifies:
 *   1. Renders rows for a successful response.
 *   2. Empty state when coverage[] is empty.
 *   3. Stale-data chip fires when any coverage_pct < 95.
 *   4. ColumnSelector toggle hides / shows columns.
 *   5. CSV download invokes downloadCsv with visible cols.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

afterEach(() => cleanup());

vi.mock("@/hooks/useFeatureCoverage", () => ({
  useFeatureCoverage: vi.fn(),
}));

vi.mock("@/lib/downloadCsv", () => ({
  downloadCsv: vi.fn(),
}));

import { useFeatureCoverage } from "@/hooks/useFeatureCoverage";
import { downloadCsv } from "@/lib/downloadCsv";
import { FeatureCoverageTab } from "../FeatureCoverageTab";

const mockUse = useFeatureCoverage as unknown as ReturnType<
  typeof vi.fn
>;
const mockDownload = downloadCsv as unknown as ReturnType<
  typeof vi.fn
>;

function buildResponse(overrides: Partial<{
  staleFeatureCoverage: number;
}> = {}) {
  return {
    data: {
      interval_sec: 900,
      period_start: "2026-04-15",
      period_end: "2026-05-15",
      feature_set_version: "v1.0",
      total_unique_bars: 1000,
      tickers_total: 50,
      rows_total: 25000,
      coverage: [
        {
          feature_name: "sma_20",
          coverage_pct: 100.0,
          rows: 1000,
          tickers_seen: 50,
        },
        {
          feature_name: "rsi_14",
          coverage_pct: 98.5,
          rows: 985,
          tickers_seen: 50,
        },
        {
          feature_name: "vwap",
          coverage_pct:
            overrides.staleFeatureCoverage ?? 97.0,
          rows: 970,
          tickers_seen: 49,
        },
      ],
      computed_at: "2026-05-15T00:00:00+00:00",
    },
    error: undefined,
    loading: false,
    revalidate: vi.fn(),
  };
}

describe("FeatureCoverageTab", () => {
  it("renders rows for a healthy response", () => {
    mockUse.mockReturnValue(buildResponse());

    render(<FeatureCoverageTab />);

    expect(
      screen.getByTestId("feature-coverage-tab"),
    ).toBeDefined();
    expect(screen.getByTestId("fc-row-sma_20")).toBeDefined();
    expect(screen.getByTestId("fc-row-rsi_14")).toBeDefined();
    expect(screen.getByTestId("fc-row-vwap")).toBeDefined();
    expect(screen.getByTestId("fc-summary").textContent).toMatch(
      /3 features/,
    );
  });

  it("renders empty state when coverage list is empty", () => {
    mockUse.mockReturnValue({
      data: {
        interval_sec: 900,
        period_start: "2026-04-15",
        period_end: "2026-05-15",
        feature_set_version: "v1.0",
        total_unique_bars: 0,
        tickers_total: 0,
        rows_total: 0,
        coverage: [],
        computed_at: "2026-05-15T00:00:00+00:00",
      },
      error: undefined,
      loading: false,
      revalidate: vi.fn(),
    });

    render(<FeatureCoverageTab />);
    expect(screen.getByTestId("fc-empty")).toBeDefined();
  });

  it("shows stale chip when any feature is below 95%", () => {
    mockUse.mockReturnValue(
      buildResponse({ staleFeatureCoverage: 80.0 }),
    );

    render(<FeatureCoverageTab />);

    const chip = screen.getByTestId("stale-coverage-chip");
    expect(chip).toBeDefined();
    expect(chip.textContent).toMatch(/below 95/);
  });

  it("does NOT show stale chip when every feature is ≥95%", () => {
    mockUse.mockReturnValue(buildResponse());

    render(<FeatureCoverageTab />);

    expect(
      screen.queryByTestId("stale-coverage-chip"),
    ).toBeNull();
  });

  it("toggles column visibility via ColumnSelector", async () => {
    mockUse.mockReturnValue(buildResponse());

    render(<FeatureCoverageTab />);

    // Flush the microtask used by useColumnSelection so the
    // hook reports hydrated state before we drive interactions.
    await act(async () => {
      await Promise.resolve();
    });

    // Default: all five columns rendered as <th>.
    expect(
      screen.getByTestId("fc-header-coverage_pct"),
    ).toBeDefined();
    expect(
      screen.getByTestId("fc-header-tickers_seen"),
    ).toBeDefined();

    // Open the column popover via its trigger testid.
    fireEvent.click(
      screen.getByTestId("column-selector-trigger"),
    );

    // Uncheck "Tickers Seen".
    const tickersCheckbox = screen.getByLabelText(
      /Tickers Seen/,
    );
    await act(async () => {
      fireEvent.click(tickersCheckbox);
    });

    expect(
      screen.queryByTestId("fc-header-tickers_seen"),
    ).toBeNull();
  });

  it("CSV download is invoked with current rows", () => {
    mockUse.mockReturnValue(buildResponse());
    mockDownload.mockClear();

    render(<FeatureCoverageTab />);

    const btn = screen.getByTestId("download-csv");
    fireEvent.click(btn);

    expect(mockDownload).toHaveBeenCalledTimes(1);
    const args = mockDownload.mock.calls[0];
    const rows = args[0] as Array<{ feature_name: string }>;
    expect(rows.map((r) => r.feature_name).sort()).toEqual([
      "rsi_14",
      "sma_20",
      "vwap",
    ]);
    // Filename slug.
    expect(args[2]).toBe("feature-coverage");
  });
});
