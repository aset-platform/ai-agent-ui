#!/usr/bin/env bash
# PreToolUse nudge: prefer Serena symbol tools over whole-file Read / symbol
# Grep, to burn fewer tokens. NON-BLOCKING — always allows the call; only
# injects an advisory via hookSpecificOutput.additionalContext.
#
# Conditional (only nudges when Serena would actually be cheaper):
#   Read  — whole-file read (no offset/limit) of a LARGE code file (>300 ln)
#   Grep  — pattern is a bare identifier (symbol-like)
# Anti-nag: at most once per category per session (state in $TMPDIR).
# Self-gating wording: tells the model to skip if Serena MCP is unavailable
# (this script cannot detect MCP connection state).
#
# Registered in .claude/settings.json under hooks.PreToolUse (matcher
# "Read|Grep"). Contract: exit 0 + JSON on stdout.
set -euo pipefail

LARGE_FILE_LINES=300

input=$(cat)
tool_name=$(printf '%s' "$input" | jq -r '.tool_name // empty')
session_id=$(printf '%s' "$input" | jq -r '.session_id // "nosession"')

state_dir="${TMPDIR:-/tmp}/claude-serena-nudge"
mkdir -p "$state_dir" 2>/dev/null || true

# emit_allow [nudge_text] — print the allow decision (+optional nudge), exit 0
emit_allow() {
  if [ -n "${1:-}" ]; then
    jq -n --arg ctx "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",
      permissionDecision:"allow",additionalContext:$ctx}}'
  else
    jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",
      permissionDecision:"allow"}}'
  fi
  exit 0
}

# already_nudged CATEGORY — true (0) if already nudged this session; else
# records it and returns false (1) so the caller proceeds to nudge once.
already_nudged() {
  local f="$state_dir/${session_id}.$1"
  [ -f "$f" ] && return 0
  : > "$f" 2>/dev/null || true
  return 1
}

case "$tool_name" in
  Read)
    file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
    offset=$(printf '%s' "$input" | jq -r '.tool_input.offset // empty')
    limit=$(printf '%s' "$input" | jq -r '.tool_input.limit // empty')
    # code files only
    case "$file_path" in
      *.py|*.ts|*.tsx|*.js|*.jsx) ;;
      *) emit_allow ;;
    esac
    # targeted read → leave it alone
    { [ -n "$offset" ] || [ -n "$limit" ]; } && emit_allow
    lines=$(wc -l < "$file_path" 2>/dev/null || echo 0)
    [ "${lines:-0}" -lt "$LARGE_FILE_LINES" ] && emit_allow
    already_nudged read && emit_allow
    emit_allow "If Serena MCP is connected, prefer get_symbols_overview on \
'$file_path' then find_symbol to read only the target symbol — this file is \
${lines} lines and a full Read is token-heavy. Ignore if Serena is \
unavailable or you genuinely need the whole file."
    ;;
  Grep)
    pattern=$(printf '%s' "$input" | jq -r '.tool_input.pattern // empty')
    if printf '%s' "$pattern" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*$'; then
      already_nudged grep && emit_allow
      emit_allow "If Serena MCP is connected, '$pattern' looks like a symbol \
— find_symbol / find_referencing_symbols returns its definition and usages \
far cheaper than grepping all matches. Ignore if Serena is unavailable or \
you are searching free text."
    fi
    emit_allow
    ;;
  *)
    emit_allow
    ;;
esac
