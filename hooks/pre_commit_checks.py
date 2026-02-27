#!/usr/bin/env python3
"""Pre-commit quality gate for the ai-agent-ui project.

Invoked by ``hooks/pre-commit`` with no arguments.  Retrieves the list of
modified / newly-created staged files internally via ``git diff --cached``.

Checks performed (in order):

1. **Static code analysis** (always runs):

   * Bare ``print()`` calls in backend Python — auto-fixed by replacing with
     ``logger.info()`` and injecting the module logger if absent.
   * Missing Google-style docstrings (module / class / public method).
   * Naming-convention violations (snake_case functions, PascalCase classes).
   * OOP issues — module-level mutable globals that belong on ``self``.
   * XSS risks — f-strings with HTML tags in Dash components or TS files.
   * SQL-injection risks — f-strings / concatenation inside ``execute()`` calls.

   When ``ANTHROPIC_API_KEY`` is set, all violations are sent to Claude
   (``claude-sonnet-4-6``) for automatic repair.  Fixed files are re-staged
   so the fixes are included in the current commit.

2. **Meta-file freshness** (requires ``ANTHROPIC_API_KEY``):

   Claude reviews the staged diff and updates ``CLAUDE.md``, ``PROGRESS.md``,
   and the project-root ``README.md`` if they are stale.  All three are
   checked in a single API call.  Updated files are re-staged automatically.

3. **Documentation freshness** (requires ``ANTHROPIC_API_KEY``):

   Staged source files are mapped to their ``docs/`` counterparts.  The LLM
   patches any stale sections.  Updated pages are re-staged.

4. **Changelog date ordering** (always runs, no API required):

   ``docs/dev/changelog.md`` H2 headings are parsed, verified to be in
   descending date order (newest first), and rewritten if they are not.

Exit codes:
    0  All checks passed or were auto-fixed — commit proceeds.
    1  A blocking violation that could not be repaired was found (syntax
       error in staged Python, or a static error when no Claude key is set).

Example::

    # Run directly for testing (no commit needed)
    python hooks/pre_commit_checks.py
"""

import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Bootstrap — ensure project root is importable
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv_into_environ() -> None:
    """Load key=value pairs from .env files into os.environ (without overwriting).

    Reads the project-root ``.env`` and ``backend/.env`` files at startup so
    that variables like ``ANTHROPIC_API_KEY`` and ``JWT_SECRET_KEY`` are available
    even when not explicitly exported in the shell.  Existing env vars always take precedence.
    """
    for env_file in (_PROJECT_ROOT / ".env", _PROJECT_ROOT / "backend" / ".env"):
        if not env_file.exists():
            continue
        with open(env_file, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val


_load_dotenv_into_environ()

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_CYAN   = "\033[36m"
_BOLD   = "\033[1m"


def _c(text: str, colour: str) -> str:
    """Wrap *text* in an ANSI escape sequence if stdout is a TTY.

    Args:
        text: The string to colourise.
        colour: ANSI escape code to apply.

    Returns:
        Colourised string, or plain *text* when not a TTY.
    """
    return f"{colour}{text}{_RESET}" if sys.stdout.isatty() else text


def _ok(msg: str)   -> None: print(f"  {_c('✅', _GREEN)}  {msg}")
def _warn(msg: str) -> None: print(f"  {_c('⚠️ ', _YELLOW)} {msg}")
def _err(msg: str)  -> None: print(f"  {_c('❌', _RED)}  {msg}")
def _fix(msg: str)  -> None: print(f"  {_c('🔧', _CYAN)}  {msg}")
def _info(msg: str) -> None: print(f"  {_c('ℹ️ ', _CYAN)}  {msg}")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    """A single code-quality violation found in a staged file.

    Attributes:
        filepath: Relative path from the project root.
        line: 1-based line number where the violation occurs.
        category: One of ``'print'``, ``'docstring'``, ``'naming'``,
            ``'oop'``, or ``'security'``.
        severity: ``'error'`` blocks the commit; ``'warning'`` does not.
        description: Human-readable description of the violation.
    """

    filepath: str
    line: int
    category: str
    severity: str
    description: str


# ---------------------------------------------------------------------------
# Source → docs mapping
# ---------------------------------------------------------------------------

# Maps a path prefix (or exact path) to the docs pages that cover it.
_DOCS_MAP: List[Tuple[str, List[str]]] = [
    ("backend/main.py",         ["docs/backend/api.md", "docs/backend/overview.md"]),
    ("backend/agents/",         ["docs/backend/agents.md"]),
    ("backend/tools/",          ["docs/backend/tools.md"]),
    ("backend/config.py",       ["docs/backend/config.md"]),
    ("backend/logging_config.py", ["docs/backend/logging.md"]),
    ("auth/",                   ["docs/backend/auth.md"]),
    ("dashboard/",              ["docs/dashboard/overview.md"]),
    ("frontend/app/",           ["docs/frontend/overview.md"]),
    ("frontend/lib/",           ["docs/frontend/overview.md"]),
    ("run.sh",                  ["docs/dev/how-to-run.md"]),
]

# Python source directories eligible for quality checks
_PYTHON_DIRS = {"backend", "auth", "dashboard", "scripts"}

# Paths that should never be analysed
_SKIP_SEGMENTS = {"demoenv", "__pycache__", "node_modules", ".next", "site", ".git"}

# ---------------------------------------------------------------------------
# Security patterns
# ---------------------------------------------------------------------------

# (compiled regex, description)
_SQL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\.execute\s*\(\s*f[\'"]'),
     "SQL injection: f-string in .execute() — use parameterised queries"),
    (re.compile(r'\.execute\s*\(\s*["\'][^"\']*\{'),
     "SQL injection: string-format in .execute() — use parameterised queries"),
    (re.compile(r'(?:execute|query)\s*\([^)]*\+\s*\w'),
     "SQL injection: concatenation in query call — use parameterised queries"),
]

_XSS_PY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'html\.\w+\(\s*f[\'"].*<[a-zA-Z]'),
     "XSS risk: f-string with HTML tags inside a Dash component"),
    (re.compile(r'innerHTML\s*=\s*f[\'"]'),
     "XSS risk: f-string assigned directly to innerHTML"),
]

_XSS_TS_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'dangerouslySetInnerHTML\s*=\s*\{\{'),
     "XSS risk: dangerouslySetInnerHTML — ensure value is sanitised"),
    (re.compile(r'\.innerHTML\s*=(?!=)'),
     "XSS risk: direct innerHTML assignment"),
    (re.compile(r'document\.write\s*\('),
     "XSS risk: document.write()"),
]


# ---------------------------------------------------------------------------
# Main checker class
# ---------------------------------------------------------------------------

class PreCommitChecker:
    """Orchestrates all four pre-commit quality checks.

    Attributes:
        staged_files: Relative paths of modified / created staged files.
        staged_py: Subset of ``staged_files`` that end in ``.py``.
        staged_ts: Subset of ``staged_files`` that end in ``.ts`` / ``.tsx``.
        has_claude: True when ``ANTHROPIC_API_KEY`` is present in the environment.
        skip_claude: True when ``SKIP_CLAUDE_CHECKS=1`` is set.
        current_branch: Name of the current git branch (from ``GIT_CURRENT_BRANCH`` env var).
        fixes_applied: List of relative paths that were auto-fixed and re-staged.
    """

    def __init__(self) -> None:
        """Initialise the checker by reading the staged file list from git."""
        self.staged_files: List[str] = self._git(
            "diff", "--cached", "--name-only", "--diff-filter=ACM"
        ).splitlines()

        self.staged_py: List[str] = [
            f for f in self.staged_files
            if f.endswith(".py") and self._is_eligible_python(f)
        ]
        self.staged_ts: List[str] = [
            f for f in self.staged_files
            if f.endswith((".ts", ".tsx"))
            and not any(seg in f for seg in _SKIP_SEGMENTS)
        ]
        self.has_claude: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.skip_claude: bool = os.environ.get("SKIP_CLAUDE_CHECKS", "0") == "1"
        self.current_branch: str = os.environ.get("GIT_CURRENT_BRANCH", "unknown")
        self.fixes_applied: List[str] = []

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> int:
        """Execute all four checks in order.

        Returns:
            0 if the commit should proceed, 1 if it should be blocked.
        """
        if not self.staged_files:
            return 0

        blocking = False

        # ── 1. Static code analysis ───────────────────────────────────────
        print(f"\n{_BOLD}── [1/4] Code quality analysis  (branch: {self.current_branch}){_RESET}")
        issues = self._collect_static_issues()

        if issues:
            _info(f"{len(issues)} issue(s) found in staged files.")
            if self.has_claude and not self.skip_claude:
                self._fix_with_claude(issues)
            else:
                if not self.has_claude:
                    _warn("ANTHROPIC_API_KEY not set — static violations shown; no auto-fix.")
                for iss in issues:
                    fn = _err if iss.severity == "error" else _warn
                    fn(f"{iss.filepath}:{iss.line}  [{iss.category}] {iss.description}")
                    if iss.severity == "error":
                        blocking = True
        else:
            _ok("No violations found in staged Python / TypeScript files.")

        # ── 2. Meta-file freshness ────────────────────────────────────────
        print(f"\n{_BOLD}── [2/4] CLAUDE.md / PROGRESS.md / README.md freshness{_RESET}")
        if self.has_claude and not self.skip_claude:
            self._update_meta_files()
        else:
            _warn("Skipping (ANTHROPIC_API_KEY not set or SKIP_CLAUDE_CHECKS=1 — add key to backend/.env to enable).")

        # ── 3. Docs freshness ─────────────────────────────────────────────
        print(f"\n{_BOLD}── [3/4] Documentation freshness{_RESET}")
        if self.has_claude and not self.skip_claude:
            self._update_docs()
        else:
            _warn("Skipping (ANTHROPIC_API_KEY not set or SKIP_CLAUDE_CHECKS=1 — add key to backend/.env to enable).")

        # ── 4. Changelog ordering ─────────────────────────────────────────
        print(f"\n{_BOLD}── [4/4] Changelog date ordering{_RESET}")
        self._fix_changelog_order()

        # ── Summary ───────────────────────────────────────────────────────
        if self.fixes_applied:
            print(f"\n{_BOLD}Auto-fixed and re-staged:{_RESET}")
            for f in self.fixes_applied:
                _fix(f)

        return 1 if blocking else 0

    # ── Check 1: static analysis ──────────────────────────────────────────────

    def _collect_static_issues(self) -> List[Issue]:
        """Collect all static violations from staged Python and TypeScript files.

        Returns:
            List of :class:`Issue` objects sorted by filepath then line number.
        """
        issues: List[Issue] = []

        for rel_path in self.staged_py:
            full_path = _PROJECT_ROOT / rel_path
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=rel_path)
            except SyntaxError as exc:
                issues.append(Issue(rel_path, exc.lineno or 0, "syntax", "error",
                                    f"Syntax error — cannot analyse file: {exc}"))
                continue

            issues.extend(self._check_prints(rel_path, tree))
            issues.extend(self._check_docstrings(rel_path, tree))
            issues.extend(self._check_naming(rel_path, tree))
            issues.extend(self._check_oop(rel_path, tree))
            issues.extend(self._check_security_py(rel_path, source))

        for rel_path in self.staged_ts:
            full_path = _PROJECT_ROOT / rel_path
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8")
            issues.extend(self._check_security_ts(rel_path, source))

        return sorted(issues, key=lambda i: (i.filepath, i.line))

    @staticmethod
    def _check_prints(filepath: str, tree: ast.AST) -> List[Issue]:
        """Return an error Issue for every bare ``print()`` call found.

        Args:
            filepath: Relative source path (for Issue attribution).
            tree: Parsed AST of the file.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                issues.append(Issue(
                    filepath, node.lineno, "print", "error",
                    "Bare print() — replace with logger.info() / logger.debug()",
                ))
        return issues

    @staticmethod
    def _check_docstrings(filepath: str, tree: ast.Module) -> List[Issue]:
        """Return warning Issues for missing module, class, or public-method docstrings.

        ``__init__.py`` files are exempt from the module-docstring check.
        Private/dunder methods (except ``__init__``) are skipped.

        Args:
            filepath: Relative source path.
            tree: Parsed AST of the file.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []

        # Module docstring
        if not filepath.endswith("__init__.py"):
            has_mod_doc = (
                tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)
            )
            if not has_mod_doc:
                issues.append(Issue(filepath, 1, "docstring", "warning",
                                    "Missing module-level docstring"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                has_doc = (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                )
                if not has_doc:
                    issues.append(Issue(
                        filepath, node.lineno, "docstring", "warning",
                        f"Class '{node.name}' is missing a docstring",
                    ))

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip private/dunder except __init__
                if node.name.startswith("_") and node.name != "__init__":
                    continue
                has_doc = (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                )
                if not has_doc:
                    issues.append(Issue(
                        filepath, node.lineno, "docstring", "warning",
                        f"Function/method '{node.name}' is missing a docstring",
                    ))
        return issues

    @staticmethod
    def _check_naming(filepath: str, tree: ast.AST) -> List[Issue]:
        """Return warning Issues for naming-convention violations.

        Rules checked:

        * Class names must be ``PascalCase``.
        * Public function / method names must be ``snake_case``.

        Args:
            filepath: Relative source path.
            tree: Parsed AST of the file.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []
        _pascal = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
        _snake  = re.compile(r"^[a-z_][a-z0-9_]*$")

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if not _pascal.match(node.name):
                    issues.append(Issue(
                        filepath, node.lineno, "naming", "warning",
                        f"Class '{node.name}' should be PascalCase",
                    ))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("__") and name.endswith("__"):
                    continue  # dunders are fine
                if not name.startswith("_") and not _snake.match(name):
                    issues.append(Issue(
                        filepath, node.lineno, "naming", "warning",
                        f"Function '{name}' should be snake_case",
                    ))
        return issues

    @staticmethod
    def _check_oop(filepath: str, tree: ast.Module) -> List[Issue]:
        """Return warning Issues for module-level mutable global assignments.

        Mutable globals (lists, dicts, sets, calls) declared at the module
        scope — outside any function or class — are a common OOP anti-pattern;
        state should live on ``self`` instead.  Constants (``ALL_CAPS``) are
        excluded.

        Args:
            filepath: Relative source path.
            tree: Parsed AST of the file.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []
        _constant_re = re.compile(r"^[A-Z][A-Z0-9_]+$")
        for node in tree.body:  # only top-level statements
            if not isinstance(node, ast.Assign):
                continue
            is_mutable = isinstance(node.value, (ast.List, ast.Dict, ast.Set, ast.Call))
            if not is_mutable:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if not _constant_re.match(target.id):
                        issues.append(Issue(
                            filepath, node.lineno, "oop", "warning",
                            f"Module-level mutable global '{target.id}' — "
                            "consider moving to a class instance attribute",
                        ))
        return issues

    @staticmethod
    def _check_security_py(filepath: str, source: str) -> List[Issue]:
        """Return error Issues for SQL-injection and XSS patterns in Python source.

        Args:
            filepath: Relative source path.
            source: Full file content as a string.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            for pattern, description in _SQL_PATTERNS + _XSS_PY_PATTERNS:
                if pattern.search(line):
                    issues.append(Issue(filepath, lineno, "security", "error", description))
        return issues

    @staticmethod
    def _check_security_ts(filepath: str, source: str) -> List[Issue]:
        """Return warning Issues for XSS patterns in TypeScript / TSX source.

        Args:
            filepath: Relative source path.
            source: Full file content as a string.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            for pattern, description in _XSS_TS_PATTERNS:
                if pattern.search(line):
                    issues.append(Issue(filepath, lineno, "security", "warning", description))
        return issues

    # ── Check 1b: Claude auto-fix ─────────────────────────────────────────────

    def _fix_with_claude(self, issues: List[Issue]) -> None:
        """Send each affected file to Claude for automatic repair.

        Issues are grouped by file.  A single API call is made per file,
        passing the full source and the list of violations.  The response
        (corrected source) is written back to disk and the file is re-staged.

        Args:
            issues: List of violations collected by :meth:`_collect_static_issues`.
        """
        by_file: Dict[str, List[Issue]] = {}
        for iss in issues:
            by_file.setdefault(iss.filepath, []).append(iss)

        for rel_path, file_issues in by_file.items():
            full_path = _PROJECT_ROOT / rel_path
            if not full_path.exists():
                continue

            source = full_path.read_text(encoding="utf-8")
            issues_text = "\n".join(
                f"  Line {i.line} [{i.category}] {i.description}"
                for i in file_issues
            )

            prompt = (
                "You are a Python code quality expert. Fix the following issues in the file "
                f"``{rel_path}`` and return **only** the complete corrected file content "
                "(no markdown fences, no explanation).\n\n"
                "RULES:\n"
                "- Google-style Sphinx docstrings with Args:, Returns:, Raises: sections.\n"
                "- Replace bare print() with the module logger.  Add "
                "  ``logger = logging.getLogger(__name__)`` and ``import logging`` if absent.\n"
                "- Rename symbols to snake_case (functions/variables) or PascalCase (classes).\n"
                "- Move module-level mutable globals into class __init__ if inside a class,\n"
                "  or prefix with _ and add a comment if they must remain module-level.\n"
                "- For security issues: use parameterised DB queries; avoid raw f-strings in HTML.\n"
                "- Python 3.9 compatibility: use ``Optional[X]`` not ``X | Y``.\n"
                "- Do NOT change any logic or remove existing functionality.\n\n"
                f"VIOLATIONS FOUND:\n{issues_text}\n\n"
                f"FILE: {rel_path}\n"
                "```python\n"
                f"{source}\n"
                "```\n\n"
                "Return only the corrected file content:"
            )

            fixed = self._call_claude(prompt, max_tokens=16384)
            if not fixed:
                _warn(f"Claude could not fix {rel_path} — violations remain.")
                continue

            fixed = fixed.strip()
            # Strip markdown fences if Claude added them anyway
            if fixed.startswith("```"):
                fixed = re.sub(r"^```\w*\n?", "", fixed)
                fixed = re.sub(r"\n?```\s*$", "", fixed)

            full_path.write_text(fixed, encoding="utf-8")
            self._stage(rel_path)
            self.fixes_applied.append(rel_path)
            _fix(f"Auto-fixed {len(file_issues)} issue(s) in {rel_path}")

    # ── Check 2: Meta-file freshness ──────────────────────────────────────────

    def _update_meta_files(self) -> None:
        """Review and update CLAUDE.md, PROGRESS.md, and README.md.

        Sends the staged diff plus current file contents to Claude in a single
        API call.  Claude returns a structured JSON payload indicating which
        files need updating and their new content.  Changed files are written
        to disk and re-staged.
        """
        diff = self._git("diff", "--cached")
        if not diff.strip():
            _ok("No staged diff — meta-files are already current.")
            return

        diff_trunc = diff[:10_000] + "\n… [truncated]" if len(diff) > 10_000 else diff

        claude_md = _PROJECT_ROOT / "CLAUDE.md"
        progress_md = _PROJECT_ROOT / "PROGRESS.md"
        readme_md = _PROJECT_ROOT / "README.md"

        def _read(p: Path, limit: int = 8000) -> str:
            """Read a file, returning a truncated string if it is very long.

            Args:
                p: Path to the file.
                limit: Maximum characters to return.

            Returns:
                File contents (possibly truncated) or empty string if absent.
            """
            if not p.exists():
                return ""
            c = p.read_text(encoding="utf-8")
            return c[:limit] + "\n… [truncated]" if len(c) > limit else c

        today = datetime.now().strftime("%b %d, %Y")

        prompt = (
            "You are maintaining documentation for the ai-agent-ui project.\n\n"
            f"STAGED DIFF:\n{diff_trunc}\n\n"
            f"TODAY'S DATE: {today}\n\n"
            f"CURRENT CLAUDE.md:\n{_read(claude_md)}\n\n"
            f"CURRENT PROGRESS.md:\n{_read(progress_md)}\n\n"
            f"CURRENT README.md:\n{_read(readme_md)}\n\n"
            "TASKS:\n"
            "1. CLAUDE.md: Update ONLY sections affected by the diff (project structure,\n"
            "   API, decisions, architecture notes).  Do NOT rewrite unrelated sections.\n"
            "   If nothing changed, set claude_md_updated to false.\n\n"
            "2. PROGRESS.md: Prepend a new session entry at the TOP of the file (after\n"
            "   the first heading line) summarising today's changes.  If an entry for\n"
            "   today already exists, append to it.  Follow the existing format.\n"
            "   If nothing changed, set progress_md_updated to false.\n\n"
            "3. README.md: This is the project's main README.  Update it to reflect any\n"
            "   new features, changed services, or updated setup steps.  If it does not\n"
            "   exist, create a concise, well-structured README from scratch based on the\n"
            "   project context.  If it is already up to date, set readme_updated to false.\n\n"
            "Return ONLY valid JSON with this exact structure:\n"
            "{\n"
            '  "claude_md_updated": true/false,\n'
            '  "claude_md": "<full updated content or null>",\n'
            '  "progress_md_updated": true/false,\n'
            '  "progress_md": "<full updated content or null>",\n'
            '  "readme_updated": true/false,\n'
            '  "readme": "<full updated content or null>"\n'
            "}"
        )

        response = self._call_claude(prompt, max_tokens=16384)
        if not response:
            _warn("Meta-file update skipped (API call failed).")
            return

        try:
            data = self._parse_json_response(response)
        except (json.JSONDecodeError, ValueError) as exc:
            _warn(f"Could not parse meta-file response: {exc}")
            return

        for key, path, label in [
            ("claude_md",    claude_md,    "CLAUDE.md"),
            ("progress_md",  progress_md,  "PROGRESS.md"),
            ("readme",       readme_md,    "README.md"),
        ]:
            updated_flag = data.get(f"{key}_updated") or data.get(f"{key.split('_')[0]}_updated", False)
            # Handle both "claude_md_updated" and "readme_updated" key styles
            if key == "readme":
                updated_flag = data.get("readme_updated", False)
            content = data.get(key)
            if updated_flag and content:
                path.write_text(content, encoding="utf-8")
                rel = str(path.relative_to(_PROJECT_ROOT))
                self._stage(rel)
                self.fixes_applied.append(rel)
                _fix(f"Updated {label}")
            else:
                _ok(f"{label} is up to date.")

    # ── Check 3: Docs freshness ───────────────────────────────────────────────

    def _update_docs(self) -> None:
        """Identify and update stale documentation pages for staged source files.

        Each staged file is matched against :data:`_DOCS_MAP`.  For each
        affected docs page, Claude is asked whether it needs patching and, if
        so, returns the updated Markdown.  Changed pages are re-staged.

        After all pages are processed, :meth:`_run_mkdocs_build` is called to
        verify that the documentation site still builds correctly.  A build
        failure is reported as a warning but never blocks the commit (that is
        the pre-push hook's responsibility).
        """
        affected: Dict[str, bool] = {}
        for staged in self.staged_files:
            for prefix, doc_paths in _DOCS_MAP:
                if staged.startswith(prefix) or staged == prefix:
                    for dp in doc_paths:
                        doc_full = _PROJECT_ROOT / dp
                        if doc_full.exists():
                            affected[dp] = True

        if not affected:
            _ok("No docs pages mapped to the staged changes.")
            # Still validate mkdocs build as a final sanity check (non-blocking)
            if self._run_mkdocs_build():
                _ok("mkdocs build passed.")
            return

        diff = self._git("diff", "--cached")
        diff_trunc = diff[:8_000] + "\n… [truncated]" if len(diff) > 8_000 else diff

        _info(f"Checking {len(affected)} docs page(s)…")

        for doc_rel in affected:
            doc_path = _PROJECT_ROOT / doc_rel
            current = doc_path.read_text(encoding="utf-8")
            current_trunc = current[:8_000] + "\n… [truncated]" if len(current) > 8_000 else current

            prompt = (
                "You are maintaining MkDocs documentation for the ai-agent-ui project.\n\n"
                f"STAGED DIFF:\n{diff_trunc}\n\n"
                f"CURRENT CONTENT OF {doc_rel}:\n{current_trunc}\n\n"
                "Does this page need updating based on the diff?  Rules:\n"
                "- Patch only stale or missing content.  Preserve all existing correct sections.\n"
                "- Match the existing heading style and formatting.\n"
                "- Be concise.\n\n"
                "Return ONLY valid JSON:\n"
                '{"needs_update": true/false, "updated_content": "<full markdown or null>"}'
            )

            response = self._call_claude(prompt, max_tokens=8_192)
            if not response:
                _warn(f"Could not check {doc_rel} (API call failed).")
                continue

            try:
                data = self._parse_json_response(response)
            except (json.JSONDecodeError, ValueError) as exc:
                _warn(f"Could not parse docs response for {doc_rel}: {exc}")
                continue

            if data.get("needs_update") and data.get("updated_content"):
                doc_path.write_text(data["updated_content"], encoding="utf-8")
                self._stage(doc_rel)
                self.fixes_applied.append(doc_rel)
                _fix(f"Updated {doc_rel}")
            else:
                _ok(f"{doc_rel} is up to date.")

        # Validate mkdocs build after any LLM-driven doc updates (non-blocking)
        if self._run_mkdocs_build():
            _ok("mkdocs build passed after doc updates.")
        else:
            _warn("mkdocs build failed — fix before pushing (pre-push hook will block).")

    # ── Check 3b: mkdocs build validation ────────────────────────────────────

    def _run_mkdocs_build(self) -> bool:
        """Run ``mkdocs build`` in a temp directory and return True if it passes.

        Uses the project's demoenv mkdocs binary so that all MkDocs plugins
        and extensions are available.  The generated site is written to a
        temporary directory and cleaned up immediately after.

        Returns:
            ``True`` if ``mkdocs build`` exits with code 0, ``False`` otherwise.
        """
        import tempfile
        mkdocs_bin = _PROJECT_ROOT / "backend" / "demoenv" / "bin" / "mkdocs"
        if not mkdocs_bin.exists():
            _warn("mkdocs not found in demoenv — skipping build validation.")
            return True  # non-blocking: assume OK if mkdocs isn't installed
        tmpdir = tempfile.mkdtemp(prefix="pre_commit_mkdocs_")
        try:
            result = subprocess.run(
                [
                    str(mkdocs_bin),
                    "build",
                    "--config-file", str(_PROJECT_ROOT / "mkdocs.yml"),
                    "--site-dir", tmpdir,
                    "--quiet",
                ],
                cwd=_PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                _warn("mkdocs build failed after doc updates:")
                for line in (result.stdout + result.stderr).splitlines():
                    if line.strip():
                        _warn(f"  {line}")
                return False
            return True
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Check 4: Changelog ordering ───────────────────────────────────────────

    def _fix_changelog_order(self) -> None:
        """Verify and fix the date ordering in docs/dev/changelog.md.

        Parses all H2 (``## ``) headings, extracts the date from each,
        sorts sections in descending order (newest first), and rewrites the
        file if the order was incorrect.  A ``## Known Issues`` section is
        always moved to the very end of the file.

        The fix is deterministic and never requires an API call.
        """
        changelog = _PROJECT_ROOT / "docs" / "dev" / "changelog.md"
        if not changelog.exists():
            _warn(f"Changelog not found at {changelog} — skipping.")
            return

        content = changelog.read_text(encoding="utf-8")
        # Split on each H2 heading (captured), keeping the heading itself
        parts = re.split(r"^(## .+)$", content, flags=re.MULTILINE)

        if len(parts) < 3:
            _ok("Changelog has no H2 sections — nothing to reorder.")
            return

        preamble = parts[0]
        sections: List[Tuple[str, str]] = []
        for idx in range(1, len(parts), 2):
            heading = parts[idx]
            body = parts[idx + 1] if idx + 1 < len(parts) else ""
            sections.append((heading, body))

        def _parse_date(heading: str) -> datetime:
            """Extract a date from a changelog H2 heading string.

            Args:
                heading: The full heading text, e.g.
                    ``"## Feb 25, 2026 (continued — auth)"``

            Returns:
                Parsed :class:`datetime`, or :data:`datetime.min` if unparseable.
            """
            m = re.search(
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"\s+(\d{1,2}),?\s+(\d{4})",
                heading,
            )
            if m:
                try:
                    return datetime.strptime(
                        f"{m.group(1)} {int(m.group(2))} {m.group(3)}",
                        "%b %d %Y",
                    )
                except ValueError:
                    pass
            return datetime.min

        # Separate dated sections from the "Known Issues" footer
        regular: List[Tuple[str, str]] = []
        footer: List[Tuple[str, str]] = []
        for h, b in sections:
            if re.search(r"known issues|pending work", h, re.IGNORECASE):
                footer.append((h, b))
            else:
                regular.append((h, b))

        dates = [_parse_date(h) for h, _ in regular]

        # Check whether already ordered (non-increasing)
        already_ordered = all(
            dates[i] >= dates[i + 1]
            for i in range(len(dates) - 1)
            if dates[i] != datetime.min and dates[i + 1] != datetime.min
        )
        if already_ordered:
            _ok("Changelog dates are in correct descending order.")
            return

        # Stable sort preserves relative order for same-date entries
        regular.sort(key=lambda s: _parse_date(s[0]), reverse=True)

        new_content = preamble
        for h, b in regular + footer:
            new_content += h + b

        changelog.write_text(new_content, encoding="utf-8")
        self._stage("docs/dev/changelog.md")
        self.fixes_applied.append("docs/dev/changelog.md")
        _fix("Reordered changelog sections to newest-first order.")

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _is_eligible_python(rel_path: str) -> bool:
        """Return True if *rel_path* is inside a directory eligible for checks.

        Skips virtualenv, cache, and node_modules subtrees.

        Args:
            rel_path: Relative path from the project root.

        Returns:
            ``True`` when the file should be analysed.
        """
        if any(seg in rel_path for seg in _SKIP_SEGMENTS):
            return False
        top = rel_path.split("/")[0]
        return top in _PYTHON_DIRS

    def _call_claude(self, prompt: str, max_tokens: int = 8_192) -> Optional[str]:
        """Make a single Anthropic API call and return the text response.

        Uses the ``anthropic`` SDK with ``claude-sonnet-4-6``.  The API key
        is read from ``os.environ["ANTHROPIC_API_KEY"]``.  Returns ``None``
        gracefully when the key is absent or the call fails, so callers can
        skip LLM-powered features without blocking the commit.

        Args:
            prompt: The user-turn prompt to send to Claude.
            max_tokens: Upper bound on response tokens.

        Returns:
            Response text string, or ``None`` if the key is absent or the call failed.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            import anthropic  # type: ignore[import]
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except ImportError:
            _warn("anthropic SDK not installed — run: pip install anthropic")
            return None
        except Exception as exc:
            _warn(f"Anthropic API call failed: {exc}")
            return None

    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
        """Strip any markdown fences from *raw* and parse as JSON.

        Args:
            raw: Raw string returned by Claude, possibly wrapped in
                 ````json … ``` `` fences.

        Returns:
            Parsed dictionary.

        Raises:
            json.JSONDecodeError: If the stripped string is not valid JSON.
        """
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        return json.loads(text)

    def _stage(self, rel_path: str) -> None:
        """Add *rel_path* to the git staging area.

        Args:
            rel_path: Relative path from the project root.
        """
        subprocess.run(
            ["git", "add", rel_path],
            cwd=_PROJECT_ROOT,
            capture_output=True,
        )

    def _git(self, *args: str) -> str:
        """Run a git sub-command and return its stdout.

        Args:
            *args: Git arguments (e.g. ``"diff"``, ``"--cached"``).

        Returns:
            Decoded stdout string (empty string on error).
        """
        result = subprocess.run(
            ["git"] + list(args),
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        return result.stdout


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    checker = PreCommitChecker()
    sys.exit(checker.run())
