"""Syntax and structure tests for dashboard layout and callback modules.

All tests are pure Python (no browser / Selenium).  We use :mod:`ast` to
verify that both modules parse without errors and that key identifiers are
present.
"""

import ast
from pathlib import Path

import pytest

_DASHBOARD_DIR = Path(__file__).parent.parent.parent / "dashboard"
_LAYOUTS_PKG = _DASHBOARD_DIR / "layouts"
_CALLBACKS_PKG = _DASHBOARD_DIR / "callbacks"
_APP_PY = _DASHBOARD_DIR / "app.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    """Parse a Python source file and return the AST root."""
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def _top_level_names(tree: ast.Module):
    """Return a set of names defined at the module top-level."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


# ---------------------------------------------------------------------------
# layouts.py
# ---------------------------------------------------------------------------


class TestLayoutsParse:
    """Verify dashboard/layouts/ package parses and exposes key components."""

    def test_layouts_parses_without_errors(self):
        """All sub-modules in the layouts package must parse without errors."""
        assert (
            _LAYOUTS_PKG.is_dir()
        ), "dashboard/layouts/ package directory does not exist"
        for py_file in _LAYOUTS_PKG.glob("*.py"):
            tree = _parse(py_file)
            assert isinstance(
                tree, ast.Module
            ), f"{py_file.name} failed to parse"

    def test_layouts_has_module_docstring(self):
        """The layouts package __init__.py must have a module-level docstring."""
        init_py = _LAYOUTS_PKG / "__init__.py"
        tree = _parse(init_py)
        assert (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
        ), "layouts/__init__.py is missing a module-level docstring"

    def test_layouts_defines_key_functions(self):
        """Expected layout factory functions must be defined across sub-modules."""
        layout_fns = {
            "analysis_tabs_layout",
            "home_layout",
            "insights_layout",
        }
        found_names: set = set()
        for py_file in _LAYOUTS_PKG.glob("*.py"):
            tree = _parse(py_file)
            found_names |= _top_level_names(tree)
        assert layout_fns <= found_names, (
            f"Missing layout functions {layout_fns - found_names} in layouts/ package. "
            f"Defined names: {found_names}"
        )


# ---------------------------------------------------------------------------
# callbacks.py
# ---------------------------------------------------------------------------


class TestCallbacksParse:
    """Verify dashboard/callbacks/ package parses and exposes key functions."""

    def test_callbacks_parses_without_errors(self):
        """All sub-modules in the callbacks package must parse without errors."""
        assert (
            _CALLBACKS_PKG.is_dir()
        ), "dashboard/callbacks/ package directory does not exist"
        for py_file in _CALLBACKS_PKG.glob("*.py"):
            tree = _parse(py_file)
            assert isinstance(
                tree, ast.Module
            ), f"{py_file.name} failed to parse"

    def test_callbacks_has_module_docstring(self):
        """The callbacks package __init__.py must have a module-level docstring."""
        init_py = _CALLBACKS_PKG / "__init__.py"
        tree = _parse(init_py)
        assert (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
        ), "callbacks/__init__.py is missing a module-level docstring"

    def test_callbacks_defines_get_market(self):
        """_get_market must be defined in callbacks/utils.py."""
        utils_py = _CALLBACKS_PKG / "utils.py"
        tree = _parse(utils_py)
        names = _top_level_names(tree)
        assert (
            "_get_market" in names
        ), "_get_market not found in callbacks/utils.py"

    def test_callbacks_defines_validate_token(self):
        """_validate_token must be defined in callbacks/auth_utils.py."""
        auth_py = _CALLBACKS_PKG / "auth_utils.py"
        tree = _parse(auth_py)
        names = _top_level_names(tree)
        assert (
            "_validate_token" in names
        ), "_validate_token not found in callbacks/auth_utils.py"


# ---------------------------------------------------------------------------
# app.py
# ---------------------------------------------------------------------------


class TestAppParse:
    """Verify dashboard/app.py parses without errors."""

    def test_app_parses_without_errors(self):
        tree = _parse(_APP_PY)
        assert isinstance(tree, ast.Module)
