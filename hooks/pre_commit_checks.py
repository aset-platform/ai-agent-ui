#!/usr/bin/env python3
"""Pre-commit quality gate for the ai-agent-ui project.

Invoked by ``hooks/pre-commit`` with no arguments.  Retrieves the list of
modified / newly-created staged files internally via ``git diff --cached``.

Checks performed (in order):

1. **Static code analysis** (always runs, no API required):

   * Bare ``print()`` calls in backend Python — reported as errors.
   * Missing Google-style docstrings (module / class / public method).
   * Naming-convention violations (snake_case functions, PascalCase classes).
   * OOP issues — module-level mutable globals that belong on ``self``.
   * XSS risks — f-strings with HTML tags in Dash components or TS files.
   * SQL-injection risks — f-strings / concatenation inside ``execute()`` calls.

   Errors (print, security) block the commit.  Warnings (docstring, naming,
   oop) are reported but do not block.

2. **MkDocs build validation** (always runs, no API required):

   Runs ``mkdocs build`` in a temp directory.  Build failure is reported as a
   warning but never blocks the commit (that is the pre-push hook's job).

3. **Changelog date ordering** (always runs, no API required):

   ``docs/dev/changelog.md`` H2 headings are parsed, verified to be in
   descending date order (newest first), and rewritten if they are not.

Exit codes:
    0  All checks passed — commit proceeds.
    1  A blocking violation was found (syntax error, bare print(), or
       security issue in staged Python).

Example::

    # Run directly for testing (no commit needed)
    python hooks/pre_commit_checks.py
"""

import ast
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Bootstrap — project root
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"


def _c(text: str, colour: str) -> str:
    """Wrap *text* in an ANSI escape sequence if stdout is a TTY.

    Args:
        text: The string to colourise.
        colour: ANSI escape code to apply.

    Returns:
        Colourised string, or plain *text* when not a TTY.
    """
    return f"{colour}{text}{_RESET}" if sys.stdout.isatty() else text


def _ok(msg: str) -> None:
    print(f"  {_c('✅', _GREEN)}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {_c('⚠️ ', _YELLOW)} {msg}")


def _err(msg: str) -> None:
    print(f"  {_c('❌', _RED)}  {msg}")


def _info(msg: str) -> None:
    print(f"  {_c('ℹ️ ', _CYAN)}  {msg}")


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
# Source → docs mapping (kept for reference; no longer used at commit time)
# ---------------------------------------------------------------------------

# Python source directories eligible for quality checks
_PYTHON_DIRS = {"backend", "auth", "dashboard", "scripts"}

# Paths that should never be analysed
_SKIP_SEGMENTS = {
    "demoenv",
    "__pycache__",
    "node_modules",
    ".next",
    "site",
    ".git",
}

# ---------------------------------------------------------------------------
# Security patterns
# ---------------------------------------------------------------------------

_SQL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r'\.execute\s*\(\s*f[\'"]'),
        "SQL injection: f-string in .execute() — use parameterised queries",
    ),
    (
        re.compile(r'\.execute\s*\(\s*["\'][^"\']*\{'),
        "SQL injection: string-format in .execute() — use parameterised queries",
    ),
    (
        re.compile(r"(?:execute|query)\s*\([^)]*\+\s*\w"),
        "SQL injection: concatenation in query call — use parameterised queries",
    ),
]

_XSS_PY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r'html\.\w+\(\s*f[\'"].*<[a-zA-Z]'),
        "XSS risk: f-string with HTML tags inside a Dash component",
    ),
    (
        re.compile(r'innerHTML\s*=\s*f[\'"]'),
        "XSS risk: f-string assigned directly to innerHTML",
    ),
]

_XSS_TS_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r"dangerouslySetInnerHTML\s*=\s*\{\{"),
        "XSS risk: dangerouslySetInnerHTML — ensure value is sanitised",
    ),
    (
        re.compile(r"\.innerHTML\s*=(?!=)"),
        "XSS risk: direct innerHTML assignment",
    ),
    (re.compile(r"document\.write\s*\("), "XSS risk: document.write()"),
]


# ---------------------------------------------------------------------------
# Main checker class
# ---------------------------------------------------------------------------


class PreCommitChecker:
    """Orchestrates all three pre-commit quality checks.

    Attributes:
        staged_files: Relative paths of modified / created staged files.
        staged_py: Subset of ``staged_files`` that end in ``.py``.
        staged_ts: Subset of ``staged_files`` that end in ``.ts`` / ``.tsx``.
        current_branch: Name of the current git branch.
    """

    def __init__(self) -> None:
        """Initialise the checker by reading the staged file list from git."""
        self.staged_files: List[str] = self._git(
            "diff", "--cached", "--name-only", "--diff-filter=ACM"
        ).splitlines()

        self.staged_py: List[str] = [
            f
            for f in self.staged_files
            if f.endswith(".py") and self._is_eligible_python(f)
        ]
        self.staged_ts: List[str] = [
            f
            for f in self.staged_files
            if f.endswith((".ts", ".tsx"))
            and not any(seg in f for seg in _SKIP_SEGMENTS)
        ]
        self.current_branch: str = os.environ.get(
            "GIT_CURRENT_BRANCH", "unknown"
        )

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> int:
        """Execute all three checks in order.

        Returns:
            0 if the commit should proceed, 1 if it should be blocked.
        """
        if not self.staged_files:
            return 0

        blocking = False

        # ── 1. Static code analysis ───────────────────────────────────────
        print(
            f"\n{_BOLD}── [1/3] Code quality analysis  (branch: {self.current_branch}){_RESET}"
        )
        issues = self._collect_static_issues()

        if issues:
            errors = [i for i in issues if i.severity == "error"]
            warnings = [i for i in issues if i.severity == "warning"]
            if errors:
                _info(
                    f"{len(errors)} error(s) found — commit will be blocked."
                )
                for iss in errors:
                    _err(
                        f"{iss.filepath}:{iss.line}  [{iss.category}] {iss.description}"
                    )
                blocking = True
            if warnings:
                _info(f"{len(warnings)} warning(s) found (non-blocking).")
                for iss in warnings:
                    _warn(
                        f"{iss.filepath}:{iss.line}  [{iss.category}] {iss.description}"
                    )
        else:
            _ok("No violations found in staged Python / TypeScript files.")

        # ── 2. MkDocs build validation ────────────────────────────────────
        print(f"\n{_BOLD}── [2/3] MkDocs build validation{_RESET}")
        if self._run_mkdocs_build():
            _ok("mkdocs build passed.")
        else:
            _warn(
                "mkdocs build failed — fix before pushing (pre-push hook will block)."
            )

        # ── 3. Changelog date ordering ────────────────────────────────────
        print(f"\n{_BOLD}── [3/3] Changelog date ordering{_RESET}")
        self._fix_changelog_order()

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
                issues.append(
                    Issue(
                        rel_path,
                        exc.lineno or 0,
                        "syntax",
                        "error",
                        f"Syntax error — cannot analyse file: {exc}",
                    )
                )
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
                issues.append(
                    Issue(
                        filepath,
                        node.lineno,
                        "print",
                        "error",
                        "Bare print() — replace with logger.info() / logger.debug()",
                    )
                )
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

        if not filepath.endswith("__init__.py"):
            has_mod_doc = (
                tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)
            )
            if not has_mod_doc:
                issues.append(
                    Issue(
                        filepath,
                        1,
                        "docstring",
                        "warning",
                        "Missing module-level docstring",
                    )
                )

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                has_doc = (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                )
                if not has_doc:
                    issues.append(
                        Issue(
                            filepath,
                            node.lineno,
                            "docstring",
                            "warning",
                            f"Class '{node.name}' is missing a docstring",
                        )
                    )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") and node.name != "__init__":
                    continue
                has_doc = (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                )
                if not has_doc:
                    issues.append(
                        Issue(
                            filepath,
                            node.lineno,
                            "docstring",
                            "warning",
                            f"Function/method '{node.name}' is missing a docstring",
                        )
                    )
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
        _snake = re.compile(r"^[a-z_][a-z0-9_]*$")

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if not _pascal.match(node.name):
                    issues.append(
                        Issue(
                            filepath,
                            node.lineno,
                            "naming",
                            "warning",
                            f"Class '{node.name}' should be PascalCase",
                        )
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("__") and name.endswith("__"):
                    continue
                if not name.startswith("_") and not _snake.match(name):
                    issues.append(
                        Issue(
                            filepath,
                            node.lineno,
                            "naming",
                            "warning",
                            f"Function '{name}' should be snake_case",
                        )
                    )
        return issues

    @staticmethod
    def _check_oop(filepath: str, tree: ast.Module) -> List[Issue]:
        """Return warning Issues for module-level mutable global assignments.

        Mutable globals (lists, dicts, sets, calls) declared at the module
        scope outside any function or class are a common OOP anti-pattern.
        Constants (``ALL_CAPS``) are excluded.

        Args:
            filepath: Relative source path.
            tree: Parsed AST of the file.

        Returns:
            List of :class:`Issue` objects (may be empty).
        """
        issues = []
        _constant_re = re.compile(r"^[A-Z][A-Z0-9_]+$")
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            is_mutable = isinstance(
                node.value, (ast.List, ast.Dict, ast.Set, ast.Call)
            )
            if not is_mutable:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if not _constant_re.match(target.id):
                        issues.append(
                            Issue(
                                filepath,
                                node.lineno,
                                "oop",
                                "warning",
                                f"Module-level mutable global '{target.id}' — "
                                "consider moving to a class instance attribute",
                            )
                        )
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
                    issues.append(
                        Issue(
                            filepath, lineno, "security", "error", description
                        )
                    )
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
                    issues.append(
                        Issue(
                            filepath,
                            lineno,
                            "security",
                            "warning",
                            description,
                        )
                    )
        return issues

    # ── Check 2: MkDocs build ─────────────────────────────────────────────────

    def _run_mkdocs_build(self) -> bool:
        """Run ``mkdocs build`` in a temp directory and return True if it passes.

        Uses the project venv mkdocs binary.  The generated site is
        written to a temporary directory and cleaned up immediately after.

        Returns:
            ``True`` if ``mkdocs build`` exits with code 0, ``False`` otherwise.
        """
        import tempfile

        _venv_home = (
            Path(
                os.environ.get(
                    "AI_AGENT_UI_HOME",
                    Path.home() / ".ai-agent-ui",
                )
            )
            / "venv"
        )
        mkdocs_bin = _venv_home / "bin" / "mkdocs"
        if not mkdocs_bin.exists():
            # Backwards compat: try old project-local path
            mkdocs_bin = (
                _PROJECT_ROOT / "backend" / "demoenv" / "bin" / "mkdocs"
            )
        if not mkdocs_bin.exists():
            _warn("mkdocs not found — skipping build validation.")
            return True
        tmpdir = tempfile.mkdtemp(prefix="pre_commit_mkdocs_")
        try:
            result = subprocess.run(
                [
                    str(mkdocs_bin),
                    "build",
                    "--config-file",
                    str(_PROJECT_ROOT / "mkdocs.yml"),
                    "--site-dir",
                    tmpdir,
                    "--quiet",
                ],
                cwd=_PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                _warn("mkdocs build failed:")
                for line in (result.stdout + result.stderr).splitlines():
                    if line.strip():
                        _warn(f"  {line}")
                return False
            return True
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Check 3: Changelog ordering ───────────────────────────────────────────

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

        regular: List[Tuple[str, str]] = []
        footer: List[Tuple[str, str]] = []
        for h, b in sections:
            if re.search(r"known issues|pending work", h, re.IGNORECASE):
                footer.append((h, b))
            else:
                regular.append((h, b))

        dates = [_parse_date(h) for h, _ in regular]
        already_ordered = all(
            dates[i] >= dates[i + 1]
            for i in range(len(dates) - 1)
            if dates[i] != datetime.min and dates[i + 1] != datetime.min
        )
        if already_ordered:
            _ok("Changelog dates are in correct descending order.")
            return

        regular.sort(key=lambda s: _parse_date(s[0]), reverse=True)
        new_content = preamble
        for h, b in regular + footer:
            new_content += h + b

        changelog.write_text(new_content, encoding="utf-8")
        subprocess.run(
            ["git", "add", "docs/dev/changelog.md"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
        )
        print(
            f"  {_c('🔧', _CYAN)}  Reordered changelog sections to newest-first order."
        )

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _is_eligible_python(rel_path: str) -> bool:
        """Return True if *rel_path* is inside a directory eligible for checks.

        Args:
            rel_path: Relative path from the project root.

        Returns:
            ``True`` when the file should be analysed.
        """
        if any(seg in rel_path for seg in _SKIP_SEGMENTS):
            return False
        top = rel_path.split("/")[0]
        return top in _PYTHON_DIRS

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
