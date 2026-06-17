# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect replacement forecast shadow/veto switch application receipts.
# Reuse: Run before applying or changing the replacement forecast live-root switch path.
# Authority basis: The selected Open-Meteo ECMWF IFS 9km plus AIFS sampled-2t strategy may only enter shadow/veto wiring here.
"""Replacement forecast shadow/veto switch apply tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.apply_replacement_forecast_shadow_veto_switch import apply_replacement_forecast_shadow_veto_switch
from src.data.replacement_forecast_config_switch import TARGET_SHADOW_MATERIALIZATION_CONFIG
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION
from src.data.replacement_forecast_current_fact_patch import REQUIRED_DATA_EVIDENCE, REQUIRED_SOURCE_EVIDENCE
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE, REQUIRED_FORECAST_TABLES, REQUIRED_TRADE_TABLES, REQUIRED_WORLD_TABLES
from src.data.replacement_forecast_readiness import PRODUCT_ID, SOURCE_ID
from src.data.replacement_forecast_runtime_policy import DIRECTION_FLIP_FLAG, KELLY_INCREASE_FLAG, SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG
# DEAD-PROMOTION-APPARATUS REMOVAL (2026-06-16): re-pointed from the deleted
# replacement_forecast_simple_switch_bundle to the canonical LIVE schema-init source
# (identical 4-tuple; init_replacement_forecast_shadow_schema is the schema authority
# used by the operator apply tool). Keeps this LIVE apply-path test intact.
from scripts.init_replacement_forecast_shadow_schema import REPLACEMENT_SHADOW_TABLES


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
    replacement_tables = set(REPLACEMENT_SHADOW_TABLES)
    _create_db(
        root / "state" / "zeus-forecasts.db",
        tuple(table for table in REQUIRED_FORECAST_TABLES if table not in replacement_tables),
    )
    _create_db(root / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(root / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)


def _write_refit_handoff(path: Path) -> None:
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


def _write_evidence(path: Path) -> None:
    evidence = {key: True for key in (*REQUIRED_SOURCE_EVIDENCE, *REQUIRED_DATA_EVIDENCE)}
    evidence["generated_at"] = "2026-06-06T16:00:00Z"
    evidence["evidence_refs"] = ["shadow/veto apply evidence"]
    evidence["notes"] = ["openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00"]
    path.write_text(
        json.dumps({"status": "SIMPLE_SWITCH_EVIDENCE_COMPLETE", "complete": True, "evidence": evidence}),
        encoding="utf-8",
    )


def test_shadow_veto_switch_default_is_read_only(tmp_path) -> None:
    live_root = tmp_path / "live"
    evidence_path = tmp_path / "evidence.json"
    refit_path = tmp_path / "refit_handoff.json"
    _write_live_root(live_root)
    _write_evidence(evidence_path)
    _write_refit_handoff(refit_path)
    settings_before = (live_root / "config" / "settings.json").read_text(encoding="utf-8")

    receipt = apply_replacement_forecast_shadow_veto_switch(
        live_root=live_root,
        evidence_json=evidence_path,
        refit_handoff_json=refit_path,
        apply=False,
        optional_dependencies=("requests",),
    )

    assert receipt["status"] == "SHADOW_VETO_SWITCH_READY"
    assert receipt["live_root_written"] is False
    assert receipt["dry_run"]["status"] == "DRY_RUN_READY"
    assert receipt["runtime_policy_status"] == "SHADOW_VETO_ONLY"
    assert receipt["live_trade_authority"] is False
    assert receipt["rollback_commands"] == []
    assert (live_root / "config" / "settings.json").read_text(encoding="utf-8") == settings_before
    assert not (live_root / REFIT_HANDOFF_FILE).exists()


def test_shadow_veto_switch_apply_writes_temp_root_and_receipt(tmp_path) -> None:
    live_root = tmp_path / "live"
    evidence_path = tmp_path / "evidence.json"
    refit_path = tmp_path / "refit_handoff.json"
    backup_dir = tmp_path / "backup"
    _write_live_root(live_root)
    _write_evidence(evidence_path)
    _write_refit_handoff(refit_path)

    receipt = apply_replacement_forecast_shadow_veto_switch(
        live_root=live_root,
        evidence_json=evidence_path,
        refit_handoff_json=refit_path,
        backup_dir=backup_dir,
        apply=True,
        optional_dependencies=("requests",),
    )

    assert receipt["status"] == "SHADOW_VETO_SWITCH_APPLIED", json.dumps(receipt["dry_run"], sort_keys=True)
    assert receipt["live_root_written"] is True
    assert receipt["dry_run"]["status"] == "DRY_RUN_READY"
    assert receipt["dry_run"]["live_switch"]["simple_switch_ready"] is True
    assert receipt["runtime_policy_status"] == "SHADOW_VETO_ONLY"
    assert receipt["live_trade_authority"] is False
    assert set(receipt["applied_steps"]) == {
        "config_shadow_veto_flags",
        "shadow_materialization_dirs",
        "replacement_shadow_schema",
        "refit_handoff",
        "current_fact_patch",
    }
    payload = json.loads((live_root / "config" / "settings.json").read_text(encoding="utf-8"))
    assert payload["feature_flags"][SHADOW_FLAG] is True
    assert payload["feature_flags"][VETO_FLAG] is True
    assert payload["feature_flags"][TRADE_AUTHORITY_FLAG] is False
    assert payload["feature_flags"][KELLY_INCREASE_FLAG] is False
    assert payload["feature_flags"][DIRECTION_FLIP_FLAG] is False
    assert payload["replacement_forecast_shadow"] == TARGET_SHADOW_MATERIALIZATION_CONFIG
    for key, relative in TARGET_SHADOW_MATERIALIZATION_CONFIG.items():
        if key.endswith("_dir"):
            assert (live_root / str(relative)).is_dir()
            assert receipt["shadow_materialization_dirs"][key] == str(live_root / str(relative))
    assert "Status: CURRENT_FOR_LIVE" in (live_root / "docs" / "operations" / "current_source_validity.md").read_text(encoding="utf-8")
    assert (live_root / REFIT_HANDOFF_FILE).exists()
    assert (backup_dir / "config" / "settings.json").exists()
    assert (backup_dir / "state" / "zeus-forecasts.db").exists()
    assert receipt["rollback_commands"]
    with sqlite3.connect(live_root / "state" / "zeus-forecasts.db") as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(REPLACEMENT_SHADOW_TABLES).issubset(tables)


def test_shadow_veto_switch_cli_writes_receipt_json(tmp_path) -> None:
    live_root = tmp_path / "live"
    evidence_path = tmp_path / "evidence.json"
    refit_path = tmp_path / "refit_handoff.json"
    receipt_path = tmp_path / "receipt.json"
    _write_live_root(live_root)
    _write_evidence(evidence_path)
    _write_refit_handoff(refit_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/apply_replacement_forecast_shadow_veto_switch.py",
            "--live-root",
            str(live_root),
            "--evidence-json",
            str(evidence_path),
            "--refit-handoff-json",
            str(refit_path),
            "--receipt-json",
            str(receipt_path),
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
    assert payload["status"] == "SHADOW_VETO_SWITCH_READY"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] == "SHADOW_VETO_SWITCH_READY"
