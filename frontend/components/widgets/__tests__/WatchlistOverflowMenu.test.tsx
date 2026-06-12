import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { WatchlistOverflowMenu } from "../WatchlistOverflowMenu";

const mockSubmit = vi.fn().mockResolvedValue({
  filename: "u1.jsonl",
  n_tickers: 1,
  n_trigger_dates: 1,
  n_ticks: 3,
  trigger_tickers: ["X.NS"],
});

vi.mock("@/hooks/useBuildReplayFixture", () => ({
  useBuildReplayFixture: () => ({
    submit: mockSubmit,
    submitting: false,
    result: null,
    error: null,
  }),
}));

afterEach(() => {
  cleanup();
  mockSubmit.mockClear();
});

describe("WatchlistOverflowMenu", () => {
  it("opens menu on button click and closes on Escape", () => {
    render(
      <WatchlistOverflowMenu
        onBulkAdd={vi.fn()}
        onRemoveAll={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId(
        "dashboard-watchlist-overflow-menu",
      ),
    ).toBeNull();

    fireEvent.click(
      screen.getByTestId(
        "dashboard-watchlist-overflow-button",
      ),
    );
    expect(
      screen.getByTestId(
        "dashboard-watchlist-overflow-menu",
      ),
    ).toBeDefined();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByTestId(
        "dashboard-watchlist-overflow-menu",
      ),
    ).toBeNull();
  });

  it("Bulk add item click invokes onBulkAdd once", () => {
    const onBulkAdd = vi.fn();
    render(
      <WatchlistOverflowMenu
        onBulkAdd={onBulkAdd}
        onRemoveAll={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByTestId(
        "dashboard-watchlist-overflow-button",
      ),
    );
    fireEvent.click(
      screen.getByTestId(
        "dashboard-watchlist-bulk-add-item",
      ),
    );
    expect(onBulkAdd).toHaveBeenCalledOnce();
  });

  it("Build RSI(2) replay fixture item renders and calls submit on click", () => {
    render(
      <WatchlistOverflowMenu
        onBulkAdd={vi.fn()}
        onRemoveAll={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByTestId("dashboard-watchlist-overflow-button"),
    );
    const fixtureItem = screen.getByTestId("watchlist-build-fixture");
    expect(fixtureItem).toBeDefined();
    fireEvent.click(fixtureItem);
    expect(mockSubmit).toHaveBeenCalledOnce();
  });
});
