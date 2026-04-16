/**
 * Page object for Portfolio CRUD operations on the
 * Dashboard Home page (``/dashboard``).
 *
 * Extends BasePage with methods for add/edit/delete stock
 * modals and watchlist row inspection.
 */

import { type Locator } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class PortfolioCrudPage extends BasePage {
  /** Navigate to the dashboard home page. */
  async gotoDashboard(): Promise<void> {
    await super.goto("/dashboard");
  }

  // ── Add Stock ─────────────────────────────────────

  /** Click the "Add Stock" button on the dashboard. */
  async openAddStockModal(): Promise<void> {
    const btn = this.tid(FE.dashboardAddStockBtn);
    await btn.scrollIntoViewIfNeeded();
    await btn.click();
  }

  /**
   * Fill the add-stock form fields.
   *
   * Uses string-literal data-testid selectors for modal
   * form inputs (need to be added to selectors.ts).
   */
  async fillAddStockForm(
    ticker: string,
    qty: number,
    price: number,
    date: string,
  ): Promise<void> {
    await this.tid(FE.addStockTicker).fill(ticker);
    await this.tid(FE.addStockQuantity).fill(
      String(qty),
    );
    await this.tid(FE.addStockPrice).fill(
      String(price),
    );
    await this.tid(FE.addStockDate).fill(date);
  }

  /** Click the submit button in the add-stock modal. */
  async submitAddStock(): Promise<void> {
    await this.tid(FE.addStockSubmit).click();
  }

  // ── Edit Stock ────────────────────────────────────

  /**
   * Open the edit modal for a specific ticker row.
   *
   * Clicks the edit button inside the watchlist row
   * identified by the ticker symbol.
   */
  async openEditModal(ticker: string): Promise<void> {
    const row = this.tid(
      FE.dashboardWatchlistRow(ticker),
    );
    await row.scrollIntoViewIfNeeded();
    await this.tid(`watchlist-edit-${ticker}`).click();
  }

  /** Fill the edit-stock form with updated values. */
  async fillEditForm(
    qty: number,
    price: number,
  ): Promise<void> {
    const qtyInput = this.tid(FE.editStockQuantity);
    await qtyInput.clear();
    await qtyInput.fill(String(qty));

    const priceInput = this.tid(FE.editStockPrice);
    await priceInput.clear();
    await priceInput.fill(String(price));
  }

  /** Click the save/submit button in the edit modal. */
  async submitEdit(): Promise<void> {
    await this.tid(FE.editStockSave).click();
  }

  // ── Delete Stock ──────────────────────────────────

  /**
   * Click the delete button for a specific ticker row.
   *
   * This should open a ConfirmDialog.
   */
  async clickDeleteBtn(ticker: string): Promise<void> {
    const row = this.tid(
      FE.dashboardWatchlistRow(ticker),
    );
    await row.scrollIntoViewIfNeeded();
    await this.tid(`watchlist-delete-${ticker}`).click();
  }

  /** Click the confirm button in the ConfirmDialog. */
  async confirmDelete(): Promise<void> {
    // data-testid="confirm-dialog-confirm"
    await this.tid("confirm-dialog-confirm").click();
  }

  /** Click the cancel button in the ConfirmDialog. */
  async cancelDelete(): Promise<void> {
    // data-testid="confirm-dialog-cancel"
    await this.tid("confirm-dialog-cancel").click();
  }

  // ── Watchlist Inspection ──────────────────────────

  /** Return the count of rows in the watchlist table. */
  async getWatchlistRowCount(): Promise<number> {
    const table = this.tid(FE.dashboardWatchlistTable);
    const rows = table.locator("tbody tr");
    return rows.count();
  }

  /** Check whether a ticker row is visible. */
  isTickerVisible(ticker: string): Locator {
    return this.tid(FE.dashboardWatchlistRow(ticker));
  }

  /** The watchlist table container. */
  watchlistTable(): Locator {
    return this.tid(FE.dashboardWatchlistTable);
  }

  /** The add-stock modal container. */
  addStockModal(): Locator {
    // data-testid="add-stock-modal"
    return this.tid("add-stock-modal");
  }

  /** The edit-stock modal container. */
  editStockModal(): Locator {
    // data-testid="edit-stock-modal"
    return this.tid("edit-stock-modal");
  }

  /** The ConfirmDialog container. */
  confirmDialog(): Locator {
    // data-testid="confirm-dialog"
    return this.tid("confirm-dialog");
  }

  /** Validation error message in the add-stock modal. */
  addStockError(): Locator {
    // data-testid="add-stock-error"
    return this.tid("add-stock-error");
  }
}
