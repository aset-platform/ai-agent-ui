import { afterEach, describe, expect, it, vi } from "vitest";

import { triggerCsvDownload } from "../triggerCsvDownload";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(async () => ({
    ok: true,
    status: 200,
    blob: async () => new Blob(["a,b\n1,2\n"], { type: "text/csv" }),
    headers: new Headers({
      "Content-Disposition": 'attachment; filename="x.csv"',
    }),
  })),
}));

afterEach(() => vi.clearAllMocks());

describe("triggerCsvDownload", () => {
  it("uses the filename from Content-Disposition", async () => {
    const createObjUrl = vi.fn(() => "blob:url");
    const revokeObjUrl = vi.fn();
    Object.assign(URL, {
      createObjectURL: createObjUrl,
      revokeObjectURL: revokeObjUrl,
    });
    const click = vi.fn();
    const remove = vi.fn();
    vi.spyOn(document, "createElement").mockImplementation(
      () => ({ href: "", download: "", click, remove }) as unknown as HTMLAnchorElement,
    );
    vi.spyOn(document.body, "appendChild").mockImplementation(
      (n) => n,
    );
    await triggerCsvDownload("/v1/advanced-analytics/foo/export");
    expect(click).toHaveBeenCalled();
    expect(revokeObjUrl).toHaveBeenCalledWith("blob:url");
  });

  it("throws on non-ok response", async () => {
    const { apiFetch } = await import("@/lib/apiFetch");
    (apiFetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 413,
      json: async () => ({ detail: "Export exceeds 10,000 rows" }),
    });
    await expect(
      triggerCsvDownload("/v1/advanced-analytics/foo/export"),
    ).rejects.toThrow(/413/);
  });
});
