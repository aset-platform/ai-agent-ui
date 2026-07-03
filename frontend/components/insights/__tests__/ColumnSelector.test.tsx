import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ColumnSelector, type ColumnSpec } from "../ColumnSelector";

afterEach(() => cleanup());

const CATALOG: ColumnSpec[] = [
  { key: "ticker", label: "Ticker", category: "Identity" },
  { key: "qty", label: "Qty", category: "Trade" },
];

/**
 * Regression test for the clipping bug found 2026-07-03: a trigger
 * button positioned near the LEFT edge of a scrollable container
 * (TradeLogTable's ColumnSelector, paired with a right-aligned
 * download button via justify-between) had its popover clipped,
 * because the default right-0 anchor expands the fixed-width
 * popover LEFTWARD — overflowing past `<main
 * className="overflow-y-auto">`'s implicit horizontal clip
 * boundary. `align="left"` anchors the popover's left edge to the
 * button instead, expanding rightward into open table space.
 */
describe("ColumnSelector — popover anchor alignment", () => {
  it("anchors right-0 by default (safe for a right-positioned trigger)", () => {
    render(
      <ColumnSelector catalog={CATALOG} selected={["ticker"]} onChange={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId("column-selector-trigger"));
    const popover = screen.getByTestId("column-selector-popover");
    expect(popover.className).toContain("right-0");
    expect(popover.className).not.toContain("left-0");
  });

  it("anchors left-0 when align=\"left\" (safe for a left-positioned trigger)", () => {
    render(
      <ColumnSelector
        catalog={CATALOG}
        selected={["ticker"]}
        onChange={vi.fn()}
        align="left"
      />,
    );
    fireEvent.click(screen.getByTestId("column-selector-trigger"));
    const popover = screen.getByTestId("column-selector-popover");
    expect(popover.className).toContain("left-0");
    expect(popover.className).not.toContain("right-0");
  });
});
