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

// ── Task 5 ──────────────────────────────────────────────────
const TWO_POSTBACKS = [
  {
    event_ts: "2026-05-10T09:30:00Z",
    tradingsymbol: "RELIANCE.NS",
    status: "COMPLETE",
    filled_quantity: 5,
    average_price: 2950.75,
    raw: { order_id: "111", guid: "a" },
  },
  {
    event_ts: "2026-05-10T09:25:00Z",
    tradingsymbol: "INFY.NS",
    status: "REJECTED",
    filled_quantity: 0,
    average_price: 0,
    raw: { order_id: "222", guid: "b" },
  },
];

describe("KitePostbackPanel — populated state", () => {
  it("renders one row per postback", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const rows = screen.getAllByTestId("kite-postback-row");
    expect(rows).toHaveLength(2);
  });

  it("renders the symbol in the first row", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(screen.getByText("RELIANCE.NS")).toBeTruthy();
    expect(screen.getByText("INFY.NS")).toBeTruthy();
  });

  it("renders avg price formatted with ₹ symbol", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(screen.getByText("₹2950.75")).toBeTruthy();
  });

  it("shows — for avg price when average_price is 0", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(screen.getByText("—")).toBeTruthy();
  });

  it("does not render empty state when postbacks present", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(
      screen.queryByTestId("kite-postback-empty-state"),
    ).toBeNull();
  });

  it("shows postback count in the panel header", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    // Header should contain "(2)" count.
    const panel = screen.getByTestId("kite-postback-panel");
    expect(panel.textContent).toContain("(2)");
  });
});

// ── Task 6 ──────────────────────────────────────────────────
describe("KitePostbackPanel — status badge colours", () => {
  const statusCases: Array<{
    status: string;
    cls: string;
    label: string;
  }> = [
    { status: "COMPLETE", cls: "bg-emerald-100", label: "green for COMPLETE" },
    { status: "REJECTED", cls: "bg-rose-100", label: "red for REJECTED" },
    { status: "CANCELLED", cls: "bg-slate-100", label: "gray for CANCELLED" },
    { status: "UPDATE", cls: "bg-blue-100", label: "blue for UPDATE" },
  ];

  statusCases.forEach(({ status, cls, label }) => {
    it(`applies ${label}`, () => {
      mockHook.mockReturnValue({
        postbacks: [
          {
            event_ts: "2026-05-10T09:30:00Z",
            tradingsymbol: "TEST.NS",
            status,
            filled_quantity: 1,
            average_price: 100,
            raw: { order_id: "x" },
          },
        ],
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      });

      render(<KitePostbackPanel />);

      const badge = screen.getByText(status);
      expect(badge.className).toContain(cls);
    });
  });
});
