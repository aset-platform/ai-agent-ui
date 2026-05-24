import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { WatchlistOverflowMenu } from "../WatchlistOverflowMenu";

afterEach(() => cleanup());

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
});
