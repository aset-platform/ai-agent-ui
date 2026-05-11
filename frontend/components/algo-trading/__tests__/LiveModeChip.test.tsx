/**
 * LiveModeChip — variant chip for header strip.
 * Slice 4 of three-page split.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { LiveModeChip } from "../live/LiveModeChip";

afterEach(() => {
  cleanup();
});

describe("LiveModeChip", () => {
  it("LIVE ARMED renders with rose-600 background", () => {
    render(<LiveModeChip mode="live" armed={true} />);
    const chip = screen.getByTestId("live-mode-chip");
    expect(chip.textContent ?? "").toMatch(/live armed/i);
    expect(chip.className).toContain("bg-rose-600");
  });

  it("LIVE DISARMED renders with slate-400 background", () => {
    render(<LiveModeChip mode="live" armed={false} />);
    const chip = screen.getByTestId("live-mode-chip");
    expect(chip.textContent ?? "").toMatch(/disarmed/i);
    expect(chip.className).toContain("bg-slate-400");
  });

  it("DRY-RUN renders with amber-500 background", () => {
    render(<LiveModeChip mode="dry_run" armed={false} />);
    const chip = screen.getByTestId("live-mode-chip");
    expect(chip.textContent ?? "").toMatch(/dry/i);
    expect(chip.className).toContain("bg-amber-500");
  });
});
