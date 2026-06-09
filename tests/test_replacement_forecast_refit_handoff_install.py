# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect dry-run-first installation of replacement refit handoff artifacts.
# Reuse: Run before changing simple-switch handoff install behavior.
# Authority basis: Simple switch may install a handoff artifact, but dry-run planning must not mutate live root.
"""Replacement forecast refit handoff install planner tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE
from src.data.replacement_forecast_readiness import PRODUCT_ID, SOURCE_ID
from src.data.replacement_forecast_refit_handoff_install import plan_replacement_forecast_refit_handoff_install


REPO_ROOT = Path(__file__).resolve().parents[1]


def _handoff(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "replacement_forecast_refit_handoff_v1",
                "status": "REFIT_HANDOFF_READY",
                "ready_for_product_refit": True,
                "live_promotion_allowed": False,
                "training_scope": "replacement_product_specific_only",
                "baseline_calibration_reused": False,
                "metric": "high",
                "source_family": "derived_posterior",
                "source_id": SOURCE_ID,
                "product_id": PRODUCT_ID,
                "data_version": HIGH_DATA_VERSION,
                "calibration_method": "soft_anchor_product_specific_nested_refit",
                "emos_cell_key": f"Shanghai|JJA|high|derived_posterior|{SOURCE_ID}|{PRODUCT_ID}|{HIGH_DATA_VERSION}",
                "refit_decision": {
                    "status": "PRODUCT_SPECIFIC_REFIT_READY",
                    "reason_codes": ["REPLACEMENT_REFIT_PRODUCT_SPECIFIC_EVIDENCE_READY"],
                    "data_refit_required": True,
                    "emos_replacement_ready": True,
                    "product_specific_training_allowed": True,
                    "live_promotion_allowed": False,
                    "missing_evidence": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_refit_handoff_install_plan_is_dry_run_by_default(tmp_path: Path) -> None:
    source = tmp_path / "handoff.json"
    live_root = tmp_path / "live"
    _handoff(source)

    plan = plan_replacement_forecast_refit_handoff_install(
        live_root=live_root,
        refit_handoff_json=source,
    )

    assert plan.status == "REFIT_HANDOFF_INSTALL_READY"
    assert plan.live_root_written is False
    assert plan.wrote_target is False
    assert not (live_root / REFIT_HANDOFF_FILE).exists()


def test_refit_handoff_install_write_places_validated_artifact(tmp_path: Path) -> None:
    source = tmp_path / "handoff.json"
    live_root = tmp_path / "live"
    _handoff(source)

    plan = plan_replacement_forecast_refit_handoff_install(
        live_root=live_root,
        refit_handoff_json=source,
        write=True,
    )

    assert plan.status == "REFIT_HANDOFF_INSTALLED"
    assert plan.live_root_written is True
    assert plan.wrote_target is True
    assert plan.same_content is True
    assert (live_root / REFIT_HANDOFF_FILE).exists()


def test_refit_handoff_install_blocks_invalid_source(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"status":"bad"}\n', encoding="utf-8")

    plan = plan_replacement_forecast_refit_handoff_install(
        live_root=tmp_path / "live",
        refit_handoff_json=source,
    )

    assert plan.status == "REFIT_HANDOFF_INSTALL_BLOCKED"
    assert "REPLACEMENT_REFIT_HANDOFF_INSTALL_SOURCE_INVALID" in plan.reason_codes
    assert plan.live_root_written is False


def test_refit_handoff_install_cli_stdout(tmp_path: Path) -> None:
    source = tmp_path / "handoff.json"
    live_root = tmp_path / "live"
    _handoff(source)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_refit_handoff_install.py",
            "--live-root",
            str(live_root),
            "--refit-handoff-json",
            str(source),
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "REFIT_HANDOFF_INSTALL_READY"
    assert payload["live_root_written"] is False
