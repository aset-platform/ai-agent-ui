/**
 * LiveDryRunBanner — unit tests.
 *
 * Verifies:
 * 1. Banner renders when dry_run=true.
 * 2. Banner is absent when dry_run=false.
 * 3. Banner is absent when gates is null (data not yet loaded).
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { LiveDryRunBanner } from "../LiveDryRunBanner";
import type { GatesStatus } from "@/hooks/useLiveStatus";

const FULL_GATES_DRY: GatesStatus = {
  kite_connected: true,
  caps_set: true,
  kill_switch_disarmed: true,
  walkforward_recent: true,
  drift_within_limit: true,
  all_pass: true,
  live_orders_enabled: true,
  dry_run: true,
};

const FULL_GATES_REAL: GatesStatus = {
  ...FULL_GATES_DRY,
  dry_run: false,
};

describe("LiveDryRunBanner", () => {
  it("renders banner when dry_run=true", () => {
    render(<LiveDryRunBanner gates={FULL_GATES_DRY} />);
    const banner = screen.getByTestId("live-dry-run-banner");
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("DRY RUN MODE");
    expect(banner.textContent).toContain("ALGO_LIVE_DRY_RUN=false");
  });

  it("does not render banner when dry_run=false", () => {
    render(<LiveDryRunBanner gates={FULL_GATES_REAL} />);
    expect(
      screen.queryByTestId("live-dry-run-banner"),
    ).toBeNull();
  });

  it("does not render banner when gates is null", () => {
    render(<LiveDryRunBanner gates={null} />);
    expect(
      screen.queryByTestId("live-dry-run-banner"),
    ).toBeNull();
  });
});
