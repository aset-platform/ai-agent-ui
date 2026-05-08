import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

import { FilterDropdown } from "../FilterDropdown";
import { TECH_FILTER_CATALOG } from "../filterCatalogs";

afterEach(() => cleanup());

describe("FilterDropdown", () => {
  function renderTech(selected: string[] = []) {
    const onChange = vi.fn();
    const onReset = vi.fn();
    render(
      <FilterDropdown
        bundleId="tech"
        bundleLabel="Technical"
        catalog={TECH_FILTER_CATALOG}
        selected={selected}
        onChange={onChange}
        onReset={onReset}
      />,
    );
    return { onChange, onReset };
  }

  it("renders trigger button with no badge when nothing selected", () => {
    renderTech();
    const btn = screen.getByTestId("aa-filter-tech-button");
    expect(btn.textContent).toContain("Technical");
    expect(btn.textContent).not.toMatch(/\d/);
  });

  it("shows active count badge when selections exist", () => {
    renderTech(["golden_recent", "price_gt_sma50"]);
    const btn = screen.getByTestId("aa-filter-tech-button");
    expect(btn.textContent).toContain("2");
  });

  it("toggling a checkbox calls onChange with new selection", () => {
    const { onChange } = renderTech([]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(
      screen.getByTestId("aa-filter-tech-option-golden_recent"),
    );
    expect(onChange).toHaveBeenCalledWith(["golden_recent"]);
  });

  it("checking same radio twice keeps single selection", () => {
    const { onChange } = renderTech(["rsi_oversold"]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(
      screen.getByTestId("aa-filter-tech-option-rsi_neutral"),
    );
    expect(onChange).toHaveBeenLastCalledWith(["rsi_neutral"]);
  });

  it("clicking radio again with same value deselects it", () => {
    const { onChange } = renderTech(["rsi_neutral"]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(
      screen.getByTestId("aa-filter-tech-option-rsi_neutral"),
    );
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("reset button calls onReset", () => {
    const { onReset } = renderTech(["golden_recent"]);
    fireEvent.click(screen.getByTestId("aa-filter-tech-button"));
    fireEvent.click(screen.getByTestId("aa-filter-tech-reset"));
    expect(onReset).toHaveBeenCalled();
  });
});
