/**
 * Tests for the RecommendationsPanel sub-tab dispatch.
 *
 * The actual History / Performance children pull live
 * SWR data, so we mock them with sentinel components and
 * verify only the parent-level switching + URL sync.
 */

import {
  describe, it, expect, vi, afterEach,
} from "vitest";
import {
  render, screen, fireEvent, cleanup, waitFor,
} from "@testing-library/react";

vi.mock(
  "@/components/insights/RecommendationHistoryTab",
  () => ({
    RecommendationHistoryTab: () => (
      <div data-testid="history-pane">history</div>
    ),
  }),
);
vi.mock(
  "@/components/insights/RecommendationPerformanceTab",
  () => ({
    RecommendationPerformanceTab: () => (
      <div data-testid="perf-pane">performance</div>
    ),
  }),
);

import { RecommendationsPanel } from
  "@/components/insights/RecommendationsPanel";

afterEach(() => {
  cleanup();
  window.history.replaceState(
    null, "",
    "/analytics/analysis?tab=recommendations",
  );
});

describe("RecommendationsPanel", () => {
  it("renders both sub-tab buttons", () => {
    render(<RecommendationsPanel />);
    expect(
      screen.getByTestId("subtab-history"),
    ).toBeDefined();
    expect(
      screen.getByTestId("subtab-performance"),
    ).toBeDefined();
  });

  it("starts on History by default", () => {
    render(<RecommendationsPanel />);
    expect(
      screen.getByTestId("history-pane"),
    ).toBeDefined();
    expect(
      screen.queryByTestId("perf-pane"),
    ).toBeNull();
  });

  it("switches to Performance on click", async () => {
    render(<RecommendationsPanel />);
    fireEvent.click(
      screen.getByTestId("subtab-performance"),
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("perf-pane"),
      ).toBeDefined();
    });
    expect(
      screen.queryByTestId("history-pane"),
    ).toBeNull();
    expect(window.location.search).toContain(
      "subtab=performance",
    );
  });

  it("clears subtab param when switching back",
    async () => {
      window.history.replaceState(
        null, "",
        "/analytics/analysis?tab=recommendations" +
          "&subtab=performance",
      );
      render(<RecommendationsPanel />);
      await waitFor(() => {
        expect(
          screen.getByTestId("perf-pane"),
        ).toBeDefined();
      });
      fireEvent.click(
        screen.getByTestId("subtab-history"),
      );
      await waitFor(() => {
        expect(
          screen.getByTestId("history-pane"),
        ).toBeDefined();
      });
      expect(window.location.search).not.toContain(
        "subtab",
      );
    });

  it("hydrates from ?subtab=performance on mount",
    async () => {
      window.history.replaceState(
        null, "",
        "/analytics/analysis?tab=recommendations" +
          "&subtab=performance",
      );
      render(<RecommendationsPanel />);
      await waitFor(() => {
        expect(
          screen.getByTestId("perf-pane"),
        ).toBeDefined();
      });
    });
});
