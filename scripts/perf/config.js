/**
 * Full-surface performance audit configuration.
 * 63 audit points across 4 sections.
 */

const BASE = process.env.PERF_PORT
  ? `http://localhost:${process.env.PERF_PORT}`
  : "http://localhost:3000";

// ── Section 1: Page Loads ──────────────────────────

const PAGES = [
  { id: "page-login", path: "/login", budget: 65, auth: false },
  { id: "page-dashboard", path: "/dashboard", budget: 75, auth: true },
  { id: "page-analytics", path: "/analytics", budget: 80, auth: true },
  {
    id: "page-analysis",
    path: "/analytics/analysis",
    budget: 70,
    auth: true,
  },
  {
    id: "page-compare",
    path: "/analytics/compare",
    budget: 75,
    auth: true,
  },
  {
    id: "page-insights",
    path: "/analytics/insights",
    budget: 70,
    auth: true,
  },
  {
    id: "page-marketplace",
    path: "/analytics/marketplace",
    budget: 75,
    auth: true,
  },
  { id: "page-admin", path: "/admin", budget: 70, auth: "admin" },
  { id: "page-docs", path: "/docs", budget: 85, auth: true },
  { id: "page-insights-legacy", path: "/insights", budget: 75, auth: true },
];

// ── Section 2: Tab Switches ────────────────────────

const TABS = [
  // Analysis page tabs (5)
  {
    id: "tab-analysis-portfolio",
    page: "/analytics/analysis",
    tabId: "portfolio",
    selector: '[data-testid="analytics-tab-portfolio"]',
    waitFor: '[data-testid="portfolio-analysis-chart"], [data-testid="portfolio-analysis-empty"], [data-testid="portfolio-analysis-error"]',
    auth: true,
  },
  {
    id: "tab-analysis-portfolio-forecast",
    page: "/analytics/analysis",
    tabId: "portfolio-forecast",
    selector: '[data-testid="analytics-tab-portfolio-forecast"]',
    waitFor: '[data-testid="portfolio-forecast-chart"], [data-testid="portfolio-forecast-empty"], [data-testid="portfolio-forecast-error"]',
    auth: true,
  },
  {
    id: "tab-analysis-analysis",
    page: "/analytics/analysis",
    tabId: "analysis",
    selector: '[data-testid="analytics-tab-analysis"]',
    waitFor: '[data-testid="stock-analysis-chart"], [data-testid="stock-analysis-error"]',
    auth: true,
  },
  {
    id: "tab-analysis-forecast",
    page: "/analytics/analysis",
    tabId: "forecast",
    selector: '[data-testid="analytics-tab-forecast"]',
    waitFor: '[data-testid="stock-forecast-chart"], [data-testid="stock-forecast-error"]',
    auth: true,
  },
  {
    id: "tab-analysis-compare",
    page: "/analytics/analysis",
    tabId: "compare",
    selector: '[data-testid="analytics-tab-compare"]',
    waitFor: '[data-testid="compare-chart"], [data-testid="compare-ticker-select"], [data-testid="compare-empty"]',
    auth: true,
  },

  // Insights page tabs (7)
  {
    id: "tab-insights-screener",
    page: "/analytics/insights",
    tabId: "screener",
    selector: '[data-testid="insights-tab-screener"]',
    waitFor: '[data-testid="insights-table"], [data-testid="insights-error"]',
    auth: true,
  },
  {
    id: "tab-insights-targets",
    page: "/analytics/insights",
    tabId: "targets",
    selector: '[data-testid="insights-tab-targets"]',
    waitFor: '[data-testid="insights-table"], [data-testid="insights-error"]',
    auth: true,
  },
  {
    id: "tab-insights-dividends",
    page: "/analytics/insights",
    tabId: "dividends",
    selector: '[data-testid="insights-tab-dividends"]',
    waitFor: '[data-testid="insights-table"], [data-testid="insights-error"]',
    auth: true,
  },
  {
    id: "tab-insights-risk",
    page: "/analytics/insights",
    tabId: "risk",
    selector: '[data-testid="insights-tab-risk"]',
    waitFor: '[data-testid="insights-table"], [data-testid="insights-error"]',
    auth: true,
  },
  {
    id: "tab-insights-sectors",
    page: "/analytics/insights",
    tabId: "sectors",
    selector: '[data-testid="insights-tab-sectors"]',
    waitFor: '[data-testid="insights-chart"], [data-testid="insights-table"], [data-testid="insights-error"]',
    auth: true,
  },
  {
    id: "tab-insights-correlation",
    page: "/analytics/insights",
    tabId: "correlation",
    selector: '[data-testid="insights-tab-correlation"]',
    waitFor: '[data-testid="insights-chart"], [data-testid="insights-empty"], [data-testid="insights-error"]',
    auth: true,
  },
  {
    id: "tab-insights-quarterly",
    page: "/analytics/insights",
    tabId: "quarterly",
    selector: '[data-testid="insights-tab-quarterly"]',
    waitFor: '[data-testid="insights-chart"], [data-testid="insights-table"], [data-testid="insights-error"]',
    auth: true,
  },

  // Admin page tabs (5)
  {
    id: "tab-admin-users",
    page: "/admin",
    tabId: "users",
    selector: '[data-testid="admin-tab-users"]',
    waitFor: '[data-testid="admin-users-table"]',
    auth: "admin",
  },
  {
    id: "tab-admin-audit",
    page: "/admin",
    tabId: "audit",
    selector: '[data-testid="admin-tab-audit"]',
    waitFor: '[data-testid="admin-audit-table"]',
    auth: "admin",
  },
  {
    id: "tab-admin-observability",
    page: "/admin",
    tabId: "observability",
    selector: '[data-testid="admin-tab-observability"]',
    waitFor: '[data-testid^="admin-tier-card-"], [data-testid="admin-cascade-table"]',
    auth: "admin",
  },
  {
    id: "tab-admin-maintenance",
    page: "/admin",
    tabId: "maintenance",
    selector: '[data-testid="admin-tab-maintenance"]',
    waitFor: "button",
    auth: "admin",
  },
  {
    id: "tab-admin-transactions",
    page: "/admin",
    tabId: "transactions",
    selector: '[data-testid="admin-tab-transactions"]',
    waitFor: "table, [data-testid='admin-transactions-empty']",
    auth: "admin",
  },

  // Profile modal tabs (3)
  {
    id: "tab-profile-profile",
    page: "/dashboard",
    tabId: "profile",
    selector: null, // opened via modal trigger
    waitFor: '[data-testid="edit-profile-modal"] input',
    auth: true,
    modalTrigger: "profile",
  },
  {
    id: "tab-profile-billing",
    page: "/dashboard",
    tabId: "billing",
    selector: null,
    waitFor: '[data-testid="edit-profile-modal"]',
    auth: true,
    modalTrigger: "billing",
  },
  {
    id: "tab-profile-audit",
    page: "/dashboard",
    tabId: "audit",
    selector: null,
    waitFor: '[data-testid="edit-profile-modal"]',
    auth: true,
    modalTrigger: "audit",
  },
];

// ── Section 3: Modals ──────────────────────────────

const MODALS = [
  {
    id: "modal-edit-profile",
    label: "EditProfileModal",
    page: "/dashboard",
    triggerSteps: [
      { action: "click", selector: '[data-testid="profile-avatar"]' },
      { action: "click", text: "Edit Profile" },
    ],
    waitFor: '[data-testid="edit-profile-modal"]',
    closeSelector: '[data-testid="edit-profile-modal"] button[aria-label="Close"], [data-testid="edit-profile-close"]',
    auth: true,
  },
  {
    id: "modal-change-password",
    label: "ChangePasswordModal",
    page: "/dashboard",
    triggerSteps: [
      { action: "click", selector: '[data-testid="profile-avatar"]' },
      { action: "click", text: "Change Password" },
    ],
    waitFor: '[data-testid="change-password-modal"]',
    closeSelector: '[data-testid="change-password-modal"] button',
    auth: true,
  },
  {
    id: "modal-session-management",
    label: "SessionManagementModal",
    page: "/dashboard",
    triggerSteps: [
      { action: "click", selector: '[data-testid="profile-avatar"]' },
      { action: "click", text: "Manage Sessions" },
    ],
    waitFor: '[data-testid="session-management-modal"]',
    closeSelector: '[data-testid="session-management-modal"] button',
    auth: true,
  },
  {
    id: "modal-add-stock",
    label: "AddStockModal",
    page: "/dashboard",
    triggerSteps: [
      { action: "click", selector: '[data-testid="dashboard-add-stock-btn"]' },
    ],
    waitFor: '.fixed.inset-0.z-50',
    auth: true,
  },
  {
    id: "modal-user-add",
    label: "UserModal (Add)",
    page: "/admin",
    triggerSteps: [
      { action: "click", selector: '[data-testid="admin-users-add-btn"]' },
    ],
    waitFor: '[role="dialog"]',
    auth: "admin",
  },
  {
    id: "modal-confirm-dialog",
    label: "ConfirmDialog",
    page: "/dashboard",
    triggerSteps: [
      // Click the first delete/unlink action in watchlist
      {
        action: "click",
        selector: '[data-testid^="dashboard-watchlist-refresh-"]',
      },
    ],
    waitFor: '[data-testid^="dashboard-watchlist-refresh-"]',
    auth: true,
    optional: true,
  },
];

// ── Section 4: Interactive Controls ────────────────

const INTERACTIVE = [
  // Chart timeframes (on analysis page, analysis tab)
  ...[
    "1m", "3m", "6m", "1y", "2y", "3y", "max",
  ].map((range) => ({
    id: `interact-range-${range}`,
    label: `Chart range ${range.toUpperCase()}`,
    page: "/analytics/analysis",
    prerequisite: { tab: "analysis" },
    selector: `[data-testid="stock-analysis-range-${range}"]`,
    settledCheck: "chart-redraw",
    auth: true,
  })),

  // Market filters
  {
    id: "interact-market-india",
    label: "Market filter India",
    page: "/dashboard",
    selector: '[data-testid="dashboard-market-filter-india"]',
    settledCheck: "content-update",
    auth: true,
  },
  {
    id: "interact-market-us",
    label: "Market filter US",
    page: "/dashboard",
    selector: '[data-testid="dashboard-market-filter-us"]',
    settledCheck: "content-update",
    auth: true,
  },

  // Sidebar toggle
  {
    id: "interact-sidebar-collapse",
    label: "Sidebar collapse",
    page: "/dashboard",
    selector: '[data-testid="sidebar-collapse-toggle"]',
    settledCheck: "layout-settle",
    auth: true,
  },
  {
    id: "interact-sidebar-expand",
    label: "Sidebar expand",
    page: "/dashboard",
    selector: '[data-testid="sidebar-collapse-toggle"]',
    settledCheck: "layout-settle",
    auth: true,
  },

  // Chat panel toggle
  {
    id: "interact-chat-open",
    label: "Chat panel open",
    page: "/dashboard",
    selector: '[data-testid="chat-toggle"]',
    settledCheck: "layout-settle",
    auth: true,
  },
  {
    id: "interact-chat-close",
    label: "Chat panel close",
    page: "/dashboard",
    selector: '[data-testid="chat-toggle"]',
    settledCheck: "layout-settle",
    auth: true,
  },

  // Marketplace pagination
  {
    id: "interact-marketplace-next",
    label: "Marketplace next page",
    page: "/analytics/marketplace",
    selector: '[data-testid="marketplace-pagination-next"]',
    settledCheck: "content-update",
    auth: true,
  },
  {
    id: "interact-marketplace-prev",
    label: "Marketplace prev page",
    page: "/analytics/marketplace",
    selector: '[data-testid="marketplace-pagination-prev"]',
    settledCheck: "content-update",
    auth: true,
  },

  // Theme toggle
  {
    id: "interact-theme-toggle",
    label: "Theme toggle",
    page: "/dashboard",
    selector: '[data-testid="sidebar-theme-toggle"]',
    settledCheck: "layout-settle",
    auth: true,
  },
];

// ── Budgets ────────────────────────────────────────

const TAB_BUDGET = 60;   // score >= 60 for tab switches (API latency variance)
const MODAL_BUDGET = 80;  // score >= 80 for modals
const INTERACTION_BUDGET = 50; // score >= 50 for interactions (chart redraws have fixed 1.5s wait)

// ── Timeouts ───────────────────────────────────────

const TIMEOUTS = {
  pageLoad: 30000,
  tabSwitch: 15000,
  modalOpen: 10000,
  interaction: 10000,
  settleDelay: 1500,
  totalAudit: 9 * 60 * 1000, // 9 minutes
};

module.exports = {
  BASE,
  PAGES,
  TABS,
  MODALS,
  INTERACTIVE,
  TAB_BUDGET,
  MODAL_BUDGET,
  INTERACTION_BUDGET,
  TIMEOUTS,
};
