/**
 * Shared data-testid constants for Playwright selectors.
 *
 * Centralised here so POM classes and tests reference the same
 * strings — a rename only needs to happen in one place.
 */

/* ── Frontend (Next.js) ──────────────────────────────── */

export const FE = {
  // Login page
  loginEmail: "login-email-input",
  loginPassword: "login-password-input",
  loginSubmit: "login-submit-button",
  loginError: "login-error-message",
  oauthGoogle: "oauth-google-button",

  // Chat page
  chatInput: "chat-message-input",
  chatSend: "chat-send-button",
  agentSelector: "agent-selector",
  clearMessages: "clear-messages-button",
  profileAvatar: "profile-avatar",
  assistantMessage: "assistant-message",
  userMessage: "user-message",
  statusBadge: "status-badge",

  // Navigation
  navMenuToggle: "nav-menu-toggle",
  navItem: (name: string) => `nav-item-${name}`,

  // Sidebar
  sidebar: "sidebar",
  sidebarThemeToggle: "sidebar-theme-toggle",
  sidebarCollapseToggle: "sidebar-collapse-toggle",
  sidebarItem: (view: string) => `sidebar-item-${view}`,
  sidebarGroup: (view: string) => `sidebar-group-${view}`,
  sidebarChild: (label: string) => `sidebar-child-${label}`,

  // Modals
  editProfileModal: "edit-profile-modal",
  changePasswordModal: "change-password-modal",
  sessionManagementModal: "session-management-modal",

  // Session management
  sessionCard: "session-card",
  currentSessionBadge: "current-session-badge",
  revokeSessionBtn: "revoke-session-btn",
  revokeAllSessionsBtn: "revoke-all-sessions-btn",

  // Theme
  themeToggle: "theme-toggle",

  // ── Analytics/Analysis (5 tabs) ──────────────────
  analyticsTab: (id: string) => `analytics-tab-${id}`,

  // Portfolio Analysis
  portfolioAnalysisChart: "portfolio-analysis-chart",
  portfolioAnalysisRefreshBtn: "portfolio-analysis-refresh-btn",
  portfolioAnalysisRefreshIcon: "portfolio-analysis-refresh-icon",
  portfolioAnalysisPeriod: (p: string) =>
    `portfolio-analysis-period-${p}`,
  portfolioAnalysisMetric: (n: string) =>
    `portfolio-analysis-metric-${n}`,
  portfolioAnalysisMetricValue: (n: string) =>
    `portfolio-analysis-metric-value-${n}`,
  portfolioAnalysisEmpty: "portfolio-analysis-empty",
  portfolioAnalysisError: "portfolio-analysis-error",
  portfolioAnalysisCurrencyBadge:
    "portfolio-analysis-currency-badge",

  // Portfolio Forecast
  portfolioForecastChart: "portfolio-forecast-chart",
  portfolioForecastRefreshBtn: "portfolio-forecast-refresh-btn",
  portfolioForecastRefreshIcon: "portfolio-forecast-refresh-icon",
  portfolioForecastHorizon: (n: number) =>
    `portfolio-forecast-horizon-${n}`,
  portfolioForecastCard: (name: string) =>
    `portfolio-forecast-card-${name}`,
  portfolioForecastCardValue: (name: string) =>
    `portfolio-forecast-card-value-${name}`,
  portfolioForecastCardPnl: "portfolio-forecast-card-pnl",
  portfolioForecastEmpty: "portfolio-forecast-empty",
  portfolioForecastError: "portfolio-forecast-error",

  // Stock Analysis
  stockAnalysisChart: "stock-analysis-chart",
  stockAnalysisRange: (p: string) =>
    `stock-analysis-range-${p}`,
  stockAnalysisInterval: (i: string) =>
    `stock-analysis-interval-${i}`,
  stockAnalysisIndicatorsMenu: "stock-analysis-indicators-menu",
  stockAnalysisIndicator: (name: string) =>
    `stock-analysis-indicator-${name}`,
  stockAnalysisError: "stock-analysis-error",

  // Stock Forecast
  stockForecastChart: "stock-forecast-chart",
  stockForecastHorizon: (n: number) =>
    `stock-forecast-horizon-${n}`,
  stockForecastTargetCard: (i: number) =>
    `stock-forecast-target-card-${i}`,
  stockForecastAccuracy: (m: string) =>
    `stock-forecast-accuracy-${m}`,
  stockForecastError: "stock-forecast-error",

  // Compare Stocks
  compareChart: "compare-chart",
  compareTickerSelect: "compare-ticker-select",
  compareEmpty: "compare-empty",

  // ── Dashboard Home ───────────────────────────────
  dashboardMarketFilter: (m: string) =>
    `dashboard-market-filter-${m}`,
  dashboardHeroPortfolioValue: "dashboard-hero-portfolio-value",
  dashboardHeroDailyChange: "dashboard-hero-daily-change",
  dashboardWatchlistTable: "dashboard-watchlist-table",
  dashboardWatchlistRow: (t: string) =>
    `dashboard-watchlist-row-${t}`,
  dashboardWatchlistRefresh: (t: string) =>
    `dashboard-watchlist-refresh-${t}`,
  dashboardAddStockBtn: "dashboard-add-stock-btn",
  dashboardForecastWidget: "dashboard-forecast-widget",

  // ── Insights ─────────────────────────────────────
  insightsTab: (id: string) => `insights-tab-${id}`,
  insightsMarketFilter: "insights-market-filter",
  insightsSectorFilter: "insights-sector-filter",
  insightsTickerFilter: "insights-ticker-filter",
  insightsRsiFilter: "insights-rsi-filter",
  insightsPeriodFilter: "insights-period-filter",
  insightsStatementType: "insights-statement-type",
  insightsTable: "insights-table",
  insightsChart: "insights-chart",
  insightsEmpty: "insights-empty",
  insightsError: "insights-error",

  // ── Marketplace ──────────────────────────────────
  marketplaceSearch: "marketplace-search",
  marketplaceMarket: (f: string) =>
    `marketplace-market-${f}`,
  marketplaceTable: "marketplace-table",
  marketplaceRow: (t: string) => `marketplace-row-${t}`,
  marketplaceLink: (t: string) => `marketplace-link-${t}`,
  marketplaceUnlink: (t: string) => `marketplace-unlink-${t}`,
  marketplaceStats: "marketplace-stats",
  marketplacePaginationPrev: "marketplace-pagination-prev",
  marketplacePaginationNext: "marketplace-pagination-next",
  marketplacePageInfo: "marketplace-page-info",

  // ── Admin ────────────────────────────────────────
  adminTab: (id: string) => `admin-tab-${id}`,
  adminUsersSearch: "admin-users-search",
  adminUsersTable: "admin-users-table",
  adminUsersAddBtn: "admin-users-add-btn",
  adminUserRow: (id: string) => `admin-user-row-${id}`,
  adminUserEdit: (id: string) => `admin-user-edit-${id}`,
  adminUserReset: (id: string) => `admin-user-reset-${id}`,
  adminUserToggle: (id: string) => `admin-user-toggle-${id}`,
  adminAuditSearch: "admin-audit-search",
  adminAuditTable: "admin-audit-table",
  adminTierCard: (m: string) => `admin-tier-card-${m}`,
  adminTierToggle: (m: string) => `admin-tier-toggle-${m}`,
  adminBudgetCard: (m: string) => `admin-budget-card-${m}`,
  adminCascadeTable: "admin-cascade-table",
  adminSummaryRequests: "admin-summary-requests",
  adminSummaryCascades: "admin-summary-cascades",
  adminSummaryCompressions: "admin-summary-compressions",

  // ── AddStockModal ────────────────────────────────
  addStockTicker: "add-stock-ticker",
  addStockQuantity: "add-stock-quantity",
  addStockPrice: "add-stock-price",
  addStockDate: "add-stock-date",
  addStockNotes: "add-stock-notes",
  addStockSubmit: "add-stock-submit",
  addStockError: "add-stock-error",

  // ── EditStockModal ──────────────────────────────
  editStockQuantity: "edit-stock-quantity",
  editStockPrice: "edit-stock-price",
  editStockSave: "edit-stock-save",
  editStockError: "edit-stock-error",

  // ── ConfirmDialog ───────────────────────────────
  confirmDialog: "confirm-dialog",
  confirmDialogConfirm: "confirm-dialog-confirm",
  confirmDialogCancel: "confirm-dialog-cancel",

  // ── UserModal ───────────────────────────────────
  userModalEmail: "user-modal-email",
  userModalName: "user-modal-name",
  userModalRole: "user-modal-role",
  userModalPassword: "user-modal-password",
  userModalSubmit: "user-modal-submit",

  // ── ResetPasswordModal ──────────────────────────
  resetPasswordInput: "reset-password-input",
  resetPasswordSubmit: "reset-password-submit",

  // ── BillingTab ──────────────────────────────────
  billingGatewayRazorpay: "billing-gateway-razorpay",
  billingGatewayStripe: "billing-gateway-stripe",
  billingUpgradePro: "billing-upgrade-pro",
  billingUpgradePremium: "billing-upgrade-premium",
  billingCancel: "billing-cancel",
  billingCurrentPlan: "billing-current-plan",

  // ── Chart canvases ───────────────────────────────
  portfolioChartCanvas: "portfolio-chart-canvas",
  portfolioForecastChartCanvas:
    "portfolio-forecast-chart-canvas",
  stockChartCanvas: "stock-chart-canvas",
  forecastChartCanvas: "forecast-chart-canvas",
  compareChartCanvas: "compare-chart-canvas",
  plotlyChart: "plotly-chart",
} as const;
