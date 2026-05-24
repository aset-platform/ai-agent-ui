import { describe, it, expect, vi } from "vitest";
import { setAllocation } from "@/hooks/useBudget";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      allocated_inr: "100000",
      enabled: true,
    }),
  }),
}));

describe("setAllocation", () => {
  it("PUTs the allocation and returns the updated budget", async () => {
    const out = await setAllocation("100000");
    expect(out.allocated_inr).toBe("100000");
    expect(out.enabled).toBe(true);
  });
});
