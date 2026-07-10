/**
 * TickerChip — unit tests.
 *
 * Verifies:
 * 1. Normal chip renders the ticker with no warning styling.
 * 2. offUniverse=true renders the amber/red warning affordance +
 *    tooltip, mirroring the FeatureChip "unwired" convention.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { TickerChip } from "../LiveSafetyBeltsForm";

describe("TickerChip", () => {
  it("renders a plain chip with no warning for an in-universe ticker", () => {
    render(
      <TickerChip ticker="ITC.NS" offUniverse={false} onRemove={vi.fn()} />,
    );
    const chip = screen.getByText("ITC.NS");
    expect(chip).toBeDefined();
    expect(screen.queryByText("⚠")).toBeNull();
  });

  it("renders a warning affordance for an off-universe ticker", () => {
    render(
      <TickerChip
        ticker="MOVALUE.NS"
        offUniverse={true}
        onRemove={vi.fn()}
      />,
    );
    const chip = screen.getByTestId("live-caps-ticker-MOVALUE.NS");
    expect(chip.title).toContain("liquidity");
  });
});
