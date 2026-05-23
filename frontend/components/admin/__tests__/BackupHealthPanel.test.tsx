import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { BackupHealthPanel } from "../BackupHealthPanel";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/apiFetch";

describe("BackupHealthPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders warehouse_size_mb on SIZE tile", async () => {
    (apiFetch as ReturnType<typeof vi.fn>).mockImplementation(
      (url: string) => {
        if (url.endsWith("/admin/backups/health")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                status: "healthy",
                latest_date: "2026-05-23",
                completed_at: "2026-05-23T00:30:00Z",
                age_hours: 5.2,
                backup_count: 2,
                table_count: 27,
                warehouse_size_mb: 2347.6,
                has_catalog: true,
              }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ backups: [] }),
        });
      },
    );

    render(<BackupHealthPanel />);

    // SIZE tile should display ~2.3 GB (auto-converts from MB)
    await waitFor(() => {
      expect(screen.getByText(/2\.3 GB/)).toBeDefined();
    });
    // TABLES tile new in this PR
    expect(screen.getByText("27")).toBeDefined();
    expect(screen.getByText(/Tables/i)).toBeDefined();
  });
});
