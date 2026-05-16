# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §7 P3.3
"""
Integration test: cli_integration_shim wire-up in topology_doctor.run_navigation().

Verifies:
1. run_navigation() without --v-next-shadow returns payload without v_next_shadow key.
2. run_navigation() with v_next_shadow=True returns payload WITH v_next_shadow key.
3. The v_next_shadow envelope contains all mandatory fields.
4. The existing payload fields (ok, admission, task_blockers) are UNCHANGED.
5. maybe_shadow_compare is importable from the public __init__ API.
6. The --v-next-shadow argparse flag exists in topology_doctor_cli.py.
"""
import sys
import importlib

import pytest

from scripts.topology_v_next.cli_integration_shim import (
    maybe_shadow_compare,
    format_output,
    map_old_status_to_severity,
)
from scripts.topology_v_next.dataclasses import Severity


class TestShimWireUpIntegration:

    def test_run_navigation_without_shadow_no_v_next_key(self):
        """run_navigation() without v_next_shadow=True returns no v_next_shadow key."""
        import scripts.topology_doctor as api
        result = api.run_navigation("test navigation task", ["scripts/topology_doctor.py"])
        assert "v_next_shadow" not in result, (
            "v_next_shadow key present even with v_next_shadow=False (transparent no-op broken)"
        )

    def test_run_navigation_with_shadow_adds_v_next_key(self, monkeypatch):
        """run_navigation() with v_next_shadow=True adds v_next_shadow key."""
        # Patch log_divergence to avoid writing to disk during integration test
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        import scripts.topology_doctor as api
        result = api.run_navigation(
            "test navigation task",
            ["scripts/topology_doctor.py"],
            v_next_shadow=True,
        )
        assert "v_next_shadow" in result, (
            "v_next_shadow key missing from payload with v_next_shadow=True"
        )

    def test_v_next_shadow_envelope_has_mandatory_fields(self, monkeypatch):
        """v_next_shadow envelope has ok, decision, advisory, blockers fields."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        import scripts.topology_doctor as api
        result = api.run_navigation(
            "test task",
            ["scripts/topology_doctor.py"],
            v_next_shadow=True,
        )
        shadow = result["v_next_shadow"]

        assert shadow.get("error") is None, f"Shadow raised: {shadow.get('error')}"
        assert "ok" in shadow
        assert "decision" in shadow
        assert "advisory" in shadow
        assert "blockers" in shadow
        assert isinstance(shadow["advisory"], list)
        assert isinstance(shadow["blockers"], list)
        assert isinstance(shadow["ok"], bool)
        assert shadow["decision"] in {"ADMIT", "ADVISORY", "SOFT_BLOCK", "HARD_STOP"}

    def test_existing_payload_fields_unchanged(self, monkeypatch):
        """With v_next_shadow=True, ok/admission/task_blockers fields are not mutated."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        import scripts.topology_doctor as api
        # Get baseline without shadow
        baseline = api.run_navigation("test task", ["scripts/topology_doctor.py"])
        # Get with shadow
        with_shadow = api.run_navigation(
            "test task",
            ["scripts/topology_doctor.py"],
            v_next_shadow=True,
        )

        # These fields must be identical — shim is strictly additive
        for key in ("ok", "task_blockers", "admission", "route_card", "task"):
            assert baseline[key] == with_shadow[key], (
                f"Field {key!r} was mutated by shadow: "
                f"baseline={baseline[key]!r}, shadow={with_shadow[key]!r}"
            )

    def test_maybe_shadow_compare_importable_from_init(self):
        """maybe_shadow_compare is re-exported from scripts.topology_v_next.__init__."""
        from scripts.topology_v_next import maybe_shadow_compare as msc
        assert callable(msc)

    def test_v_next_shadow_argparse_flag_exists(self):
        """topology_doctor_cli.py has --v-next-shadow argparse flag."""
        import scripts.topology_doctor_cli as cli_module
        import argparse
        # Import api for _build_parser
        import scripts.topology_doctor as api
        parser = cli_module.build_parser(api)
        # Parse with the flag — should not raise
        args = parser.parse_args(["--navigation", "--task", "test", "--files", "src/foo.py", "--v-next-shadow"])
        assert args.v_next_shadow is True, (
            f"--v-next-shadow flag not parsed correctly: {args.v_next_shadow!r}"
        )

    def test_map_old_status_to_severity_covers_all_statuses(self):
        """map_old_status_to_severity covers all 6 current admission statuses."""
        expected_mappings = {
            "admitted": Severity.ADMIT,
            "advisory_only": Severity.ADVISORY,
            "blocked": Severity.SOFT_BLOCK,
            "scope_expansion_required": Severity.SOFT_BLOCK,
            "route_contract_conflict": Severity.SOFT_BLOCK,
            "ambiguous": Severity.SOFT_BLOCK,
        }
        for status, expected_severity in expected_mappings.items():
            result = map_old_status_to_severity(status)
            assert result == expected_severity, (
                f"map_old_status_to_severity({status!r}) returned {result!r}, "
                f"expected {expected_severity!r}"
            )

    def test_hard_stop_not_in_mapping_raises_key_error(self):
        """HARD_STOP has no old-side equivalent — KeyError is the signal."""
        with pytest.raises(KeyError):
            map_old_status_to_severity("hard_stop")

    def test_exception_in_shadow_returns_error_envelope(self, monkeypatch):
        """If v_next.admit raises, payload is returned with error envelope."""
        def _raise(*args, **kwargs):
            raise RuntimeError("simulated v_next failure")

        monkeypatch.setattr("scripts.topology_v_next.cli_integration_shim.admit", _raise)
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        payload = {
            "ok": True,
            "admission": {"status": "admitted"},
            "route_card": {},
            "task_blockers": [],
            "admission_blockers": [],
        }
        result = maybe_shadow_compare(
            {**payload},
            task="test task",
            files=["src/foo.py"],
            intent="create_new",
            v_next_shadow=True,
        )

        # Original payload fields preserved
        assert result["ok"] is True
        # Error envelope present
        shadow = result["v_next_shadow"]
        assert shadow.get("error") is not None
        assert "RuntimeError" in shadow["error"]
        assert shadow["ok"] is None
        assert shadow["advisory"] == []
        assert shadow["blockers"] == []
