/**
 * E2E tests for Portfolio CRUD operations (Gap 1).
 *
 * Tests add, edit, and delete stock flows via the dashboard
 * watchlist.  Uses the portfolio fixture for pre-seeded
 * holdings and the general user auth.
 */

import {
  test,
  expect,
} from "../../fixtures/portfolio.fixture";
import { PortfolioCrudPage } from "../../pages/frontend/portfolio-crud.page";
import { waitForPageReady } from "../../utils/wait.helper";
import {
  apiDeletePortfolioHolding,
  apiAddPortfolioHolding,
} from "../../utils/api.helper";
import { request as pwRequest } from "@playwright/test";

/** Ticker used for add/delete tests — not in seeded set. */
const TEST_TICKER = "INFY.NS";
const TEST_QTY = 12;
const TEST_PRICE = 1500;
const TEST_DATE = "2025-01-15";

test.describe("Portfolio CRUD", () => {
  let portfolio: PortfolioCrudPage;

  test.beforeEach(async ({ page, seededPortfolio }) => {
    void seededPortfolio; // ensure fixture runs
    portfolio = new PortfolioCrudPage(page);
    await portfolio.gotoDashboard();
    // Wait for sidebar (dashboard rendered)
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
    // Wait for WatchlistWidget to appear in DOM, then
    // scroll to it. It's far below the fold on the
    // dashboard page.
    const addBtn = page.getByTestId(
      "dashboard-add-stock-btn",
    );
    await addBtn.waitFor({
      state: "attached",
      timeout: 30_000,
    });
    await addBtn.scrollIntoViewIfNeeded();
    await expect(addBtn).toBeVisible({
      timeout: 5_000,
    });
  });

  // ── Add Stock ───────────────────────────────────────

  test("add stock modal opens from dashboard add-stock button", async () => {
    await portfolio.openAddStockModal();
    const modal = portfolio.addStockModal();
    await expect(modal).toBeVisible({ timeout: 5_000 });
  });

  test("add stock with valid data creates holding in watchlist", async ({
    page,
    userToken,
  }) => {
    // Clean up the test ticker first in case of leftover
    const ctx = await pwRequest.newContext();
    await apiDeletePortfolioHolding(
      ctx,
      userToken,
      TEST_TICKER,
    ).catch(() => {
      // May not exist — that's fine.
    });
    await ctx.dispose();

    await portfolio.openAddStockModal();
    await portfolio.fillAddStockForm(
      TEST_TICKER,
      TEST_QTY,
      TEST_PRICE,
      TEST_DATE,
    );
    await portfolio.submitAddStock();

    // Wait for modal to close and watchlist to update
    await page.waitForTimeout(2_000);
    await page.waitForTimeout(2_000);

    // Verify the ticker appears in the watchlist
    const row = portfolio.isTickerVisible(TEST_TICKER);
    await expect(row).toBeVisible({ timeout: 10_000 });

    // Cleanup: remove the added holding
    const cleanup = await pwRequest.newContext();
    await apiDeletePortfolioHolding(
      cleanup,
      userToken,
      TEST_TICKER,
    ).catch(() => {
      // Best-effort cleanup
    });
    await cleanup.dispose();
  });

  test("add stock validation rejects zero quantity", async () => {
    await portfolio.openAddStockModal();
    await portfolio.fillAddStockForm(
      TEST_TICKER,
      0,
      TEST_PRICE,
      TEST_DATE,
    );
    await portfolio.submitAddStock();

    // Modal should remain open with an error message
    const modal = portfolio.addStockModal();
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Either a validation error or the form stays open
    const error = portfolio.addStockError();
    const modalStillOpen = await modal.isVisible();
    expect(modalStillOpen).toBe(true);

    // Check for validation error text if present
    if (await error.isVisible()) {
      const text = await error.textContent();
      expect(text?.toLowerCase()).toMatch(
        /quantity|invalid|greater|zero|positive/,
      );
    }
  });

  // ── Edit Stock ──────────────────────────────────────

  test("edit stock modal opens with current values pre-filled", async () => {
    // Use a seeded ticker
    const ticker = "RELIANCE.NS";
    await portfolio.openEditModal(ticker);

    const modal = portfolio.editStockModal();
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Quantity input should have a non-empty value
    const qtyInput = portfolio
      .editStockModal()
      .getByTestId("edit-stock-quantity");
    const qtyValue = await qtyInput.inputValue();
    expect(qtyValue).not.toBe("");
    expect(Number(qtyValue)).toBeGreaterThan(0);
  });

  test("edit stock updates quantity in watchlist", async ({
    page,
  }) => {
    const ticker = "RELIANCE.NS";
    const newQty = 25;

    await portfolio.openEditModal(ticker);
    await portfolio.fillEditForm(newQty, 2450);
    await portfolio.submitEdit();

    // Wait for modal to close and watchlist to update
    await page.waitForTimeout(2_000);
    await page.waitForTimeout(2_000);

    // The row should still be visible
    const row = portfolio.isTickerVisible(ticker);
    await expect(row).toBeVisible({ timeout: 10_000 });

    // Verify quantity updated (check row text contains new qty)
    const rowText = await row.textContent();
    expect(rowText).toContain(String(newQty));
  });

  // ── Delete Stock ────────────────────────────────────

  test("delete stock shows ConfirmDialog with ticker name", async () => {
    const ticker = "BEL.NS";
    await portfolio.clickDeleteBtn(ticker);

    const dialog = portfolio.confirmDialog();
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Dialog text should reference the ticker
    const dialogText = await dialog.textContent();
    expect(dialogText).toContain(ticker);
  });

  test("delete stock confirm removes row from watchlist", async ({
    page,
    userToken,
  }) => {
    // Seed a disposable holding for this test
    const disposableTicker = "BPCL.NS";
    const ctx = await pwRequest.newContext();
    await apiAddPortfolioHolding(
      ctx,
      userToken,
      disposableTicker,
      5,
      350,
    ).catch(() => {
      // May already exist from fixture
    });
    await ctx.dispose();

    // Reload to ensure the holding is visible
    await page.reload();
    await page.waitForTimeout(2_000);

    const rowBefore = portfolio.isTickerVisible(
      disposableTicker,
    );
    await expect(rowBefore).toBeVisible({ timeout: 10_000 });

    // Delete via confirm dialog
    await portfolio.clickDeleteBtn(disposableTicker);
    await portfolio.confirmDelete();

    // Wait for removal
    await page.waitForTimeout(2_000);
    await page.waitForTimeout(2_000);

    const rowAfter = portfolio.isTickerVisible(
      disposableTicker,
    );
    await expect(rowAfter).toBeHidden({ timeout: 10_000 });
  });

  test("delete stock cancel preserves holding", async ({
    page,
  }) => {
    const ticker = "TCS.NS";

    // Ensure the row exists
    const rowBefore = portfolio.isTickerVisible(ticker);
    await expect(rowBefore).toBeVisible({ timeout: 10_000 });

    // Open delete dialog and cancel
    await portfolio.clickDeleteBtn(ticker);
    const dialog = portfolio.confirmDialog();
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    await portfolio.cancelDelete();

    // Dialog should close
    await expect(dialog).toBeHidden({ timeout: 5_000 });

    // Row should still be present
    await page.waitForTimeout(500);
    const rowAfter = portfolio.isTickerVisible(ticker);
    await expect(rowAfter).toBeVisible({ timeout: 5_000 });
  });
});
