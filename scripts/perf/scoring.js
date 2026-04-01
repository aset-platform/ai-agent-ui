/**
 * Score estimation functions per audit type.
 * Returns 0-100 score (higher = better).
 */

/** Linear interpolation between good and poor. */
function lerp(value, good, poor) {
  if (value <= good) return 1;
  if (value >= poor) return 0;
  return 1 - (value - good) / (poor - good);
}

/**
 * Page load score (Lighthouse-like weights).
 */
function scorePageLoad(metrics) {
  if (!metrics) return 0;
  const lcp = lerp(metrics.lcp_ms, 2500, 4000) * 0.25;
  const tbt = lerp(metrics.tbt_ms, 200, 600) * 0.30;
  const cls = lerp(metrics.cls, 0.1, 0.25) * 0.25;
  const fcp = lerp(metrics.fcp_ms, 1800, 3000) * 0.10;
  const si = Math.min(
    lerp(metrics.lcp_ms, 2500, 4000),
    lerp(metrics.fcp_ms, 1800, 3000),
  ) * 0.10;
  return Math.round((lcp + tbt + cls + fcp + si) * 100);
}

/**
 * Tab switch score.
 */
function scoreTabSwitch(metrics) {
  if (!metrics) return 0;
  const time = lerp(metrics.switch_ms, 500, 3000) * 0.50;
  const cls = lerp(metrics.cls, 0.025, 0.1) * 0.30;
  const tbt = lerp(metrics.tbt_ms, 100, 300) * 0.20;
  return Math.round((time + cls + tbt) * 100);
}

/**
 * Modal open score.
 */
function scoreModalOpen(metrics) {
  if (!metrics) return 0;
  const time = lerp(metrics.open_ms, 300, 2000) * 0.50;
  const cls = lerp(metrics.cls, 0.025, 0.1) * 0.30;
  const tbt = lerp(metrics.tbt_ms, 50, 200) * 0.20;
  return Math.round((time + cls + tbt) * 100);
}

/**
 * Interaction score.
 */
function scoreInteraction(metrics) {
  if (!metrics) return 0;
  const time = lerp(
    metrics.response_ms, 300, 2000,
  ) * 0.50;
  const cls = lerp(metrics.cls, 0.025, 0.1) * 0.30;
  const tbt = lerp(metrics.tbt_ms, 50, 200) * 0.20;
  return Math.round((time + cls + tbt) * 100);
}

/**
 * Overall score (weighted sections).
 */
function scoreOverall(sectionScores) {
  const { pages, tabs, modals, interactive } =
    sectionScores;
  return Math.round(
    pages * 0.40
    + tabs * 0.30
    + modals * 0.15
    + interactive * 0.15,
  );
}

module.exports = {
  scorePageLoad,
  scoreTabSwitch,
  scoreModalOpen,
  scoreInteraction,
  scoreOverall,
};
