# Jira MCP Usage Guide

## Project Setup

- Project MUST be **company-managed** (not team-managed) for full API access
- Team-managed projects have limited API support and many fields silently fail

## Tool-Specific Notes

### `create-task-for-epic`
- Use `issueType: Story` to create stories under an epic
- Do NOT use `create-epic-with-subtasks` for stories — it creates subtasks, not stories

### `update-issue`
- `labels` needs proper JSON array format
- Supports `components` field despite not being in schema docs
- Does NOT support assignee by email — needs Jira account ID
- `startDate` and `dueDate` accepted as `YYYY-MM-DD` strings

### `transition-issue`
- Uses `transitionName` param (e.g. "Done", "In Progress")
- NOT `status` — status is the result, transition is the action

### `search-issues`
- Broken in current MCP version (uses deprecated Jira API v2)
- Workaround: Use `get-issue` per key for individual lookups

### `diagnose-fields`
- Essential diagnostic tool — reveals which custom fields are
  available for API writes on each issue type
- Use this when field writes silently fail

## Story Points Gotcha

`storyPoints` parameter writes to `customfield_10036` and returns
HTTP 200, but the value may not persist if:
1. Story Points field is NOT on the Edit Screen in Jira admin
2. Board's Estimation Statistic points to a different custom field

**Fix**:
1. Jira Admin → Issue Type Screens → add Story Points to Edit Screen
2. Board Settings → Estimation → verify Estimation Statistic field ID
   matches `customfield_10036`

## Batch Operations

- `batch-comment` works for adding comments to multiple issues
- For bulk field updates, loop `update-issue` per key (no batch update API)
- Convert relative dates to absolute before saving (e.g. "Thursday" → "2026-03-05")
