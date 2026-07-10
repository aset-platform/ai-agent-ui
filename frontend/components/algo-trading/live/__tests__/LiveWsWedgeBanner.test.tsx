/**
 * LiveWsWedgeBanner — unit tests (ASETPLTFRM-470).
 *
 * Verifies:
 * 1. Banner renders when armed AND wedgeEscalated.
 * 2. Banner is absent when armed=false, even if wedgeEscalated=true.
 * 3. Banner is absent when wedgeEscalated=false, even if armed=true.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { LiveWsWedgeBanner } from "../LiveWsWedgeBanner";

afterEach(cleanup);

describe("LiveWsWedgeBanner", () => {
  it("renders when armed and wedgeEscalated are both true", () => {
    render(
      <LiveWsWedgeBanner armed={true} wedgeEscalated={true} />,
    );
    const banner = screen.getByTestId("live-ws-wedge-banner");
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("WEDGED");
  });

  it("does not render when armed is false", () => {
    render(
      <LiveWsWedgeBanner armed={false} wedgeEscalated={true} />,
    );
    expect(
      screen.queryByTestId("live-ws-wedge-banner"),
    ).toBeNull();
  });

  it("does not render when wedgeEscalated is false", () => {
    render(
      <LiveWsWedgeBanner armed={true} wedgeEscalated={false} />,
    );
    expect(
      screen.queryByTestId("live-ws-wedge-banner"),
    ).toBeNull();
  });
});
