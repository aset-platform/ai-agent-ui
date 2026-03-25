/**
 * JSON file reporter — writes results to
 * perf-baselines/sprint-N-full.json
 */

const fs = require("fs");
const path = require("path");

function writeBaseline(results, sectionScores, overall) {
  const baseDir = path.join(
    __dirname, "..", "..", "..", "perf-baselines",
  );

  // Find the latest sprint number
  const files = fs.readdirSync(baseDir).filter(
    (f) => f.match(/^sprint-\d+-full\.json$/),
  );
  const nums = files.map(
    (f) => parseInt(f.match(/sprint-(\d+)/)[1], 10),
  );
  const sprint = nums.length > 0
    ? Math.max(...nums)
    : 3;

  const outFile = path.join(
    baseDir, `sprint-${sprint}-full.json`,
  );

  const data = {
    sprint,
    date: new Date().toISOString().split("T")[0],
    tool: "playwright-performance-api",
    device: "desktop-headless",
    total_points: results.length,
    passed: results.filter((r) => r.passed).length,
    skipped: results.filter((r) => r.skipped).length,
    failed: results.filter(
      (r) => !r.passed && !r.skipped,
    ).length,
    overall_score: overall,
    section_scores: sectionScores,
    results: results.map((r) => ({
      id: r.id,
      type: r.type,
      score: r.score,
      budget: r.budget,
      passed: r.passed,
      skipped: r.skipped || false,
      error: r.error || null,
      metrics: r.metrics || null,
    })),
  };

  fs.writeFileSync(outFile, JSON.stringify(data, null, 2));
  console.log(`\n  Baseline saved: ${outFile}`);
}

module.exports = { writeBaseline };
