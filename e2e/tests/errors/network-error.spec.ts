/**
 * E2E tests for network error handling.
 *
 * Uses Playwright's route() to simulate failures.
 */

import { test, expect } from "@playwright/test";

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
        body: JSON.stringify({
          detail: "Internal server error",
        }),
      }),
    );

    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });

    // Send a message — will use HTTP fallback (WS blocked)
    await page.getByTestId("chat-message-input").fill("test");
    await page.getByTestId("chat-send-button").click();

    // Should show some error indication
    const errorIndicator = page
      .locator("text=error")
      .or(page.getByTestId("status-badge"));
    await expect(errorIndicator.first()).toBeVisible({
      timeout: 10_000,
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
    // Page should still be responsive after error processing
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 10_000 });
  });
});
