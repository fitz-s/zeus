# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Prove replacement forecast simple-switch writes can be rehearsed without touching live root.
# Reuse: Run before changing simple-switch activation commands or live-root rehearsal behavior.
# Authority basis: Operator-directed simple switch must be safe to rehearse before live application.
"""Replacement forecast simple-switch rehearsal tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.rehearse_replacement_forecast_simple_switch import rehearse_replacement_forecast_simple_switch
from src.data.replacement_forecast_current_fact_patch import REQUIRED_DATA_EVIDENCE, REQUIRED_SOURCE_EVIDENCE
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE, REQUIRED_FORECAST_TABLES, REQUIRED_TRADE_TABLES, REQUIRED_WORLD_TABLES
from src.data.replacement_forecast_readiness import PRODUCT_ID, SOURCE_ID
from src.data.replacement_forecast_simple_switch_bundle import REPLACEMENT_SHADOW_TABLES


REPO_ROOT = Path(__file__).resolve().parents[1]


def _create_db(path: Path, tables: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        for table in tables:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")


def _write_live_root(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "docs" / "operations").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    (root / "config" / "settings.json").write_text('{"feature_flags":{}}\n', encoding="utf-8")
    (root / "config" / "cities.json").write_text("[]\n", encoding="utf-8")
    (root / "config" / "source_release_calendar.yaml").write_text("{}\n", encoding="utf-8")
    (root / "docs" / "operations" / "current_source_validity.md").write_text("Status: STALE_FOR_LIVE\n", encoding="utf-8")
    (root / "docs" / "operations" / "current_data_state.md").write_text("Status: STALE_FOR_LIVE\n", encoding="utf-8")
    _write_refit_handoff(root)
    replacement_tables = set(REPLACEMENT_SHADOW_TABLES)
    _create_db(
        root / "state" / "zeus-forecasts.db",
        tuple(table for table in REQUIRED_FORECAST_TABLES if table not in replacement_tables),
    )
    _create_db(root / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(root / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)


def _write_refit_handoff(root: Path) -> None:
    path = root / REFIT_HANDOFF_FILE
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


def _evidence(path: Path) -> None:
    evidence = {key: True for key in (*REQUIRED_SOURCE_EVIDENCE, *REQUIRED_DATA_EVIDENCE)}
    evidence["generated_at"] = "2026-06-06T16:00:00Z"
    evidence["evidence_refs"] = ["simple-switch rehearsal evidence"]
    evidence["notes"] = ["shadow/veto only"]
    path.write_text(
        json.dumps(
            {
                "status": "SIMPLE_SWITCH_EVIDENCE_COMPLETE",
                "complete": True,
                "evidence": evidence,
            }
        ),
        encoding="utf-8",
    )


def test_rehearsal_applies_switch_on_copy_without_mutating_live_root(tmp_path) -> None:
    live_root = tmp_path / "live"
    rehearsal_root = tmp_path / "rehearsal"
    evidence_path = tmp_path / "evidence.json"
    _write_live_root(live_root)
    _evidence(evidence_path)
    live_settings_before = (live_root / "config" / "settings.json").read_text(encoding="utf-8")

    report = rehearse_replacement_forecast_simple_switch(
        live_root=live_root,
        evidence_json=evidence_path,
        rehearsal_root=rehearsal_root,
        optional_dependencies=("requests",),
    )

    assert report["status"] == "REHEARSAL_READY", json.dumps(report["dry_run"], sort_keys=True)
    assert report["live_root_written"] is False
    assert report["db_copy_mode"] == "schema-stub"
    assert report["dry_run"]["status"] == "DRY_RUN_READY"
    assert report["dry_run"]["current_target_coverage_status"] == "NO_CURRENT_TARGETS"
    assert report["dry_run"]["refit_handoff_status"] == "READY"
    assert report["dry_run"]["live_switch"]["simple_switch_ready"] is True
    assert report["dry_run"]["live_switch"]["live_trade_authority"] is False
    assert report["schema_commit"]["committed"] is True
    assert (live_root / "config" / "settings.json").read_text(encoding="utf-8") == live_settings_before
    with sqlite3.connect(live_root / "state" / "zeus-forecasts.db") as conn:
        live_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(REPLACEMENT_SHADOW_TABLES).isdisjoint(live_tables)


def test_rehearsal_can_inject_refit_handoff_without_mutating_live_root(tmp_path) -> None:
    live_root = tmp_path / "live"
    rehearsal_root = tmp_path / "rehearsal"
    evidence_path = tmp_path / "evidence.json"
    refit_handoff_path = tmp_path / "refit_handoff.json"
    _write_live_root(live_root)
    (live_root / REFIT_HANDOFF_FILE).unlink()
    _write_refit_handoff(tmp_path)
    (tmp_path / REFIT_HANDOFF_FILE).replace(refit_handoff_path)
    _evidence(evidence_path)

    report = rehearse_replacement_forecast_simple_switch(
        live_root=live_root,
        evidence_json=evidence_path,
        rehearsal_root=rehearsal_root,
        optional_dependencies=("requests",),
        refit_handoff_json=refit_handoff_path,
    )

    assert report["status"] == "REHEARSAL_READY", json.dumps(report["dry_run"], sort_keys=True)
    assert REFIT_HANDOFF_FILE in report["copied_live_files"]
    assert report["dry_run"]["status"] == "DRY_RUN_READY"
    assert report["dry_run"]["current_target_coverage_status"] == "NO_CURRENT_TARGETS"
    assert report["dry_run"]["refit_handoff_status"] == "READY"
    assert not (live_root / REFIT_HANDOFF_FILE).exists()
    assert (rehearsal_root / REFIT_HANDOFF_FILE).exists()


def test_rehearsal_cli_returns_ready_json(tmp_path) -> None:
    live_root = tmp_path / "live"
    rehearsal_root = tmp_path / "rehearsal"
    evidence_path = tmp_path / "evidence.json"
    _write_live_root(live_root)
    _evidence(evidence_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rehearse_replacement_forecast_simple_switch.py",
            "--live-root",
            str(live_root),
            "--evidence-json",
            str(evidence_path),
            "--rehearsal-root",
            str(rehearsal_root),
            "--optional-dependency",
            "requests",
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "REHEARSAL_READY"
    assert payload["dry_run"]["status"] == "DRY_RUN_READY"
    assert payload["dry_run"]["current_target_coverage_status"] == "NO_CURRENT_TARGETS"
