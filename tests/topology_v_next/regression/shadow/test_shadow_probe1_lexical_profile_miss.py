# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe1
"""
Probe 1 — LEXICAL_PROFILE_MISS: phrase-varying calls produce same v_next profile.

Trigger: two calls with same files and different task phrase.
Kill criterion: at least one record classifies divergence detectable (v_next routing
is phrase-independent; current admission may produce different profiles per phrase).
The key assertion is that v_next produces the SAME profile across both phrase variants.
"""
import json
import tempfile
from pathlib import Path

import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit


FILES = ["scripts/topology_doctor.py"]
PAYLOAD_STUB = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


def _make_payload(ok: bool = True) -> dict:
    return {**PAYLOAD_STUB, "ok": ok}


class TestProbe1LexicalProfileMiss:

    def test_vnext_profile_is_phrase_independent(self, monkeypatch):
        """v_next routes identically for two phrase-varying task strings."""
        # Patch log_divergence to be a no-op (avoid filesystem writes)
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        payload1 = maybe_shadow_compare(
            _make_payload(),
            task="add a thing",
            files=FILES,
            intent="create_new",
            v_next_shadow=True,
        )
        payload2 = maybe_shadow_compare(
            _make_payload(),
            task="update helper logic",
            files=FILES,
            intent="create_new",
            v_next_shadow=True,
        )

        shadow1 = payload1["v_next_shadow"]
        shadow2 = payload2["v_next_shadow"]

        # v_next routing must be phrase-independent (kill criterion)
        assert shadow1["profile_matched"] == shadow2["profile_matched"], (
            f"LEXICAL_PROFILE_MISS fix broken: v_next produced different profiles "
            f"for same files+intent. got {shadow1['profile_matched']!r} vs {shadow2['profile_matched']!r}"
        )
        assert shadow1["decision"] == shadow2["decision"], (
            f"v_next severity differs across phrase variants: "
            f"{shadow1['decision']!r} vs {shadow2['decision']!r}"
        )

    def test_ok_field_always_bool(self, monkeypatch):
        """ok field in envelope is always bool, never None."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        payload = maybe_shadow_compare(
            _make_payload(),
            task="add a thing",
            files=FILES,
            intent="create_new",
            v_next_shadow=True,
        )
        shadow = payload["v_next_shadow"]
        assert isinstance(shadow["ok"], bool)

    def test_noop_when_flag_false(self):
        """When v_next_shadow=False, payload is returned UNCHANGED."""
        original = _make_payload()
        result = maybe_shadow_compare(
            original,
            task="add a thing",
            files=FILES,
            intent="create_new",
            v_next_shadow=False,
        )
        assert result is original
        assert "v_next_shadow" not in result
