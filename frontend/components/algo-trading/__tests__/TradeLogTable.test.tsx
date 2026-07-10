/**
 * TradeLogTable — ExitReasonBadge label coverage.
 *
 * The Live/Paper closed-trades rollup now emits exit_reason values
 * panic_close (forced flatten) and gtt_triggered (GTT/trailing stop);
 * the badge must render human labels for them, not the raw snake_case
 * key (ASETPLTFRM-470 follow-up).
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TradeLogTable } from "../TradeLogTable";
import type { TradeRow } from "@/hooks/useBacktestRuns";

function row(exit_reason: string): TradeRow {
  return {
    ticker: "ADANIGREEN",
    qty: 2,
    avg_price: "1485.30",
    fill_price: "1532.10",
    opened_at: "2026-07-09",
    closed_at: "2026-07-10",
    holding_days: 1,
    realised_pnl_inr: "93.60",
    return_pct: "3.15",
    exit_reason,
  };
}

afterEach(() => {
  cleanup();
});

describe("TradeLogTable ExitReasonBadge", () => {
  it("renders a human label for panic_close, not the raw key", () => {
    render(<TradeLogTable rows={[row("panic_close")]} />);
    expect(screen.getByText("Panic close")).toBeDefined();
    expect(screen.queryByText("panic_close")).toBeNull();
  });

  it("renders a human label for gtt_triggered, not the raw key", () => {
    render(<TradeLogTable rows={[row("gtt_triggered")]} />);
    expect(screen.getByText("GTT stop")).toBeDefined();
    expect(screen.queryByText("gtt_triggered")).toBeNull();
  });
});
