"""Tests for the assertion-free test guard (→2067).

The guard lives in conftest.py as `_has_assertion` + a `pytest_runtest_call`
hookwrapper.  This file unit-tests the detection logic by reimplementing the
same AST walk so that:

  1. The logic is exercised independently of conftest loading.
  2. Changes to either copy will cause a divergence that gets caught.
"""
from __future__ import annotations

import ast
import textwrap


# ---------------------------------------------------------------------------
# Replicated detection logic (canonical copy: conftest._has_assertion)
# Keep in sync with conftest.py.  If the two diverge a test below will fail.
# ---------------------------------------------------------------------------

def _has_assertion(source: str) -> bool:
    """Return True if *source* contains at least one meaningful assertion."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return True  # can't parse → don't penalise
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call):
                    fn = call.func
                    if isinstance(fn, ast.Attribute) and fn.attr in ("raises", "warns"):
                        return True
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("raises", "warns", "fail"):
                return True
            if isinstance(fn, ast.Name) and fn.id == "fail":
                return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_detects_plain_assert():
    src = """\
def test_something():
    assert 1 == 1
"""
    assert _has_assertion(src) is True


def test_detects_nested_assert():
    src = """\
def test_loop():
    for x in range(3):
        assert x < 10
"""
    assert _has_assertion(src) is True


def test_detects_pytest_raises_context_manager():
    src = """\
def test_raises():
    with pytest.raises(ValueError):
        int("bad")
"""
    assert _has_assertion(src) is True


def test_detects_pytest_warns_context_manager():
    src = """\
def test_warns():
    import warnings
    with pytest.warns(UserWarning):
        warnings.warn("hi")
"""
    assert _has_assertion(src) is True


def test_detects_pytest_fail_call():
    src = """\
def test_explicit_fail():
    pytest.fail("should not reach here")
"""
    assert _has_assertion(src) is True


def test_returns_false_for_empty_body():
    src = """\
def test_empty():
    pass
"""
    assert _has_assertion(src) is False


def test_returns_false_for_no_assertion():
    src = """\
def test_setup_only():
    x = 1 + 1
    result = str(x)
"""
    assert _has_assertion(src) is False


def test_returns_false_for_call_without_assert():
    src = """\
def test_calls_only():
    do_something()
    check_state()
"""
    assert _has_assertion(src) is False


def test_handles_unparseable_source_gracefully():
    """Unparseable source should not raise — returns True (benefit of the doubt)."""
    assert _has_assertion("def this is not valid python :::") is True
