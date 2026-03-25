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
    await this.tid(FE.dashboardAddStockBtn).click();
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
    // data-testid="add-stock-ticker-input"
    await this.tid("add-stock-ticker-input").fill(ticker);
    // data-testid="add-stock-quantity-input"
    await this.tid("add-stock-quantity-input").fill(
      String(qty),
    );
    // data-testid="add-stock-price-input"
    await this.tid("add-stock-price-input").fill(
      String(price),
    );
    // data-testid="add-stock-date-input"
    await this.tid("add-stock-date-input").fill(date);
  }

  /** Click the submit button in the add-stock modal. */
  async submitAddStock(): Promise<void> {
    // data-testid="add-stock-submit-btn"
    await this.tid("add-stock-submit-btn").click();
  }

  // ── Edit Stock ────────────────────────────────────

  /**
   * Open the edit modal for a specific ticker row.
   *
   * Clicks the edit button inside the watchlist row
   * identified by the ticker symbol.
   */
  async openEditModal(ticker: string): Promise<void> {
    // data-testid="watchlist-edit-{ticker}"
    await this.tid(`watchlist-edit-${ticker}`).click();
  }

  /** Fill the edit-stock form with updated values. */
  async fillEditForm(
    qty: number,
    price: number,
  ): Promise<void> {
    const qtyInput = this.tid("edit-stock-quantity-input");
    await qtyInput.clear();
    await qtyInput.fill(String(qty));

    const priceInput = this.tid("edit-stock-price-input");
    await priceInput.clear();
    await priceInput.fill(String(price));
  }

  /** Click the save/submit button in the edit modal. */
  async submitEdit(): Promise<void> {
    // data-testid="edit-stock-submit-btn"
    await this.tid("edit-stock-submit-btn").click();
  }

  // ── Delete Stock ──────────────────────────────────

  /**
   * Click the delete button for a specific ticker row.
   *
   * This should open a ConfirmDialog.
   */
  async clickDeleteBtn(ticker: string): Promise<void> {
    // data-testid="watchlist-delete-{ticker}"
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
