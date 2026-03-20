/**
 * Unit tests for the ConfirmDialog component.
 *
 * Uses @testing-library/react for rendering and interaction.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
} from "@testing-library/react";
import { ConfirmDialog } from "@/components/ConfirmDialog";

// -----------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------

const baseProps = {
  open: true,
  title: "Delete item",
  message: "Are you sure you want to delete?",
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

// -----------------------------------------------------------------
// Tests
// -----------------------------------------------------------------

describe("ConfirmDialog", () => {
  afterEach(() => cleanup());

  it("does not render when open=false", () => {
    const { container } = render(
      <ConfirmDialog {...baseProps} open={false} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders title and message when open", () => {
    render(<ConfirmDialog {...baseProps} />);
    expect(screen.getByText("Delete item")).toBeDefined();
    expect(
      screen.getByText("Are you sure you want to delete?"),
    ).toBeDefined();
  });

  it("calls onConfirm when confirm button clicked", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog {...baseProps} onConfirm={onConfirm} />,
    );
    fireEvent.click(screen.getByText("Delete"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when cancel button clicked", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog {...baseProps} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByText("Cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("uses danger variant colors by default", () => {
    render(<ConfirmDialog {...baseProps} />);
    const confirmBtn = screen.getByText("Delete");
    expect(confirmBtn.className).toContain("bg-red-600");
  });

  it("uses warning variant colors when specified", () => {
    render(
      <ConfirmDialog {...baseProps} variant="warning" />,
    );
    const confirmBtn = screen.getByText("Delete");
    expect(confirmBtn.className).toContain("bg-amber-500");
  });

  it("renders custom confirm and cancel labels", () => {
    render(
      <ConfirmDialog
        {...baseProps}
        confirmLabel="Yes, remove"
        cancelLabel="Go back"
      />,
    );
    expect(screen.getByText("Yes, remove")).toBeDefined();
    expect(screen.getByText("Go back")).toBeDefined();
  });
});
