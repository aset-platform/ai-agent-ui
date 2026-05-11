/**
 * PanicCloseButton — typed-confirm gate.
 * Slice 4 of three-page split.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

import { PanicCloseButton } from "../live/PanicCloseButton";

afterEach(() => {
  cleanup();
});

describe("PanicCloseButton", () => {
  it("opens confirm modal on click", () => {
    render(<PanicCloseButton onConfirm={() => {}} />);
    fireEvent.click(screen.getByTestId("panic-close-button"));
    const modal = screen.getByTestId("panic-close-modal");
    expect(modal.textContent ?? "").toMatch(/close all open positions/i);
  });

  it("Close-all confirm button stays disabled until PANIC typed", () => {
    const onConfirm = vi.fn();
    render(<PanicCloseButton onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId("panic-close-button"));

    const confirm = screen.getByTestId(
      "panic-close-confirm",
    ) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    // Typing the wrong text keeps it disabled
    fireEvent.change(screen.getByTestId("panic-close-input"), {
      target: { value: "panic" }, // lowercase fails
    });
    expect(confirm.disabled).toBe(true);

    // Typing exact PANIC enables it; clicking fires onConfirm once
    fireEvent.change(screen.getByTestId("panic-close-input"), {
      target: { value: "PANIC" },
    });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
