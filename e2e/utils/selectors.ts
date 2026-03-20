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
} as const;

/* ── Dashboard (Plotly Dash) ─────────────────────────── */

export const DASH = {
  // Home
  tickerSearch: "ticker-search-input",
  analyseBtn: "search-btn",
  registryDropdown: "home-registry-dropdown",
  stockCardsGrid: "stock-cards-grid",
  stockCard: (ticker: string) => `stock-card-${ticker}`,
  filterIndia: "filter-india-btn",
  filterUS: "filter-us-btn",
  pagination: "home-pagination",
  pageSize: "home-page-size",

  // Analysis
  analysisTabs: "analysis-tabs",
  analysisRefreshBtn: "analysis-refresh-btn",
  analysisRefreshStatus: "analysis-refresh-status",

  // Forecast
  forecastTickerDropdown: "forecast-ticker-dropdown",
  forecastHorizonRadio: "forecast-horizon-radio",
  forecastRefreshBtn: "forecast-refresh-btn",
  forecastChart: "forecast-chart-container",
  forecastAccuracy: "forecast-accuracy-row",

  // Marketplace
  marketplaceGrid: "marketplace-grid",
  marketplaceSearch: "marketplace-search",

  // Admin
  adminUserTable: "admin-user-table",
  adminCreateBtn: "admin-create-user-btn",
  adminAuditLogBtn: "admin-audit-log-btn",

  // Insights
  insightsTabs: "insights-tabs",

  // Global
  errorOverlay: "error-overlay-container",
  navbarProfile: "navbar-profile-dropdown",
  themeToggleBtn: "theme-toggle-btn",
} as const;
