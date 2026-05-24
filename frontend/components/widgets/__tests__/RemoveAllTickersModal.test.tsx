import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { RemoveAllTickersModal } from "../RemoveAllTickersModal";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/apiFetch";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RemoveAllTickersModal", () => {
  it("Confirm button disabled until exact phrase typed", () => {
    render(
      <RemoveAllTickersModal
        currentCount={42}
        onClose={vi.fn()}
        onRemoved={vi.fn()}
      />,
    );
    const btn = screen.getByTestId(
      "remove-all-tickers-confirm-button",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);

    const input = screen.getByTestId(
      "remove-all-tickers-input",
    );
    // Wrong case — still disabled.
    fireEvent.change(input, {
      target: { value: "remove all" },
    });
    expect(btn.disabled).toBe(true);

    // Exact phrase — enabled.
    fireEvent.change(input, {
      target: { value: "REMOVE ALL" },
    });
    expect(btn.disabled).toBe(false);
  });

  it("posts DELETE and calls onRemoved on success", async () => {
    (apiFetch as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        ok: true,
        json: async () => ({ removed: 5 }),
      });
    const onRemoved = vi.fn();
    const onClose = vi.fn();
    render(
      <RemoveAllTickersModal
        currentCount={5}
        onClose={onClose}
        onRemoved={onRemoved}
      />,
    );
    fireEvent.change(
      screen.getByTestId("remove-all-tickers-input"),
      { target: { value: "REMOVE ALL" } },
    );
    fireEvent.click(
      screen.getByTestId(
        "remove-all-tickers-confirm-button",
      ),
    );
    await waitFor(() => {
      expect(onRemoved).toHaveBeenCalledOnce();
      expect(onClose).toHaveBeenCalledOnce();
    });
  });
});
