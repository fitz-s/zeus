# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/activation/UNLOCK_CRITERIA.md — TDD spec for scripts/produce_activation_evidence.py.
"""TDD spec for ``scripts.produce_activation_evidence``.

The producer is the operator-runnable evidence factory referenced by
``UNLOCK_CRITERIA.md``. Each public function:
- runs ONE active flag's dry-run against injected or live healthcheck evidence
- writes the resulting artifact (log / diff) to an output
  directory, with a deterministic filename
- returns a verdict dict that ``--all`` mode aggregates into a summary

These tests are the authoritative spec. The script implementation must
satisfy them; the CLI is a thin wrapper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

scripts = pytest.importorskip("scripts.produce_activation_evidence")

UTC = timezone.utc


# -------------------------------------------------------------------- #
# C4 healthcheck evidence
# -------------------------------------------------------------------- #


def test_produce_c4_diff_when_blockers_present(tmp_path: Path):
    """Healthcheck dry-run diff: when blockers list is non-empty,
    flag-OFF says healthy=True, flag-ON says healthy=False. The diff
    artifact records both states.

    The producer must NOT need a real daemon — it injects a synthetic
    healthcheck result via ``check_fn`` so it is runnable on a dev
    machine without infrastructure.
    """

    def fake_check():
        # Simulate a healthcheck where everything is GREEN except
        # entry-forecast layer is BLOCKED. ``healthcheck.check()``
        # returns the predicate-evaluated dict; the producer re-runs
        # the healthy combinator with the env flag toggled.
        return {
            "daemon_alive": True,
            "status_fresh": True,
            "status_contract_valid": True,
            "riskguard_alive": True,
            "riskguard_fresh": True,
            "riskguard_contract_valid": True,
            "assumptions_valid": True,
            "cycle_failed": False,
            "infrastructure_level": "GREEN",
            "entry_forecast_blockers": ["ENTRY_FORECAST_WORLD_DB_MISSING"],
        }

    verdict = scripts.produce_c4_healthcheck_evidence(
        out_dir=tmp_path,
        check_fn=fake_check,
    )

    assert verdict["flag"] == "ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS"
    assert verdict["healthy_when_off"] is True
    assert verdict["healthy_when_on"] is False
    assert verdict["blockers_seen"] == ["ENTRY_FORECAST_WORLD_DB_MISSING"]
    # ready_to_flip semantics: we are OK to flip because the predicate
    # diff is non-trivial and matches the documented "expose blockers"
    # contract. If healthy_when_off==healthy_when_on the diff would
    # be a no-op and a flip would be pointless.
    assert verdict["ready_to_flip"] is True

    artifact = Path(verdict["artifact_path"])
    assert artifact.exists()
    text = artifact.read_text()
    assert "healthy_when_off=True" in text
    assert "healthy_when_on=False" in text


def test_produce_c4_no_diff_when_blockers_empty(tmp_path: Path):
    """No blockers ⇒ flag toggle yields no diff. ready_to_flip is False
    with rationale "no diff to surface" — operator should NOT flip
    flag 3 until at least one cycle has produced a blocker via flags
    1+2. This pins the runbook order.
    """

    def fake_check():
        return {
            "daemon_alive": True,
            "status_fresh": True,
            "status_contract_valid": True,
            "riskguard_alive": True,
            "riskguard_fresh": True,
            "riskguard_contract_valid": True,
            "assumptions_valid": True,
            "cycle_failed": False,
            "infrastructure_level": "GREEN",
            "entry_forecast_blockers": [],
        }

    verdict = scripts.produce_c4_healthcheck_evidence(
        out_dir=tmp_path,
        check_fn=fake_check,
    )

    assert verdict["healthy_when_off"] is True
    assert verdict["healthy_when_on"] is True
    assert verdict["ready_to_flip"] is False
    assert "no diff" in verdict["rationale"].lower()


# -------------------------------------------------------------------- #
# Summary aggregation (--all)
# -------------------------------------------------------------------- #


def test_produce_all_writes_summary_artifact(tmp_path: Path):
    """``produce_all`` writes a summary for active activation evidence."""

    def fake_check():
        return {
            "daemon_alive": True,
            "status_fresh": True,
            "status_contract_valid": True,
            "riskguard_alive": True,
            "riskguard_fresh": True,
            "riskguard_contract_valid": True,
            "assumptions_valid": True,
            "cycle_failed": False,
            "infrastructure_level": "GREEN",
            "entry_forecast_blockers": ["ENTRY_FORECAST_WORLD_DB_MISSING"],
        }

    summary = scripts.produce_all(
        out_dir=tmp_path,
        check_fn=fake_check,
        as_of=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )

    assert summary["c4"]["ready_to_flip"] is True

    summary_path = Path(summary["summary_path"])
    assert summary_path.exists()
    body = summary_path.read_text()
    # Markdown sanity — must reference each flag by name and link the
    # underlying artifact paths so the operator can drill in.
    assert "ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS" in body
    assert "ready_to_flip" in body


def test_artifact_paths_are_dated_and_unique(tmp_path: Path):
    """Each producer call writes a file whose name encodes the flag
    and the as_of date so re-running on different days does not
    overwrite prior evidence. This is required by the unlock-criteria
    audit trail.
    """

    def fake_check():
        return {
            "daemon_alive": True,
            "status_fresh": True,
            "status_contract_valid": True,
            "riskguard_alive": True,
            "riskguard_fresh": True,
            "riskguard_contract_valid": True,
            "assumptions_valid": True,
            "cycle_failed": False,
            "infrastructure_level": "GREEN",
            "entry_forecast_blockers": ["ENTRY_FORECAST_WORLD_DB_MISSING"],
        }

    verdict_a = scripts.produce_c4_healthcheck_evidence(
        out_dir=tmp_path,
        check_fn=fake_check,
        as_of=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )
    verdict_b = scripts.produce_c4_healthcheck_evidence(
        out_dir=tmp_path,
        check_fn=fake_check,
        as_of=datetime(2026, 5, 5, 12, tzinfo=UTC),
    )

    assert verdict_a["artifact_path"] != verdict_b["artifact_path"]
    assert "2026-05-04" in str(verdict_a["artifact_path"])
    assert "2026-05-05" in str(verdict_b["artifact_path"])
