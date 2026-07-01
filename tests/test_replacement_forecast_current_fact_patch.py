# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect replacement forecast current-fact patch evidence gates.
# Reuse: Run before changing simple-switch current fact refresh behavior.
# Authority basis: Current source/data fact files must not be marked current without explicit evidence.
"""Replacement forecast current-fact patch tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_current_fact_patch import (
    REQUIRED_DATA_EVIDENCE,
    REQUIRED_SOURCE_EVIDENCE,
    build_replacement_forecast_current_fact_patch_plan,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _evidence() -> dict[str, object]:
    payload = {key: True for key in (*REQUIRED_SOURCE_EVIDENCE, *REQUIRED_DATA_EVIDENCE)}
    payload["generated_at"] = "2026-06-06T14:00:00Z"
    payload["evidence_refs"] = [
        "replacement suite 228 passed",
        "live-root simple-switch bundle preview captured",
    ]
    payload["notes"] = ["audit evidence only; no trade authority"]
    return payload


def _evidence_report() -> dict[str, object]:
    return {
        "status": "SIMPLE_SWITCH_EVIDENCE_COMPLETE",
        "complete": True,
        "evidence": _evidence(),
    }


def test_current_fact_patch_blocks_without_evidence(tmp_path) -> None:
    plan = build_replacement_forecast_current_fact_patch_plan(tmp_path)

    assert plan.ready is False
    assert "REPLACEMENT_CURRENT_FACT_SOURCE_EVIDENCE_MISSING" in plan.reason_codes
    assert "REPLACEMENT_CURRENT_FACT_DATA_EVIDENCE_MISSING" in plan.reason_codes
    assert plan.source_patch is None
    assert plan.data_patch is None


def test_current_fact_patch_generates_current_fact_patch_only_with_full_evidence(tmp_path) -> None:
    plan = build_replacement_forecast_current_fact_patch_plan(tmp_path, evidence=_evidence())

    assert plan.ready is True
    assert plan.required_source_evidence == ()
    assert plan.required_data_evidence == ()
    assert plan.source_patch is not None
    assert "Status: CURRENT_FOR_LIVE" in plan.source_patch
    assert "It does not authorize live trade authority" in plan.source_patch
    assert plan.data_patch is not None
    assert "Current Data State" in plan.data_patch


def test_current_fact_patch_cli_is_read_only(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_current_fact_patch.py",
            "--root",
            str(tmp_path),
            "--evidence-json",
            str(evidence_path),
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "CURRENT_FACT_PATCH_READY"
    assert not (tmp_path / "docs" / "operations" / "current_source_validity.md").exists()


def test_current_fact_patch_cli_accepts_report_and_writes_only_when_requested(tmp_path) -> None:
    evidence_path = tmp_path / "evidence-report.json"
    evidence_path.write_text(json.dumps(_evidence_report()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_current_fact_patch.py",
            "--root",
            str(tmp_path),
            "--evidence-json",
            str(evidence_path),
            "--write",
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "CURRENT_FACT_PATCH_READY"
    assert payload["written"] is True
    assert "Status: CURRENT_FOR_LIVE" in (
        tmp_path / "docs" / "operations" / "current_source_validity.md"
    ).read_text(encoding="utf-8")
