# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: boot crash-loop incidents 2026-06-12 (3x same day — each
#   needed a manual operator run of the absence resolver before the daemon
#   could boot; launchd respawned a failing boot in a loop meanwhile).
"""ANTIBODY: boot auto-resolution fires ONLY for the stuck-unknown class and
fail-closes on everything else (refusal, mixed reasons, venue failure)."""
from __future__ import annotations

import pytest

import src.execution.edli_absence_resolver as resolver_mod
from src.execution.edli_absence_resolver import boot_auto_resolve_stuck_unknowns


def test_fires_and_succeeds_for_pure_stuck_unknown_reasons(monkeypatch):
    calls = {}

    def fake_resolve(*, aggregate_id, apply, log):
        calls["apply"] = apply
        return 0

    monkeypatch.setattr(resolver_mod, "resolve", fake_resolve)
    ok = boot_auto_resolve_stuck_unknowns(
        ["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:4", "EDLI_STAGE_LIVE_CAP_RESERVED:4"]
    )
    assert ok is True
    assert calls["apply"] is True


def test_never_fires_for_mixed_reasons(monkeypatch):
    monkeypatch.setattr(
        resolver_mod, "resolve",
        lambda **kw: pytest.fail("must not attempt resolution with out-of-class blockers"),
    )
    ok = boot_auto_resolve_stuck_unknowns(
        ["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1", "EDLI_STAGE_LOADED_SHA_MISMATCH:x"]
    )
    assert ok is False


def test_never_fires_for_empty_reasons(monkeypatch):
    monkeypatch.setattr(
        resolver_mod, "resolve",
        lambda **kw: pytest.fail("must not attempt resolution with no blockers"),
    )
    assert boot_auto_resolve_stuck_unknowns([]) is False


def test_refusal_or_venue_failure_fails_closed(monkeypatch):
    def raising_resolve(**kw):
        raise RuntimeError("authenticated venue read found matching exposure; do not release cap")

    monkeypatch.setattr(resolver_mod, "resolve", raising_resolve)
    assert boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1"]) is False


def test_incomplete_resolution_fails_closed(monkeypatch):
    monkeypatch.setattr(resolver_mod, "resolve", lambda **kw: 1)
    assert boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_LIVE_CAP_RESERVED:2"]) is False
