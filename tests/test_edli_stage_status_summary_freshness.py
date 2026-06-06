# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: fix/edli-stage-readiness-2026-05-31 — EDLI-mode release-gate
#   status_summary freshness surface. ROOT: EDLI event-driven modes never call
#   run_cycle(), so write_cycle_pulse (the status_summary writer) was silent and
#   the gate's _fresh() found no recognized top-level timestamp key.
#
# Lifecycle: created=2026-05-31; last_reviewed=2026-05-31; last_reused=never
# Purpose: Prove write_cycle_pulse emits a gate-canonical freshness key so the
#   EDLI-mode live-release gate reads a fresh pulse as fresh (cross-module relationship test).
# Reuse: Verify status_summary.STATUS_PATH still points at a writable path and
#   check_live_release_gate._fresh signature unchanged before reusing.
#
# Relationship invariant (writer -> release gate, cross-module boundary):
#   When write_cycle_pulse() writes status_summary.json on a fresh pulse, the
#   payload MUST carry a top-level freshness key from the gate's canonical set
#   (generated_at|updated_at|observed_at|captured_at) — NOT only "timestamp" /
#   nested truth.generated_at — so the live-release gate reads a genuinely-fresh
#   pulse as fresh. The age math is the gate's; this proves only that the writer
#   emits a key the gate can read.

import json
from datetime import datetime, timezone

import pytest

# Gate-side freshness reader (the consumer half of the relationship).
from scripts.check_live_release_gate import _fresh  # type: ignore
from src.observability import status_summary
from src.observability.status_summary import write_cycle_pulse


_GATE_FRESHNESS_KEYS = ("generated_at", "updated_at", "observed_at", "captured_at")


def _redirect_status_path(tmp_path, monkeypatch):
    """Point the writer's STATUS_PATH at a temp file so we never touch live state."""
    target = tmp_path / "status_summary.json"
    monkeypatch.setattr(status_summary, "STATUS_PATH", target, raising=True)
    return target


def test_write_cycle_pulse_emits_gate_recognized_freshness_key(tmp_path, monkeypatch):
    """Fresh pulse -> status_summary.json carries a gate-canonical freshness key."""
    target = _redirect_status_path(tmp_path, monkeypatch)

    write_cycle_pulse({"monitors": 0, "exits": 0, "chain_sync": {}})

    assert target.exists(), "write_cycle_pulse must write the status_summary file"
    payload = json.loads(target.read_text())

    present = [k for k in _GATE_FRESHNESS_KEYS if payload.get(k)]
    assert present, (
        "status_summary payload exposes no gate-canonical freshness key "
        f"(any of {_GATE_FRESHNESS_KEYS}); gate _fresh() would return "
        f"missing_timestamp. Top-level keys present: {sorted(payload.keys())}"
    )


def test_fresh_pulse_is_read_as_fresh_by_release_gate(tmp_path, monkeypatch):
    """Relationship: a fresh write_cycle_pulse passes the gate's _fresh() check."""
    target = _redirect_status_path(tmp_path, monkeypatch)

    write_cycle_pulse({"monitors": 0, "exits": 0})

    payload = json.loads(target.read_text())
    now = datetime.now(timezone.utc)
    ok, detail = _fresh(payload, max_age_seconds=15 * 60, now=now)
    assert ok, f"freshly-pulsed status_summary read as stale by gate: {detail}"


def test_generated_at_tracks_timestamp_in_lockstep(tmp_path, monkeypatch):
    """generated_at (gate key) must equal timestamp (legacy key) on a fresh pulse,
    so the two cannot diverge and present a different freshness instant."""
    target = _redirect_status_path(tmp_path, monkeypatch)

    write_cycle_pulse({"monitors": 0, "exits": 0})

    payload = json.loads(target.read_text())
    # On a successful pulse both keys exist and are identical.
    if payload.get("timestamp") is not None:
        assert payload.get("generated_at") == payload.get("timestamp"), (
            "generated_at must equal timestamp on a fresh pulse; divergence would "
            "let the gate read a different freshness instant than legacy consumers"
        )


def test_cycle_pulse_preserves_edli_business_candidate_counters(tmp_path, monkeypatch):
    """Chain-sync pulse must not erase the EDLI business-plane candidate proof."""
    target = _redirect_status_path(tmp_path, monkeypatch)

    write_cycle_pulse(
        {
            "mode": "edli_event_reactor",
            "candidates": 7,
            "final_intents_built": 1,
            "submit_attempts": 1,
            "venue_acks": 0,
            "deterministic_rejections": {"real_order_submit_disabled": 1},
        }
    )
    write_cycle_pulse({"monitors": 1, "exits": 0, "chain_sync": {"synced": 1}})

    payload = json.loads(target.read_text())
    cycle = payload["cycle"]
    assert cycle["candidates"] == 7
    assert cycle["final_intents_built"] == 1
    assert cycle["deterministic_rejections"]["real_order_submit_disabled"] == 1
    assert cycle["chain_sync"]["synced"] == 1
