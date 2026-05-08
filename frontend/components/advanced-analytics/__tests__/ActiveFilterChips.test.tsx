import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

afterEach(cleanup);

import { ActiveFilterChips } from "../ActiveFilterChips";

describe("ActiveFilterChips", () => {
  it("renders nothing when both bundles are empty", () => {
    const { container } = render(
      <ActiveFilterChips
        tech={[]}
        fund={[]}
        onRemoveTech={vi.fn()}
        onRemoveFund={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one chip per active key with the catalog label", () => {
    render(
      <ActiveFilterChips
        tech={["golden_recent"]}
        fund={["fscore_ge_7"]}
        onRemoveTech={vi.fn()}
        onRemoveFund={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    const techChip = screen.getByTestId(
      "aa-active-filter-chip-golden_recent",
    );
    const fundChip = screen.getByTestId(
      "aa-active-filter-chip-fscore_ge_7",
    );
    expect(techChip.textContent).toContain("Recent (≤10d)");
    expect(fundChip.textContent).toContain("F-Score ≥ 7");
  });

  it("clicking × on a tech chip calls onRemoveTech with key", () => {
    const onRemoveTech = vi.fn();
    render(
      <ActiveFilterChips
        tech={["golden_recent"]}
        fund={[]}
        onRemoveTech={onRemoveTech}
        onRemoveFund={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByTestId("aa-active-filter-chip-golden_recent-x"),
    );
    expect(onRemoveTech).toHaveBeenCalledWith("golden_recent");
  });

  it("Clear all triggers callback", () => {
    const onClearAll = vi.fn();
    render(
      <ActiveFilterChips
        tech={["golden_recent"]}
        fund={[]}
        onRemoveTech={vi.fn()}
        onRemoveFund={vi.fn()}
        onClearAll={onClearAll}
      />,
    );
    fireEvent.click(screen.getByTestId("aa-active-filter-clear-all"));
    expect(onClearAll).toHaveBeenCalled();
  });
});
