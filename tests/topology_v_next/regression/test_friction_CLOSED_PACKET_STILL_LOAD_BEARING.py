# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.2
"""
Friction regression: CLOSED_PACKET_STILL_LOAD_BEARING (P1.2 variant).

SCAFFOLD §5.2: "Partial (P1 single-call helper only) — _check_authority_status
helper inside admission_engine checks artifact_authority_status per call and
emits IssueRecord. Helper is unit-testable. No production caller until P2."

Test: touching a file whose binding artifact_authority_status row is
'CURRENT_HISTORICAL' raises ADVISORY closed_packet_authority and/or
authority_status_stale via _check_authority_status.
"""
from __future__ import annotations

import datetime

import pytest

from scripts.topology_v_next.admission_engine import admit, _check_authority_status
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


def _stale_date(days_ago: int) -> str:
    d = datetime.date.today() - datetime.timedelta(days=days_ago)
    return d.strftime("%Y-%m-%d")


def _fresh_date(days_ahead: int = 1) -> str:
    d = datetime.date.today() - datetime.timedelta(days=days_ahead)
    return d.strftime("%Y-%m-%d")


def _make_binding(artifact_authority_status: dict) -> BindingLayer:
    cm = CoverageMap(
        profiles={
            "agent_runtime": (
                "scripts/topology_doctor.py",
                "architecture/old_spec.yaml",
            ),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(Intent.zeus_topology_tooling,),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status=artifact_authority_status,
    )


HISTORICAL_ARTIFACT = "architecture/old_spec.yaml"
HISTORICAL_ROW = {
    HISTORICAL_ARTIFACT: {
        "status": "CURRENT_HISTORICAL",
        "last_confirmed": "2026-01-01",
        "confirmation_ttl_days": 365,
    }
}

STALE_ARTIFACT = "architecture/old_spec.yaml"
STALE_ROW = {
    STALE_ARTIFACT: {
        "status": "CURRENT",
        "last_confirmed": _stale_date(60),
        "confirmation_ttl_days": 30,
    }
}


class TestClosedPacketHelperDirect:
    """Direct unit tests for _check_authority_status helper."""

    def test_current_historical_emits_closed_packet_advisory(self):
        issues = _check_authority_status([HISTORICAL_ARTIFACT], HISTORICAL_ROW)
        codes = {i.code for i in issues}
        assert "closed_packet_authority" in codes

    def test_closed_packet_advisory_is_advisory_severity(self):
        issues = _check_authority_status([HISTORICAL_ARTIFACT], HISTORICAL_ROW)
        advisory_issues = [i for i in issues if i.code == "closed_packet_authority"]
        assert len(advisory_issues) >= 1
        assert all(i.severity == Severity.ADVISORY for i in advisory_issues)

    def test_stale_ttl_emits_authority_status_stale(self):
        issues = _check_authority_status([STALE_ARTIFACT], STALE_ROW)
        codes = {i.code for i in issues}
        assert "authority_status_stale" in codes

    def test_stale_advisory_is_advisory_severity(self):
        issues = _check_authority_status([STALE_ARTIFACT], STALE_ROW)
        stale_issues = [i for i in issues if i.code == "authority_status_stale"]
        assert all(i.severity == Severity.ADVISORY for i in stale_issues)

    def test_fresh_artifact_no_stale_issue(self):
        row = {
            "architecture/fresh.yaml": {
                "status": "CURRENT",
                "last_confirmed": _fresh_date(1),
                "confirmation_ttl_days": 30,
            }
        }
        issues = _check_authority_status(["architecture/fresh.yaml"], row)
        codes = {i.code for i in issues}
        assert "authority_status_stale" not in codes

    def test_both_codes_emitted_when_historical_and_stale(self):
        """CURRENT_HISTORICAL + stale TTL → both issue codes."""
        row = {
            HISTORICAL_ARTIFACT: {
                "status": "CURRENT_HISTORICAL",
                "last_confirmed": _stale_date(90),
                "confirmation_ttl_days": 30,
            }
        }
        issues = _check_authority_status([HISTORICAL_ARTIFACT], row)
        codes = {i.code for i in issues}
        assert "closed_packet_authority" in codes
        assert "authority_status_stale" in codes

    def test_file_not_in_status_returns_no_issues(self):
        issues = _check_authority_status(["scripts/untracked.py"], HISTORICAL_ROW)
        assert issues == []

    def test_multiple_files_independent_checks(self):
        row = {
            "arch/a.yaml": {"status": "CURRENT_HISTORICAL", "last_confirmed": "2026-01-01", "confirmation_ttl_days": 365},
            "arch/b.yaml": {"status": "CURRENT", "last_confirmed": _stale_date(60), "confirmation_ttl_days": 30},
        }
        issues = _check_authority_status(["arch/a.yaml", "arch/b.yaml"], row)
        codes = {i.code for i in issues}
        assert "closed_packet_authority" in codes
        assert "authority_status_stale" in codes


class TestClosedPacketViaAdmit:
    """End-to-end: admit() surfaces _check_authority_status results."""

    def test_admit_surfaces_closed_packet_advisory(self):
        binding = _make_binding(HISTORICAL_ROW)
        result = admit(
            intent=Intent.modify_existing,
            files=[HISTORICAL_ARTIFACT],
            binding=binding,
        )
        codes = {i.code for i in result.issues}
        assert "closed_packet_authority" in codes

    def test_closed_packet_advisory_does_not_hard_block(self):
        """ADVISORY does not set ok=False (unless overridden in binding)."""
        binding = _make_binding(HISTORICAL_ROW)
        result = admit(
            intent=Intent.modify_existing,
            files=[HISTORICAL_ARTIFACT],
            binding=binding,
        )
        assert result.severity != Severity.HARD_STOP

    def test_admit_surfaces_stale_authority(self):
        binding = _make_binding(STALE_ROW)
        result = admit(
            intent=Intent.modify_existing,
            files=[STALE_ARTIFACT],
            binding=binding,
        )
        codes = {i.code for i in result.issues}
        assert "authority_status_stale" in codes

    def test_clean_file_no_authority_issues(self):
        binding = _make_binding({})
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=binding,
        )
        authority_codes = {
            i.code for i in result.issues
            if i.code in ("closed_packet_authority", "authority_status_stale")
        }
        assert len(authority_codes) == 0

    def test_issues_in_to_dict_output(self):
        """
        ADVISORY_OUTPUT_INVISIBILITY secondary check: authority issues appear
        in to_dict() at top level, not buried.
        """
        binding = _make_binding(HISTORICAL_ROW)
        result = admit(
            intent=Intent.modify_existing,
            files=[HISTORICAL_ARTIFACT],
            binding=binding,
        )
        d = result.to_dict()
        issue_codes = {i["code"] for i in d["issues"]}
        assert "closed_packet_authority" in issue_codes
