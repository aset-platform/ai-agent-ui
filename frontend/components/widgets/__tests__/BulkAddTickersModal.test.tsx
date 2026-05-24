import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { BulkAddTickersModal } from "../BulkAddTickersModal";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/apiFetch";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BulkAddTickersModal", () => {
  it("renders drop zone; Upload button disabled until file selected", () => {
    render(
      <BulkAddTickersModal
        onClose={vi.fn()}
        onUploaded={vi.fn()}
      />,
    );
    const uploadBtn = screen.getByTestId(
      "bulk-add-tickers-upload-button",
    );
    expect(
      (uploadBtn as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("posts multipart form and renders result view", async () => {
    (apiFetch as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          added: ["AAPL"],
          skipped_already_linked: [],
          errors: [],
          total_rows: 1,
        }),
      });

    const onUploaded = vi.fn();
    render(
      <BulkAddTickersModal
        onClose={vi.fn()}
        onUploaded={onUploaded}
      />,
    );

    const input = screen.getByTestId(
      "bulk-add-tickers-file-input",
    ) as HTMLInputElement;
    const file = new File(
      ["ticker\nAAPL\n"], "test.csv",
      { type: "text/csv" },
    );
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(
      screen.getByTestId("bulk-add-tickers-upload-button"),
    );

    await waitFor(() => {
      expect(
        screen.getByTestId(
          "bulk-add-tickers-result-added-count",
        ),
      ).toBeDefined();
    });
    expect(onUploaded).toHaveBeenCalledOnce();
  });

  it("renders first 100 errors with truncation tail", async () => {
    const errors = Array.from({ length: 150 }).map(
      (_, i) => ({
        row: i + 2,
        ticker: `BAD${i}`,
        reason: "invalid format",
      }),
    );
    (apiFetch as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          added: [],
          skipped_already_linked: [],
          errors,
          total_rows: 150,
        }),
      });

    render(
      <BulkAddTickersModal
        onClose={vi.fn()}
        onUploaded={vi.fn()}
      />,
    );

    const input = screen.getByTestId(
      "bulk-add-tickers-file-input",
    ) as HTMLInputElement;
    const file = new File(
      ["ticker\nBAD0\n"], "test.csv",
    );
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(
      screen.getByTestId("bulk-add-tickers-upload-button"),
    );

    await waitFor(() => {
      const list = screen.getByTestId(
        "bulk-add-tickers-result-errors-list",
      );
      expect(list.textContent).toContain("BAD0");
      expect(list.textContent).toContain("50 more");
    });
  });
});
