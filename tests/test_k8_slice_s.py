"""Tests for K8 Slice S: observability fixes.

Bug #31 — ingestion_guard bare except → logging
Bug #32 — date.today() → datetime.now(timezone.utc).date()
Bug #39 — daily_obs_append fetch_utc as parameter
Bug #54 — chain_reconciliation _emit_rescue_event stub → logging
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ZEUS_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Bug #31: no bare `except Exception: pass` in ingestion_guard
# ---------------------------------------------------------------------------

class TestNoBareExceptPass:
    """Bug #31: _log_availability_failure must log, not silently pass."""

    def test_no_bare_except_pass_in_source(self):
        """Static check: ingestion_guard.py must not have `except Exception: pass`."""
        source = (ZEUS_ROOT / "src" / "data" / "ingestion_guard.py").read_text()
        # Allow "except Exception as" but not bare "except Exception:\n...pass"
        bare_pass = re.findall(r'except\s+Exception\s*:\s*\n\s*pass', source)
        assert len(bare_pass) == 0, (
            f"Found bare `except Exception: pass` in ingestion_guard.py: {bare_pass}"
        )


# ---------------------------------------------------------------------------
# Bug #32: no date.today() in ingestion_guard
# ---------------------------------------------------------------------------

class TestNoDateToday:
    """Bug #32: ingestion_guard must use UTC-aware date, not local date.today()."""

    def test_no_date_today_in_source(self):
        source = (ZEUS_ROOT / "src" / "data" / "ingestion_guard.py").read_text()
        occurrences = re.findall(r'date\.today\(\)', source)
        assert len(occurrences) == 0, (
            f"Found {len(occurrences)} uses of date.today() in ingestion_guard.py"
        )


# ---------------------------------------------------------------------------
# Bug #39: fetch_utc parameter in _build_atom_pair
# ---------------------------------------------------------------------------

class TestFetchUtcParameter:
    """Bug #39: _build_atom_pair must accept fetch_utc kwarg."""

    def test_signature_accepts_fetch_utc(self):
        """Static check: _build_atom_pair has fetch_utc parameter."""
        source = (ZEUS_ROOT / "src" / "data" / "daily_obs_append.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_build_atom_pair":
                arg_names = [a.arg for a in node.args.kwonlyargs]
                assert "fetch_utc" in arg_names, (
                    f"_build_atom_pair missing fetch_utc parameter. Args: {arg_names}"
                )
                return
        pytest.fail("_build_atom_pair not found in daily_obs_append.py")


# ---------------------------------------------------------------------------
# Bug #54: _emit_rescue_event is not a stub
# ---------------------------------------------------------------------------

class TestEmitRescueEventNotStub:
    """Bug #54: _emit_rescue_event must do something, not `pass`."""

    def test_not_a_pass_stub(self):
        """Static check: the function body must not be just `pass`."""
        source = (ZEUS_ROOT / "src" / "state" / "chain_reconciliation.py").read_text()
        # Find _emit_rescue_event definition and check it's not just pass
        match = re.search(
            r'def _emit_rescue_event\([^)]*\)[^:]*:\s*\n((?:\s+.*\n)*)',
            source,
        )
        assert match, "_emit_rescue_event not found"
        body = match.group(1).strip()
        # Body should not be just a comment + pass
        lines = [l.strip() for l in body.split('\n') if l.strip() and not l.strip().startswith('#')]
        assert lines != ['pass'], (
            f"_emit_rescue_event is still a stub: {body!r}"
        )
