/**
 * ANSI-colored console reporter for perf audit.
 */

const G = "\x1b[32m";
const R = "\x1b[31m";
const Y = "\x1b[33m";
const C = "\x1b[36m";
const D = "\x1b[90m";
const N = "\x1b[0m";
const BAR = `${C}${"━".repeat(50)}${N}`;

function header(title, count) {
  console.log(`\n${BAR}`);
  console.log(
    `${C}  ${title} (${count} points)${N}`,
  );
  console.log(BAR);
}

function section(name) {
  console.log(`\n  ${C}${name}${N}`);
  console.log(`  ${D}${"─".repeat(46)}${N}`);
}

function pageResult(r) {
  const icon = r.passed
    ? `${G}✓${N}` : `${R}✗${N}`;
  const sc = r.passed
    ? `${G}${r.score}${N}` : `${R}${r.score}${N}`;
  if (r.error) {
    console.log(
      `  ${r.id.padEnd(36)} ${R}✗ ${r.error}${N}`,
    );
    return;
  }
  if (r.skipped) {
    console.log(
      `  ${r.id.padEnd(36)} ${Y}⊘ skipped${N}`,
    );
    return;
  }
  const m = r.metrics;
  const metricsStr = m.fcp_ms !== undefined
    ? `LCP: ${m.lcp_ms}ms  FCP: ${m.fcp_ms}ms`
      + `  TBT: ${m.tbt_ms}ms  CLS: ${m.cls.toFixed(3)}`
    : m.switch_ms !== undefined
      ? `Switch: ${m.switch_ms}ms`
        + `  TBT: ${m.tbt_ms}ms  CLS: ${m.cls.toFixed(3)}`
      : m.open_ms !== undefined
        ? `Open: ${m.open_ms}ms`
          + `  TBT: ${m.tbt_ms}ms  CLS: ${m.cls.toFixed(3)}`
        : `Response: ${m.response_ms}ms`
          + `  TBT: ${m.tbt_ms}ms`
          + `  CLS: ${m.cls.toFixed(3)}`;

  console.log(
    `  ${r.id.padEnd(36)} ${icon} Score: ${sc}`
    + ` (${r.budget})  ${D}${metricsStr}${N}`,
  );
}

function summary(results, sectionScores, overall) {
  console.log(`\n${BAR}`);
  const failed = results.filter(
    (r) => !r.passed && !r.skipped,
  );
  const skipped = results.filter((r) => r.skipped);
  const passed = results.filter(
    (r) => r.passed && !r.skipped,
  );
  const total = results.length - skipped.length;

  if (failed.length === 0) {
    console.log(
      `${G}  ✓ All ${total} audit points pass!${N}`,
    );
  } else {
    console.log(
      `${R}  ✗ ${failed.length}/${total}`
      + ` audit point(s) below budget${N}`,
    );
    for (const f of failed.slice(0, 10)) {
      console.log(
        `    ${f.id}: ${f.score || "ERR"}`
        + ` (budget: ${f.budget})`,
      );
    }
  }

  console.log(
    `\n  Overall: ${overall}/100`
    + `  Pages: ${sectionScores.pages}`
    + `  Tabs: ${sectionScores.tabs}`
    + `  Modals: ${sectionScores.modals}`
    + `  Interactive: ${sectionScores.interactive}`,
  );

  if (skipped.length > 0) {
    console.log(
      `  ${Y}${skipped.length} point(s) skipped`
      + ` (missing admin creds or data)${N}`,
    );
  }
  console.log(BAR);
}

module.exports = { header, section, pageResult, summary };
