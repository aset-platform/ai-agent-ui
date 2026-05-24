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
  adminSummaryCompressions: "admin-summary-tokens",

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

  // ── Advanced Analytics (Sprint 9 AA-13) ─────────
  advancedAnalyticsHeading: "advanced-analytics-heading",
  advancedAnalyticsTabs: "advanced-analytics-tabs",
  advancedAnalyticsTab: (id: string) =>
    `advanced-analytics-tab-${id}`,
  advancedAnalyticsPanel: (id: string) =>
    `advanced-analytics-panel-${id}`,
  advancedAnalyticsTable: (id: string) =>
    `advanced-analytics-table-${id}`,
  advancedAnalyticsStaleChip: (id: string) =>
    `advanced-analytics-stale-${id}`,
  advancedAnalyticsSort: (key: string) =>
    `advanced-analytics-sort-${key}`,
  advancedAnalyticsPrev: (id: string) =>
    `advanced-analytics-prev-${id}`,
  advancedAnalyticsNext: (id: string) =>
    `advanced-analytics-next-${id}`,

  // ── Advanced Analytics filter bundles ────────────
  aaFilterTechButton: "aa-filter-tech-button",
  aaFilterTechPopover: "aa-filter-tech-popover",
  aaFilterTechReset: "aa-filter-tech-reset",
  aaFilterFundButton: "aa-filter-fund-button",
  aaFilterFundPopover: "aa-filter-fund-popover",
  aaFilterFundReset: "aa-filter-fund-reset",
  aaFilterOption: (bundle: "tech" | "fund", key: string) =>
    `aa-filter-${bundle}-option-${key}`,
  aaActiveFilterChip: (key: string) =>
    `aa-active-filter-chip-${key}`,
  aaActiveFilterChipX: (key: string) =>
    `aa-active-filter-chip-${key}-x`,
  aaActiveFilterClearAll: "aa-active-filter-clear-all",

  // ── Advanced Analytics — Swing Setups tab ───────
  swingSetupsTab: "swing-setups-tab",
  swingRegimePills: "swing-regime-pills",
  swingRegimePill: (regime: "bull" | "sideways" | "bearish") =>
    `swing-regime-pill-${regime}`,
  swingMethodologyPanel: "swing-methodology-panel",
  swingMethodologyToggle: "swing-methodology-toggle",
  swingMethodologyNotes: "swing-methodology-notes",
  swingTable: "swing-table",
  swingEmpty: "swing-empty",
  swingLoading: "swing-loading",
  swingError: "swing-error",

  // ── Algo Trading (Slice 0 of the epic) ──────────
  algoTradingHeading: "algo-trading-heading",
  algoTradingTabs: "algo-trading-tabs",
  algoTradingTab: (id: string) => `algo-trading-tab-${id}`,
  algoTradingPanel: (id: string) => `algo-trading-panel-${id}`,

  // ── Algo Trading — Backtest tab (Slice 7b) ─────
  algoBacktestTab: "backtest-tab",
  algoBacktestSubTabStrip: "backtest-sub-tab-strip",
  algoBacktestSubTabSingle: "backtest-sub-tab-single",
  algoBacktestSubTabWalkforward: "backtest-sub-tab-walkforward",
  algoBacktestRunForm: "backtest-run-form",
  algoBacktestStrategySelect: "backtest-strategy-select",
  algoBacktestSubmit: "backtest-submit",
  algoBacktestSummaryCards: "backtest-summary-cards",
  algoBacktestEquityCurve: "backtest-equity-curve",
  algoBacktestEquityCurveEmpty: "backtest-equity-curve-empty",
  algoBacktestTradeTable: "backtest-trade-table",
  algoBacktestTradeTableEmpty: "backtest-trade-table-empty",

  // ── Algo Trading — Walk-forward CV (Slice V2-2) ─
  algoWalkforwardSubTab: "walkforward-sub-tab",
  algoWalkforwardRunForm: "walkforward-run-form",
  algoWalkforwardStrategySelect: "walkforward-strategy-select",
  algoWalkforwardPeriodStart: "walkforward-period-start",
  algoWalkforwardPeriodEnd: "walkforward-period-end",
  algoWalkforwardTrainDays: "walkforward-train-days",
  algoWalkforwardTestDays: "walkforward-test-days",
  algoWalkforwardStepDays: "walkforward-step-days",
  algoWalkforwardCapital: "walkforward-capital",
  algoWalkforwardSubmit: "walkforward-submit",
  algoWalkforwardRunProgress: "walkforward-run-progress",
  algoWalkforwardRunError: "walkforward-run-error",
  algoWalkforwardAggCards: "walkforward-aggregate-cards",
  algoWalkforwardCurves: "walkforward-curves",
  algoWalkforwardCurvesEmpty: "walkforward-curves-empty",
  algoWalkforwardWindowTable: "walkforward-window-table",

  // ── Algo Trading — Paper + Kill switch (Slice 8b) ─
  algoPaperTab: "paper-tab",
  algoPaperEventsTimeline: "paper-events-timeline",
  algoPaperEventsEmpty: "paper-events-empty",
  algoKillSwitchToggle: "kill-switch-toggle",
  algoKillSwitchArmBtn: "kill-switch-arm-btn",
  algoKillSwitchDisarmBtn: "kill-switch-disarm-btn",
  algoKillSwitchArmConfirm: "kill-switch-arm-confirm",
  algoKillSwitchArmConfirmBtn: "kill-switch-arm-confirm-btn",
  algoKillSwitchReasonInput: "kill-switch-reason-input",

  // ── Algo Trading — Paper supervisor (Slice 8c) ─
  algoPaperActiveRunsPanel: "paper-active-runs-panel",
  algoPaperActiveRunsEmpty: "paper-active-runs-empty",
  algoPaperStartRunForm: "paper-start-run-form",
  algoPaperStartStrategySelect: "paper-start-strategy-select",
  algoPaperStartFixtureSelect: "paper-start-fixture-select",
  algoPaperStartCapital: "paper-start-capital",
  algoPaperStartBtn: "paper-start-btn",

  // ── Algo Trading — Performance + Replay (Slice 9 + 10) ─
  algoPerformanceTab: "performance-tab",
  algoPerformanceEmpty: "performance-empty",
  algoPerformanceAggregatesTable: "performance-aggregates-table",
  algoPerformanceRunsTable: "performance-runs-table",
  algoReplayTab: "replay-tab",
  algoReplayFilters: "replay-filters",
  algoReplayModeSelect: "replay-mode-select",
  algoReplayTypeSelect: "replay-type-select",
  algoReplayTimeline: "replay-timeline",
  algoReplayEmpty: "replay-empty",

  // ── Algo Trading — Strategy Levers ───────
  algoStrategyLeversPanel: "strategy-levers-panel",
  algoStrategyLeversToggle: "strategy-levers-toggle",
  algoStrategyLeversBody: "strategy-levers-body",
  algoLeverUniverseScope: "lever-universe-scope",
  algoLeverUniverseMarket: "lever-universe-market",
  algoLeverRebalanceMaxPositions:
    "lever-rebalance-max-positions",
  algoLeverRiskMaxExposurePct:
    "lever-risk-max-exposure-pct",
  algoLeverRiskMaxConcentrationPct:
    "lever-risk-max-concentration-pct",
  algoLeverRiskMaxQty: "lever-risk-max-qty",
  algoLeverRiskMaxLossPct: "lever-risk-max-loss-pct",

  // ── Algo Trading — Live WS health dot (OBS-1) ─
  algoLiveWsHealthDot: "live-ws-health-dot",

  // ── Algo Trading — Reconciliation (V2-3) ─────
  algoReconciliationPanel: "reconciliation-drift-panel",
  algoReconciliationChip: "reconciliation-drift-chip",
  algoReconciliationToggle: "reconciliation-drift-toggle",
  algoReconciliationTable: "reconciliation-drift-table",
  algoDriftThresholdWidget: "drift-threshold-widget",
  algoDriftThresholdInput: "drift-threshold-input",
  algoDriftThresholdSave: "drift-threshold-save",

  // ── Algo Trading — Kite Postback Panel (OBS-4) ──────
  kitePostbackPanel: "kite-postback-panel",
  kitePostbackRow: "kite-postback-row",
  kitePostbackPayloadToggle: "kite-postback-payload-toggle",
  kitePostbackEmptyState: "kite-postback-empty-state",

  // ── Algo Trading — Regime widget (REGIME-1) ──────────
  regimeWidget: "regime-widget",
  regimeWidgetLoading: "regime-widget-loading",
  regimeWidgetEmpty: "regime-widget-empty",
  regimeBadge: "regime-badge",
  regimeVixGauge: "regime-vix-gauge",
  regimeBreadthBar: "regime-breadth-bar",
  regimeStressChip: "regime-stress-chip",
  regimeHistoryChart: "regime-history-chart",
  regimeHistoryEmpty: "regime-history-empty",
  // REGIME-3 — strategy↔regime binding
  regimeChangeBanner: "regime-change-banner",
  regimeChangeBannerDismiss: "regime-change-banner-dismiss",
  regimeApplicabilityChips: "regime-applicability-chips",
  regimeApplicabilityChipBull: "regime-applicability-chip-bull",
  regimeApplicabilityChipSideways: "regime-applicability-chip-sideways",
  regimeApplicabilityChipBear: "regime-applicability-chip-bear",
  regimeApplicabilityMismatchWarning:
    "regime-applicability-mismatch-warning",

  // REGIME-5 — walkforward 5-gate strip + per-regime grid
  walkForwardGatesStrip: "walkforward-gates-strip",
  walkForwardGateLightMaxDd: "walkforward-gate-light-max_dd_ok",
  walkForwardGateLightRecovery: "walkforward-gate-light-recovery_ok",
  walkForwardGateLightPerRegime:
    "walkforward-gate-light-per_regime_non_neg",
  walkForwardGateLightDsr: "walkforward-gate-light-dsr_ok",
  walkForwardGateLightPbo: "walkforward-gate-light-pbo_ok",
  walkForwardPerRegimeGrid: "walkforward-per-regime-grid",

  // REGIME-2b — Factor Scores Insights tab
  factorScoresTab: "factor-scores-tab",
  factorScoresTable: "factor-scores-table",
  factorScoresEmpty: "factor-scores-empty",
  factorScoresLoading: "factor-scores-loading",

  // REGIME-6 — Attribution panel (Daily Brinson + Trade Reasons)
  attributionPanel: "attribution-panel",
  attributionSubtabStrip: "attribution-subtab-strip",
  attributionTabBrinson: "attribution-subtab-brinson",
  attributionTabTrades: "attribution-subtab-trades",
  attributionBrinsonTable: "attribution-brinson-table",
  attributionTradesTable: "attribution-trades-table",
  attributionEmpty: "attribution-empty",
  attributionLoading: "attribution-loading",
  attributionMockChip: "attribution-mock-chip",

  // Algo Trading — three-page split (Slice 6)
  algoSidebarGroup: "sidebar-group-algo-trading",
  algoBrokerLink: "sidebar-child-zerodha-connect",
  algoStrategiesLink: "sidebar-child-strategies",
  algoLiveLink: "sidebar-child-live-trading",

  algoBrokerPage: "algo-broker-page",
  algoStrategiesHeading: "algo-strategies-heading",
  algoStrategiesTab: (id: string) => `algo-strategies-tab-${id}`,
  algoStrategiesPanel: (id: string) => `algo-strategies-panel-${id}`,

  algoLivePage: "algo-live-page",
  algoLiveTab: (id: string) => `algo-live-tab-${id}`,
  algoLivePanel: (id: string) => `algo-live-panel-${id}`,
  algoLiveHeader: "live-header-strip",
  algoLiveModeChip: "live-mode-chip",
  algoLiveDashboard: "live-dashboard",
  algoPanicButton: "panic-close-button",
  algoPanicInput: "panic-close-input",
  algoPanicConfirm: "panic-close-confirm",
  algoPositionsTable: "positions-table",
  algoPositionsEmpty: "positions-empty",

  algoDryRunTab: "dryrun-tab",
  algoDryRunBanner: "dryrun-arm-banner",
  algoDryRunArmBtn: "dryrun-arm-button",
  // algoPaperTab — defined earlier at the top-level entry,
  // re-use the existing constant.

  sweepSubTab: "sweep-sub-tab",
  sweepForm: "sweep-form",
  sweepBaseStrategySelect: "sweep-base-strategy-select",
  sweepPeriodFrom: "sweep-period-from",
  sweepPeriodTo: "sweep-period-to",
  sweepTrainDays: "sweep-train-days",
  sweepTestDays: "sweep-test-days",
  sweepStepDays: "sweep-step-days",
  sweepFieldSelect: "sweep-field-select",
  sweepValuesInput: "sweep-values-input",
  sweepRegimeStratified: "sweep-regime-stratified",
  sweepSubmit: "sweep-submit",
  sweepProgressPanel: "sweep-progress-panel",
  sweepResultsTable: "sweep-results-table",
  sweepPboBadge: "sweep-pbo-badge",
  sweepPromoteWinnerButton: "sweep-promote-winner-button",
  sweepPromoteModal: "sweep-promote-modal",
  budgetPanel: "budget-panel",
  budgetPanelError: "budget-panel-error",
  budgetTileAllocated: "budget-tile-allocated",
  budgetTileOpenPositions: "budget-tile-open-positions",
  budgetTilePending: "budget-tile-pending",
  budgetTileAvailable: "budget-tile-available",
  budgetTileEditButton: "budget-tile-edit-button",
  budgetKiteWalletRow: "budget-kite-wallet-row",
  budgetActiveReservationsTable: "budget-active-reservations-table",
  budgetAllocationModal: "budget-allocation-modal",
  budgetAllocationInput: "budget-allocation-input",
  budgetAllocationSaveButton: "budget-allocation-save-button",
  budgetAllocationBelowCommittedWarning: "budget-allocation-below-committed-warning",
  budgetReservationHistoryLink: "budget-reservation-history-link",
  budgetReservationHistoryModal: "budget-reservation-history-modal",
} as const;
