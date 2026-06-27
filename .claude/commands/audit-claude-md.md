---
description: Audit the always-loaded CLAUDE.md for bloat and safely optimize it toward a token budget (migrate-out · dedup · compress · flag stale) so it stays slick and light at startup
allowed-tools: [Read, Edit, Write, Bash, Glob, Grep, mcp__serena__list_memories, mcp__serena__read_memory]
version: 1.1.0
rollback: git checkout -- CLAUDE.md .claude/rules/ (uncommitted) or revert the commit
observe: append one dated line per run to .claude/claude-md-audit.log (before→after lines, N changes)
feedback: user approves/edits the scored change table before any file is written
---

# /audit-claude-md — Audit & optimize CLAUDE.md toward a budget

`CLAUDE.md` is loaded into context EVERY session (~11k tok today). Run this
on a cadence (daily/weekly) to keep it slick and light. This is the
**auditor/optimizer** counterpart to `/capture-learnings` (the capture
router): it works on content ALREADY in CLAUDE.md and slims it.

The 3-tier system it optimizes against (established `1d66720`):

1. **`CLAUDE.md`** — always loaded. Hard rules (§4) + one-line pointers (§5).
2. **`.claude/rules/*.md`** — lazy-loaded via `paths:` frontmatter.
3. **Serena memories** — deep detail, on-demand via `→ memory-name`.

## Budget

- **The gate is LINE COUNT — default target ~280 lines** (deterministic).
  Override per run with `$ARGUMENTS` (a line number, e.g.
  `/audit-claude-md 250`).
- **Tokens are approximate, never the gate.** `chars / 4` badly undercounts
  this file (it measured ~5.9k when `/context` reports ~11k — dense code
  identifiers, `§`/`→`/backticks/tables tokenize heavier than prose). Use
  `chars / 2.2` for a rough token figure to REPORT alongside lines; for an
  exact count defer to the user's `/context` (the "Memory files" bucket).
  Equivalent token target ≈ 9k, but decide over/under by LINES.

## Process

### Step 1: Measure

```bash
wc -l -c CLAUDE.md        # lines (the gate), chars (tokens ≈ chars / 2.2)
```

Report **current vs target by LINE COUNT** (tokens shown as approximate
only). If line count is already under target, say so and STOP — do not
churn a file that is already slim. (Still optionally surface any zero-risk
stale/dead findings from Step 2, but propose no size changes.)

### Step 2: Audit — four bloat classes

Scan CLAUDE.md. Score each finding by `severity × confidence × token-savings`:

| Class | What to look for | Fix |
|---|---|---|
| **Migrate-out** | Path-scoped detail (applies only when touching specific dirs) sitting in the body; deep narrative/debugging detail | Move to a `.claude/rules/*.md` (existing if `paths:` matches, else new) or a Serena memory; leave a one-line pointer |
| **Dedup** | Same rule in §4 AND §5, or duplicated between CLAUDE.md and a rules file | Collapse to ONE canonical home + pointer |
| **Compress** | Verbose phrasing, redundant bullets, prose that isn't code-id-dense | Tighten to the terse house voice — meaning preserved |
| **Stale/dead** | Rules citing renamed/deleted code; `→ memory` or rules-file pointers that don't resolve | **Report only** — never auto-delete |

Verify pointers resolve. **Check for the memory's EXISTENCE, not a
substring** — a name appearing as a cross-reference inside another memory
is NOT proof the memory exists (this false-positive masked a dead pointer
in testing):

```bash
# extract each "→ name" memory pointer
grep -oE "→ \`[a-z0-9-]+\`" CLAUDE.md | sed 's/→ //;s/`//g' | sort -u
# RESOLVED only if a file literally named <name>.md exists (find recurses
# the shared/ session/ etc. subtrees):
find .serena/memories -name "<name>.md"
# every ".claude/rules/<x>.md" reference must map to a real file:
grep -oE "\.claude/rules/[a-z-]+\.md" CLAUDE.md | sort -u
```

Prefer `mcp__serena__list_memories` for an authoritative name list.
**When Serena MCP is unavailable AND no `<name>.md` is found on disk,
the pointer is INDETERMINATE — flag it "verify with Serena", never treat
it as resolved OR as confirmed-dead** (MCP-managed memories may not exist
as on-disk files). An indeterminate target also BLOCKS any migrate-out
into it (no-info-loss, Step 5).

Reuse `/check-stale-memories` logic for code-reference staleness; this
command only FLAGS, it does not delete rules.

### Step 3: Prioritize to budget (safe-incremental)

Select the **highest-confidence, lowest-risk** changes that close the
gap to target. Borderline items (risky compressions, nuanced migrations)
are LISTED but NOT proposed for action. Do not over-optimize a file that
is near budget — small steady runs beat one aggressive gut.

### Step 4: Present scored table — WAIT for approval

```
# | Class       | Location | Action                          | Δ lines | Risk
1 | migrate-out | §5.10    | → .claude/rules/forecast.md     |   −6    | low
2 | dedup       | §4.3+§5.1| collapse scoped-delete dupe     |   −2    | low
3 | compress    | §6.5     | tighten yfinance bullets        |   −3    | med
4 | stale       | §6.2     | cites renamed fn (FLAG ONLY)    |    0    | —
```

Report **before / projected-after / target** as LINE counts (tokens
approximate). A migrate-out into an INDETERMINATE/missing target is
blocked — list it but mark 🔴 blocked, do not action. Write nothing until
the user approves. The user may drop/edit/re-tier any row.

### Step 5: Apply — hard safety rules

- **No information loss** — every migrated rule MUST land in a rules file
  or memory BEFORE its CLAUDE.md line is removed (migration + pointer is
  atomic, never partial).
- **§4 semantics are sacred** — a hard rule may be compressed in WORDING
  only; never drop, weaken, or merge away a rule's meaning.
- **Memory tier → `/promote-memory`** — draft the cleaned content, wire
  the `→ name` pointer, then tell the user to run `/promote-memory <name>`.
  This command does NOT write Serena memories itself.
- **Stale = flag only** — never auto-delete a rule citing possibly-renamed
  code; the user decides.
- Keep line length ≤79 chars; match the surrounding terse voice.

### Step 6: Verify

```bash
wc -l -c CLAUDE.md        # re-measure lines vs target
```

Assert the optimized file is still intact:
- all `## N.` / `### N.x` section headers preserved (numbering unbroken),
- every `→` pointer and `.claude/rules/*.md` reference resolves (or was
  already indeterminate — a run must not CREATE a new dead pointer),
- no rule silently lost (count §4 hard rules before/after — must match).

### Step 7: Log + report

Append ONE line to the audit log (create if absent):

```bash
echo "$(date +%F) | <before>→<after> lines | <N> changes | target <T>" \
  >> .claude/claude-md-audit.log
```

Summarize per applied change (class, location, target) and the net line
delta (tokens approximate). Remind the user to commit (`git add CLAUDE.md
.claude/rules/ .claude/claude-md-audit.log`) — do NOT auto-commit.

## Out of scope

- **Does not audit `.claude/rules/*.md` size** — only writes to them as
  migration targets. Auditing the rules tier is a separate future command.
- **Not a capture tool** — new learnings go through `/capture-learnings`.
- **Does not write Serena memories** — hands off to `/promote-memory`.
- **Does not delete stale rules** — flags only; deletion is the user's call.
