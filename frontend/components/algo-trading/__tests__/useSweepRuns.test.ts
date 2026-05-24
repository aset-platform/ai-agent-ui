import { describe, it, expect, vi } from "vitest";
import { startSweepRun } from "@/hooks/useSweepRuns";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      sweep_run_id: "abc-123",
    }),
  }),
}));

describe("startSweepRun", () => {
  it("POSTs config and returns sweep_run_id", async () => {
    const out = await startSweepRun({
      base_strategy_id: "stub",
      period_start: "2025-01-01",
      period_end: "2025-06-01",
      swept_field: "cooldown_days",
      swept_values: [3, 7],
    });
    expect(out.sweep_run_id).toBe("abc-123");
  });
});
