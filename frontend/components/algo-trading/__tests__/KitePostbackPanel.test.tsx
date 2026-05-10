// frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
/**
 * Vitest unit tests for KitePostbackPanel (OBS-4).
 *
 * Tests (added task-by-task):
 *  Task 3: loading skeleton renders text for FCP.
 *  Task 4: empty state renders amber troubleshooting card.
 *  Task 5: populated state renders postback rows.
 *  Task 6: status badge colour classes.
 *  Task 7: payload toggle expand / collapse; single-row-at-a-time.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

afterEach(() => cleanup());

vi.mock("@/hooks/useKitePostbacks", () => ({
  useKitePostbacks: vi.fn(),
}));

import { useKitePostbacks } from "@/hooks/useKitePostbacks";
import { KitePostbackPanel } from "../KitePostbackPanel";

const mockHook = useKitePostbacks as ReturnType<typeof vi.fn>;

// ── Task 3 ──────────────────────────────────────────────────
describe("KitePostbackPanel — loading state", () => {
  it("renders text while loading so Lighthouse FCP fires", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: true,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    // Must contain a text node — not just a CSS shimmer div.
    expect(screen.getByText(/loading postbacks/i)).toBeTruthy();
  });
});

// ── Task 4 ──────────────────────────────────────────────────
describe("KitePostbackPanel — empty state", () => {
  it("renders amber troubleshooting card when postbacks list is empty", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const card = screen.getByTestId("kite-postback-empty-state");
    expect(card).toBeTruthy();
    // Verbatim text per spec §3.6.
    expect(card.textContent).toContain("No postbacks received");
    expect(card.textContent).toContain("KITE_POSTBACK_ENABLED");
    expect(card.textContent).toContain("http://localhost:4040");
  });

  it("empty state card has amber border class", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const card = screen.getByTestId("kite-postback-empty-state");
    expect(card.className).toContain("border-amber-300");
  });

  it("does NOT render empty state while still loading", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: true,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(
      screen.queryByTestId("kite-postback-empty-state"),
    ).toBeNull();
  });
});
