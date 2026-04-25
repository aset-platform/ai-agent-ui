---
name: jira-3phase-lifecycle
description: Three-phase Jira ticket rhythm — create with full metadata, mark In Progress before code, comment+transition Done at ship
type: convention
---

# Jira 3-phase ticket lifecycle

Every coding task needs a Jira ticket and goes through three explicit
transitions. Hardened across Sprint 7 + 8 closure cycles where
shipped work sat in "In Progress" for days because nobody flipped
the state.

## The three phases

### Phase 1 — Create with full metadata BEFORE writing code

```python
mcp__atlassian__jira_create_issue(
    project_key="ASETPLTFRM",
    summary="Short title (≤80 chars)",
    issue_type="Story",       # or "Task" / "Bug"
    description="""...
## Acceptance criteria
- [ ] ...
- [ ] ...
## Out of scope
- ...
""",
    additional_fields={
        # MUST set BOTH for story points to display correctly
        "customfield_10016": 5,    # estimate (numeric)
        "customfield_10036": "5",  # board display (string)
        "labels": ["sprint8", "backend"],
    },
)
mcp__atlassian__jira_add_issues_to_sprint(
    sprint_id=...,
    issues=["ASETPLTFRM-NNN"],
)
```

Story points must be set on **both** custom fields. `_10036` works
on Stories but not Tasks. Without _10036 the board shows "—".

### Phase 2 — Mark In Progress BEFORE writing code

```python
mcp__atlassian__jira_get_transitions(issue_key="ASETPLTFRM-NNN")
mcp__atlassian__jira_transition_issue(
    issue_key="ASETPLTFRM-NNN",
    transition_id="<the In Progress transition>",
)
```

Skipping this step is the most common drift. The board lies about
sprint state, standup status is wrong, the burndown chart skews.

### Phase 3 — Comment with implementation summary, THEN transition Done

When the work ships (commit lands on the feature branch), add a
comment with:

- Summary sentence
- Commit SHA(s) + brief per-commit purpose
- Files changed (top 5–10)
- Acceptance criteria — strike through with ~~markdown~~ as proof
- Test additions (file path + count)
- Any deferred follow-up filed as a new ticket (with link)

```python
mcp__atlassian__jira_add_comment(
    issue_key="ASETPLTFRM-NNN",
    comment="""## Shipped in commit `abc1234`

**Summary:** ...

**Files modified:**
- `backend/...`
- `frontend/...`

**Tests added:** `tests/backend/test_...py` (8 cases)

**Acceptance criteria**
- ~~Endpoint returns expected shape~~
- ~~Frontend chip auto-clears when list empty~~
- ~~Test coverage ≥ 80% on new code~~

**Follow-up:** ASETPLTFRM-NNN+1 (orphan-parquet sweep)
""",
)
mcp__atlassian__jira_transition_issue(
    issue_key="ASETPLTFRM-NNN",
    transition_id="<the Done transition>",
)
```

## Why all three phases matter

- **Phase 1 metadata** (story points, sprint, labels) determines
  whether the board shows the work and the burndown chart counts it.
- **Phase 2 transition** is the only signal that work has started —
  reviewers/PM use it to identify blockers.
- **Phase 3 comment** is the searchable record. PR descriptions
  rot once branches are deleted; Jira comments are the long-term
  archive linking ticket → commit → tests → follow-ups.

## Multi-ticket sessions

When a session ships N tickets, each gets its OWN comment+transition.
Don't bulk-close — the comment is the per-ticket archive.

## Failed-mid-sprint

If a ticket is started but cannot ship in the sprint:

- Add a comment explaining the blocker.
- Transition back to "To Do" (NOT "Cancelled" unless the work is
  truly dead).
- Keep the sprint assignment if the work resumes next sprint.

## Bug filed mid-session

When a production bug is discovered while working on something else:

```python
mcp__atlassian__jira_create_issue(
    project_key="ASETPLTFRM",
    summary="...",
    issue_type="Bug",
    description="""## Symptom\n...\n## Reproduction\n...\n## Root cause\n(if known)""",
    additional_fields={
        "customfield_10016": 1,
        "customfield_10036": "1",
        "labels": ["bug", "production"],
    },
)
# If shipping in the same session, do all 3 phases.
# If deferring, leave in "To Do" with sprint assignment.
```

## Custom field reference

| Field | ID | Type | Notes |
|---|---|---|---|
| Story points (estimate) | `customfield_10016` | number | required for burndown |
| Story points (board display) | `customfield_10036` | string | works on Stories only |
| Sprint | `customfield_10020` | array | use `add_issues_to_sprint` |

## Related

- `shared/conventions/jira-mcp-usage` — MCP tool reference
- `reference/jira` (personal memory) — board IDs, sprint IDs
- `feedback/jira_ticket_lifecycle` (personal memory) — original
  observation that became this convention
