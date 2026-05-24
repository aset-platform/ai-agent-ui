import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect } from "vitest";
import { SweepPboBadge } from "../SweepPboBadge";

function mkRun(pbo: string | null) {
  return {
    run_id: "x",
    base_strategy_id: "y",
    swept_field: "cooldown_days",
    swept_values: [3, 7],
    variants: [],
    cross_variant_pbo: pbo,
    returns_matrix_shape: [100, 2] as [number, number],
    winner_variant_index: null,
    started_at: "",
    completed_at: null,
    status: "completed" as const,
  };
}

afterEach(() => cleanup());

describe("SweepPboBadge", () => {
  it("shows ROBUST verdict when PBO ≤ 0.30", () => {
    render(<SweepPboBadge run={mkRun("0.20")} />);
    expect(screen.getByText(/ROBUST/)).toBeDefined();
  });

  it("shows AT-RISK verdict when 0.30 < PBO ≤ 0.50", () => {
    render(<SweepPboBadge run={mkRun("0.40")} />);
    expect(screen.getByText(/AT-RISK/)).toBeDefined();
  });

  it("shows LIKELY OVERFIT verdict when PBO > 0.50", () => {
    render(<SweepPboBadge run={mkRun("0.70")} />);
    expect(screen.getByText(/LIKELY OVERFIT/))
      .toBeDefined();
  });

  it("shows N/A when PBO is null", () => {
    render(<SweepPboBadge run={mkRun(null)} />);
    expect(
      screen.getByText(/too few common days or variants/),
    ).toBeDefined();
  });
});
