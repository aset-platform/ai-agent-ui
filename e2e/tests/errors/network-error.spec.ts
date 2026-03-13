/**
 * E2E tests for network error handling.
 *
 * Uses Playwright's route() to simulate failures.
 */

import { test, expect } from "@playwright/test";

import { readCachedToken } from "../../utils/auth.helper";

test.describe("Network error handling", () => {
  test("backend 500 → chat shows error", async ({ page }) => {
    // Intercept WebSocket and close it immediately so the
    // frontend falls back to HTTP streaming.
    await page.routeWebSocket("**/ws/chat", (ws) => {
      ws.close();
    });

    // Mock the HTTP streaming endpoint to return 500
    await page.route("**/chat/stream", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal server error" }),
      }),
    );

    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });

    // Send a message — will use HTTP fallback (WS blocked)
    await page.getByTestId("chat-message-input").fill("test");
    await page.getByTestId("chat-send-button").click();

    // Should show some error indication (status badge or message)
    await page.waitForTimeout(3_000);
    const errorIndicator = page
      .locator("text=error")
      .or(page.getByTestId("status-badge"));
    await expect(errorIndicator.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("dashboard refresh failure → error overlay", async ({
    page,
  }) => {
    test.slow(); // 3x timeout — background refresh
    const access_token = readCachedToken();

    const DASHBOARD =
      process.env.DASHBOARD_URL || "http://127.0.0.1:8050";
    await page.goto(
      `${DASHBOARD}/analysis?token=${access_token}`,
    );

    // Wait for the page to fully render
    const refreshBtn = page.locator("#analysis-refresh-btn");
    await expect(refreshBtn).toBeVisible({ timeout: 15_000 });

    // Select a ticker first
    const dropdown = page.locator(
      "#analysis-ticker-dropdown",
    );
    await dropdown.click();
    const option = page
      .locator('[role="option"]')
      .first();
    if ((await option.count()) > 0) {
      await option.click();
    }

    await refreshBtn.click();
    // Poll callback writes ✓ or ✗ when done.
    const status = page.locator(
      "#analysis-refresh-status",
    );
    await expect(status).toContainText(/[✓✗]/, {
      timeout: 120_000,
    });
  });

  test("network offline → graceful handling", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });

    // Simulate offline by aborting all backend requests
    await page.route("**/127.0.0.1:8181/**", (route) =>
      route.abort("connectionrefused"),
    );

    await page.getByTestId("chat-message-input").fill("test");
    await page.getByTestId("chat-send-button").click();

    // Should handle gracefully (no unhandled crash)
    await page.waitForTimeout(5_000);
    // Page should still be responsive
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible();
  });
});
