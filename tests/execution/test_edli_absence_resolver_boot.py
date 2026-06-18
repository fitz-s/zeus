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


def test_absence_refusal_can_fall_through_to_later_resolver(monkeypatch):
    calls = []

    def raising_resolve(**kw):
        calls.append("absence")
        raise RuntimeError("authenticated venue read found matching exposure; do not release cap")

    monkeypatch.setattr(resolver_mod, "resolve", raising_resolve)
    import src.execution.edli_presence_resolver as presence_mod
    import src.execution.edli_resting_absorbed_resolver as resting_mod

    monkeypatch.setattr(
        presence_mod,
        "resolve_presence",
        lambda **kw: calls.append("presence") or 1,
    )
    monkeypatch.setattr(
        resting_mod,
        "resolve_resting_or_absorbed",
        lambda **kw: calls.append("resting") or 0,
    )

    assert boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1"]) is True
    assert calls == ["absence", "presence", "resting"]


def test_incomplete_resolution_fails_closed(monkeypatch):
    monkeypatch.setattr(resolver_mod, "resolve", lambda **kw: 1)
    assert boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_LIVE_CAP_RESERVED:2"]) is False
