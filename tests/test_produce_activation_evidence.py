# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/activation/UNLOCK_CRITERIA.md — TDD spec for scripts/produce_activation_evidence.py.
"""TDD spec for ``scripts.produce_activation_evidence``.

The producer is the operator-runnable evidence factory referenced by
``UNLOCK_CRITERIA.md``. Each public function:
- runs ONE flag's dry-run against an in-memory DB / tmp evidence file
- writes the resulting artifact (SQL dump / log / diff) to an output
  directory, with a deterministic filename
- returns a verdict dict that ``--all`` mode aggregates into a summary

These tests are the authoritative spec. The script implementation must
satisfy them; the CLI is a thin wrapper.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.config import EntryForecastRolloutMode, entry_forecast_config
from src.control.entry_forecast_promotion_evidence_io import (
    write_promotion_evidence,
)
from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
from src.data.live_entry_status import LiveEntryForecastStatus

scripts = pytest.importorskip("scripts.produce_activation_evidence")

UTC = timezone.utc


def _ready_status() -> LiveEntryForecastStatus:
    return LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=4,
        producer_readiness_count=4,
        producer_live_eligible_count=4,
    )


def _complete_evidence() -> EntryForecastPromotionEvidence:
    return EntryForecastPromotionEvidence(
        operator_approval_id="op-2026-05-04",
        g1_evidence_id="g1-2026-05-04",
        status_snapshot=_ready_status(),
        calibration_promotion_approved=True,
        canary_success_evidence_id="canary-2026-05-04",
    )


# -------------------------------------------------------------------- #
# C3 writer evidence
# -------------------------------------------------------------------- #


def test_produce_c3_no_evidence_lands_blocked_row_artifact(tmp_path: Path):
    """No promotion-evidence file ⇒ writer dry-run lands BLOCKED row.
    Verdict.ready_to_flip is True because BLOCKED-with-EVIDENCE-MISSING
    is the **expected** first-flip state (writer fail-closed by
    construction). Artifact contains the SQL dump of the row.
    """

    verdict = scripts.produce_c3_writer_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=tmp_path / "absent.json",
        as_of=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )

    assert verdict["flag"] == "ZEUS_ENTRY_FORECAST_READINESS_WRITER"
    assert verdict["rows_written"] == 1
    assert verdict["row_status"] == "BLOCKED"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in verdict["row_reason_codes"]
    assert verdict["ready_to_flip"] is True

    artifact = Path(verdict["artifact_path"])
    assert artifact.exists()
    assert artifact.is_relative_to(tmp_path)
    body = artifact.read_text()
    assert "BLOCKED" in body
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in body
    assert "strategy_key" in body  # SQL dump column header


def test_produce_c3_complete_evidence_lands_live_eligible_row(tmp_path: Path):
    """Complete on-disk evidence ⇒ writer lands LIVE_ELIGIBLE row.
    Verdict.ready_to_flip True; artifact dumps the row.
    """

    evidence_path = tmp_path / "evidence.json"
    write_promotion_evidence(_complete_evidence(), path=evidence_path)

    verdict = scripts.produce_c3_writer_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=evidence_path,
        as_of=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )

    assert verdict["row_status"] == "LIVE_ELIGIBLE"
    assert verdict["ready_to_flip"] is True
    assert verdict["row_reason_codes"]  # any non-empty list


# -------------------------------------------------------------------- #
# C1 rollout-gate evidence
# -------------------------------------------------------------------- #


def test_produce_c1_no_evidence_records_evidence_missing(tmp_path: Path):
    """Flag 1 dry-run with no evidence ⇒ blocker_code = EVIDENCE_MISSING;
    verdict.ready_to_flip True (the gate correctly fail-closes; that
    IS the evidence the operator needs to see).
    """

    verdict = scripts.produce_c1_rollout_gate_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=tmp_path / "absent.json",
    )

    assert verdict["flag"] == "ZEUS_ENTRY_FORECAST_ROLLOUT_GATE"
    assert verdict["blocker_code"] == "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"
    assert verdict["evidence_present"] is False
    assert verdict["ready_to_flip"] is True

    artifact = Path(verdict["artifact_path"])
    assert artifact.exists()
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in artifact.read_text()


def test_produce_c1_complete_evidence_records_no_blocker(tmp_path: Path):
    """Flag 1 with complete evidence ⇒ blocker_code is None;
    ready_to_flip True with rationale noting the gate would let live
    orders flow."""

    evidence_path = tmp_path / "evidence.json"
    write_promotion_evidence(_complete_evidence(), path=evidence_path)

    verdict = scripts.produce_c1_rollout_gate_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=evidence_path,
    )

    assert verdict["blocker_code"] is None
    assert verdict["evidence_present"] is True
    assert verdict["ready_to_flip"] is True
    artifact = Path(verdict["artifact_path"])
    assert "evidence_present=True" in artifact.read_text()


def test_produce_c1_corrupt_evidence_records_corruption_blocker(tmp_path: Path):
    """Corrupt JSON ⇒ blocker_code starts with EVIDENCE_CORRUPT prefix.
    ready_to_flip is False — operator must fix the file before flipping.
    """

    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text("not valid json {{")

    verdict = scripts.produce_c1_rollout_gate_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=evidence_path,
    )

    assert verdict["blocker_code"] is not None
    assert verdict["blocker_code"].startswith("ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:")
    assert verdict["ready_to_flip"] is False


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
    """``produce_all`` runs c1, c3, c4 in sequence and writes a
    summary markdown to ``<out_dir>/<date>_summary.md`` referencing
    each per-flag artifact. The summary has a unified verdict table.
    """

    evidence_path = tmp_path / "evidence.json"
    write_promotion_evidence(_complete_evidence(), path=evidence_path)

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
        promotion_evidence_path=evidence_path,
        check_fn=fake_check,
        as_of=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )

    assert summary["c1"]["ready_to_flip"] is True
    assert summary["c3"]["ready_to_flip"] is True
    assert summary["c4"]["ready_to_flip"] is True

    summary_path = Path(summary["summary_path"])
    assert summary_path.exists()
    body = summary_path.read_text()
    # Markdown sanity — must reference each flag by name and link the
    # underlying artifact paths so the operator can drill in.
    assert "ZEUS_ENTRY_FORECAST_READINESS_WRITER" in body
    assert "ZEUS_ENTRY_FORECAST_ROLLOUT_GATE" in body
    assert "ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS" in body
    assert "ready_to_flip" in body


def test_artifact_paths_are_dated_and_unique(tmp_path: Path):
    """Each producer call writes a file whose name encodes the flag
    and the as_of date so re-running on different days does not
    overwrite prior evidence. This is required by the unlock-criteria
    audit trail.
    """

    verdict_a = scripts.produce_c3_writer_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=tmp_path / "absent.json",
        as_of=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )
    verdict_b = scripts.produce_c3_writer_evidence(
        out_dir=tmp_path,
        promotion_evidence_path=tmp_path / "absent.json",
        as_of=datetime(2026, 5, 5, 12, tzinfo=UTC),
    )

    assert verdict_a["artifact_path"] != verdict_b["artifact_path"]
    assert "2026-05-04" in str(verdict_a["artifact_path"])
    assert "2026-05-05" in str(verdict_b["artifact_path"])
