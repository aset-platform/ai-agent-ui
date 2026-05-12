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

// Order-Safety PR #1 — Submissions tab also reads from this hook.
// Default it to an empty-list mock so the existing Postbacks tests
// keep their narrow scope.
vi.mock("@/hooks/useOrderSubmissions", () => ({
  useOrderSubmissions: vi.fn().mockReturnValue({
    submissions: [],
    isLoading: false,
    error: null,
    mutate: () => undefined,
  }),
}));

import { useKitePostbacks } from "@/hooks/useKitePostbacks";
import { KitePostbackPanel } from "../KitePostbackPanel";

const mockHook = useKitePostbacks as ReturnType<typeof vi.fn>;

/** PR #1 added a tab strip — every Postbacks test needs to click
 *  the Postbacks tab first because Submissions is the default. */
function activatePostbacksTab() {
  const tab = screen.getByTestId("kite-postback-tab-postbacks");
  fireEvent.click(tab);
}

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
    activatePostbacksTab();

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
      activatePostbacksTab();

      const badge = screen.getByText(status);
      expect(badge.className).toContain(cls);
    });
  });
});


// ── Task 7 ──────────────────────────────────────────────────
describe("KitePostbackPanel — payload toggle", () => {
  const TWO = [
    {
      event_ts: "2026-05-10T09:30:00Z",
      tradingsymbol: "RELIANCE.NS",
      status: "COMPLETE",
      filled_quantity: 5,
      average_price: 2950.75,
      raw: { order_id: "111", guid: "aaa" },
    },
    {
      event_ts: "2026-05-10T09:25:00Z",
      tradingsymbol: "INFY.NS",
      status: "COMPLETE",
      filled_quantity: 3,
      average_price: 1500.5,
      raw: { order_id: "222", guid: "bbb" },
    },
  ];

  it("payload is hidden before any toggle click", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);
    activatePostbacksTab();

    // "111" is only in the raw JSON — should not be visible initially.
    expect(screen.queryByText(/"111"/)).toBeNull();
  });

  it("clicking arrow expands the raw JSON payload", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);
    activatePostbacksTab();

    const toggles = screen.getAllByTestId(
      "kite-postback-payload-toggle",
    );
    fireEvent.click(toggles[0]);

    // The raw JSON for row 0 should now be visible.
    expect(screen.getByText(/"111"/)).toBeTruthy();
  });

  it("clicking toggle twice collapses the payload", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);
    activatePostbacksTab();

    const toggles = screen.getAllByTestId(
      "kite-postback-payload-toggle",
    );
    fireEvent.click(toggles[0]);
    expect(screen.getByText(/"111"/)).toBeTruthy();

    fireEvent.click(toggles[0]);
    expect(screen.queryByText(/"111"/)).toBeNull();
  });

  it("expanding row 1 collapses row 0 (single-row-at-a-time)", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);
    activatePostbacksTab();

    const toggles = screen.getAllByTestId(
      "kite-postback-payload-toggle",
    );

    // Expand row 0.
    fireEvent.click(toggles[0]);
    expect(screen.getByText(/"111"/)).toBeTruthy();
    expect(screen.queryByText(/"222"/)).toBeNull();

    // Expand row 1 — row 0 should collapse.
    fireEvent.click(toggles[1]);
    expect(screen.queryByText(/"111"/)).toBeNull();
    expect(screen.getByText(/"222"/)).toBeTruthy();
  });
});
