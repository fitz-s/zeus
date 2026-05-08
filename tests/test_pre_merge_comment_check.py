# Created: 2026-05-08
# Last reused or audited: 2026-05-08
# Authority basis: bundled followup B2 spec (operator approved 2026-05-08)
"""Tests for the pre_merge_comment_check BLOCKING hook (B2).

Cases:
  1. PR age < 600s  -> BLOCK
  2. Unresolved Codex P1 thread -> BLOCK
  3. Resolved Codex P1 thread -> no block
  4. CHANGES_REQUESTED review -> BLOCK
  5. ZEUS_PR_MERGE_FORCE=1 bypass -> allow (advisory only)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build realistic payloads
# ---------------------------------------------------------------------------

def _payload(pr_num: int = 42) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_input": {"command": f"gh pr merge {pr_num}"},
    }


def _pr_view_output(age_seconds: float) -> str:
    created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return json.dumps({"createdAt": created_at.strftime("%Y-%m-%dT%H:%M:%SZ")})


def _repo_view_output(owner: str = "acme", name: str = "zeus") -> str:
    return json.dumps({"owner": {"login": owner}, "name": name})


def _gql_output(threads: list[dict]) -> str:
    """Build GraphQL response for reviewThreads."""
    return json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"nodes": threads}
                }
            }
        }
    })


def _reviews_output(reviews: list[dict]) -> str:
    # Matches the shape produced by the --jq filter: [{state, login}]
    return json.dumps([{"state": r["state"], "login": r["login"]} for r in reviews])


def _codex_thread(resolved: bool, badge: str = "P1") -> dict:
    return {
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {
                    "author": {"login": "chatgpt-codex-connector[bot]"},
                    "body": f"![{badge} Badge](https://example.com/{badge}.svg) Found an issue.",
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Patch factory: simulates subprocess.run responses in sequence
# ---------------------------------------------------------------------------

def _make_run_side_effect(responses: list[tuple[int, str]]):
    """Return a side_effect callable that pops from responses list in call order."""
    calls = list(responses)  # copy

    def _run(*args, **kwargs):
        if not calls:
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            return r
        code, out = calls.pop(0)
        r = MagicMock()
        r.returncode = code
        r.stdout = out
        r.stderr = ""
        return r

    return _run


# ---------------------------------------------------------------------------
# Import target under test
# ---------------------------------------------------------------------------

from src.engine import replay  # noqa: F401 (ensure repo is importable)
# Import dispatch module — suppress its boot self-test stderr noise
import importlib
import io


def _import_dispatch() -> ModuleType:
    # Redirect stderr during import to suppress boot self-test output in test
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        if "dispatch" in sys.modules:
            mod = sys.modules.get(".claude.hooks.dispatch")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dispatch",
            os.path.join(
                os.path.dirname(__file__), "..", ".claude", "hooks", "dispatch.py"
            ),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        sys.stderr = old


_dispatch = _import_dispatch()
_check = _dispatch._run_advisory_check_pre_merge_comment_check
_BLOCK = _dispatch._BLOCK_SENTINEL


# ---------------------------------------------------------------------------
# T1: PR age < 600s -> BLOCK
# ---------------------------------------------------------------------------

class TestT1PRAgeGate:
    def test_young_pr_blocks(self):
        """PR created 30s ago -> block."""
        payload = _payload(42)
        responses = [
            (0, _pr_view_output(30)),      # gh pr view
            (0, _repo_view_output()),       # gh repo view
            (0, _gql_output([])),           # graphql threads
            (0, _reviews_output([])),       # reviews
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)), \
             patch("sys.stderr"):
            result = _check(payload)
        assert result == _BLOCK, f"Expected BLOCK, got {result!r}"

    def test_old_pr_does_not_age_block(self):
        """PR created 700s ago -> no age block (other checks still run)."""
        payload = _payload(42)
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output([])),           # no threads
            (0, _reviews_output([])),       # no blocking reviews
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)):
            result = _check(payload)
        assert result is None, f"Expected None (no block), got {result!r}"


# ---------------------------------------------------------------------------
# T2: Unresolved Codex P1 thread -> BLOCK
# ---------------------------------------------------------------------------

class TestT2UnresolvedCodexP1:
    def test_unresolved_p1_blocks(self):
        """Unresolved P1 thread from Codex bot -> block."""
        payload = _payload(42)
        threads = [_codex_thread(resolved=False, badge="P1")]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output(threads)),
            (0, _reviews_output([])),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)), \
             patch("sys.stderr"):
            result = _check(payload)
        assert result == _BLOCK, f"Expected BLOCK for unresolved P1, got {result!r}"

    def test_unresolved_p0_blocks(self):
        """Unresolved P0 thread from Codex bot -> block."""
        payload = _payload(42)
        threads = [_codex_thread(resolved=False, badge="P0")]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output(threads)),
            (0, _reviews_output([])),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)), \
             patch("sys.stderr"):
            result = _check(payload)
        assert result == _BLOCK, f"Expected BLOCK for unresolved P0, got {result!r}"


# ---------------------------------------------------------------------------
# T3: Resolved Codex P1 thread -> no block
# ---------------------------------------------------------------------------

class TestT3ResolvedCodexP1:
    def test_resolved_p1_does_not_block(self):
        """Resolved P1 thread -> should not block (isResolved=True)."""
        payload = _payload(42)
        threads = [_codex_thread(resolved=True, badge="P1")]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output(threads)),
            (0, _reviews_output([])),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)):
            result = _check(payload)
        assert result is None, f"Resolved P1 must not block; got {result!r}"


# ---------------------------------------------------------------------------
# T4: CHANGES_REQUESTED review -> BLOCK
# ---------------------------------------------------------------------------

class TestT4ChangesRequested:
    def test_changes_requested_blocks(self):
        """Review state CHANGES_REQUESTED -> block."""
        payload = _payload(42)
        reviews = [{"state": "CHANGES_REQUESTED", "login": "alice"}]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output([])),
            (0, _reviews_output(reviews)),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)), \
             patch("sys.stderr"):
            result = _check(payload)
        assert result == _BLOCK, f"Expected BLOCK for CHANGES_REQUESTED, got {result!r}"

    def test_commented_review_does_not_block(self):
        """Review state COMMENTED (Copilot summary) -> never blocks."""
        payload = _payload(42)
        reviews = [{"state": "COMMENTED", "login": "copilot-bot"}]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output([])),
            (0, _reviews_output(reviews)),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)):
            result = _check(payload)
        assert result is None, f"COMMENTED review must not block; got {result!r}"

    def test_approved_review_does_not_block(self):
        """Review state APPROVED -> no block."""
        payload = _payload(42)
        reviews = [{"state": "APPROVED", "login": "bob"}]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output([])),
            (0, _reviews_output(reviews)),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)):
            result = _check(payload)
        assert result is None, f"APPROVED review must not block; got {result!r}"


# ---------------------------------------------------------------------------
# T5: ZEUS_PR_MERGE_FORCE=1 bypass
# ---------------------------------------------------------------------------

class TestT5BypassEnv:
    def test_bypass_allows_young_pr(self):
        """ZEUS_PR_MERGE_FORCE=1 bypasses age block: returns advisory string, not BLOCK."""
        payload = _payload(42)
        responses = [
            (0, _pr_view_output(30)),
            (0, _repo_view_output()),
            (0, _gql_output([])),
            (0, _reviews_output([])),
        ]
        env = {**os.environ, "ZEUS_PR_MERGE_FORCE": "1"}
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)), \
             patch.dict(os.environ, {"ZEUS_PR_MERGE_FORCE": "1"}):
            result = _check(payload)
        assert result != _BLOCK, "Bypass must not return BLOCK sentinel"
        assert result is not None, "Bypass with active block reason should return advisory text"
        assert "bypass" in result.lower() or "ZEUS_PR_MERGE_FORCE" in result

    def test_bypass_allows_changes_requested(self):
        """ZEUS_PR_MERGE_FORCE=1 bypasses CHANGES_REQUESTED block."""
        payload = _payload(42)
        reviews = [{"state": "CHANGES_REQUESTED", "login": "alice"}]
        responses = [
            (0, _pr_view_output(700)),
            (0, _repo_view_output()),
            (0, _gql_output([])),
            (0, _reviews_output(reviews)),
        ]
        with patch("subprocess.run", side_effect=_make_run_side_effect(responses)), \
             patch.dict(os.environ, {"ZEUS_PR_MERGE_FORCE": "1"}):
            result = _check(payload)
        assert result != _BLOCK
        assert result is not None
