import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/apiFetch";
import { useAddToWatchlist } from "../useAddToWatchlist";

beforeEach(() => vi.clearAllMocks());

describe("useAddToWatchlist", () => {
  it("posts tickers and returns the bulk response", async () => {
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({
        added: ["TCS.NS"], skipped_already_linked: [],
        errors: [], total_rows: 1,
      }),
    });
    const { result } = renderHook(() => useAddToWatchlist());
    let resp: { added: string[] } | undefined;
    await act(async () => {
      resp = await result.current.submit(["TCS.NS"]);
    });
    expect(apiFetch).toHaveBeenCalledWith(
      expect.stringContaining("/users/me/tickers/bulk-add"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(resp?.added).toEqual(["TCS.NS"]);
  });

  it("sets error on non-ok response", async () => {
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false, status: 400, text: async () => "tickers list is empty",
    });
    const { result } = renderHook(() => useAddToWatchlist());
    await act(async () => {
      await result.current.submit([]).catch(() => {});
    });
    expect(result.current.error).toContain("400");
  });
});
