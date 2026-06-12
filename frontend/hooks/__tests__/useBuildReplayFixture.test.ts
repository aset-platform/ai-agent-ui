import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/apiFetch";
import { useBuildReplayFixture } from "../useBuildReplayFixture";

beforeEach(() => vi.clearAllMocks());

describe("useBuildReplayFixture", () => {
  it("posts and returns the build result", async () => {
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({
        filename: "u1.jsonl", n_tickers: 8, n_trigger_dates: 14,
        n_ticks: 84, trigger_tickers: ["TCS.NS"],
      }),
    });
    const { result } = renderHook(() => useBuildReplayFixture());
    let res: { filename: string } | undefined;
    await act(async () => { res = await result.current.submit(); });
    expect(apiFetch).toHaveBeenCalledWith(
      expect.stringContaining("/algo/paper/fixtures/build"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(res?.filename).toBe("u1.jsonl");
  });

  it("sets error on non-ok response", async () => {
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false, status: 400, text: async () => "empty universe",
    });
    const { result } = renderHook(() => useBuildReplayFixture());
    await act(async () => {
      await result.current.submit().catch(() => {});
    });
    expect(result.current.error).toContain("400");
  });
});
