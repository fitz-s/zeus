# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect the read-only replacement forecast simple-switch bundle planner.
# Reuse: Run before changing simple-switch go-live execution steps.
# Authority basis: Operator-directed simple switch must have concrete config/schema/fact/dry-run prerequisites.
"""Replacement forecast simple-switch bundle tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_live_switch_surface import (
    CURRENT_DATA_FACT_FILE,
    CURRENT_SOURCE_FACT_FILE,
    REFIT_HANDOFF_FILE,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
)
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION
from src.data.replacement_forecast_config_switch import TARGET_SHADOW_MATERIALIZATION_CONFIG
from src.data.replacement_forecast_readiness import PRODUCT_ID, SOURCE_ID
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
)
from src.data.replacement_forecast_current_fact_patch import REQUIRED_DATA_EVIDENCE, REQUIRED_SOURCE_EVIDENCE
from src.data.replacement_forecast_simple_switch_bundle import (
    build_replacement_forecast_simple_switch_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_files(root: Path, *, current: bool = True, flags: bool = True) -> None:
    (root / "config").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    (root / "config" / "cities.json").write_text("[]", encoding="utf-8")
    (root / "config" / "source_release_calendar.yaml").write_text("{}\n", encoding="utf-8")
    feature_flags = (
        {
            SHADOW_FLAG: True,
            VETO_FLAG: True,
            TRADE_AUTHORITY_FLAG: False,
            KELLY_INCREASE_FLAG: False,
            DIRECTION_FLIP_FLAG: False,
        }
        if flags
        else {}
    )
    settings_payload: dict[str, object] = {"feature_flags": feature_flags}
    if flags:
        settings_payload["replacement_forecast_shadow"] = TARGET_SHADOW_MATERIALIZATION_CONFIG
    (root / "config" / "settings.json").write_text(json.dumps(settings_payload), encoding="utf-8")
    status = "CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE"
    for relative in (CURRENT_SOURCE_FACT_FILE, CURRENT_DATA_FACT_FILE):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Status: {status}\n", encoding="utf-8")
    _write_refit_handoff(root)


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


def _create_db(path: Path, tables: tuple[str, ...]) -> None:
    with sqlite3.connect(path) as conn:
        for table in tables:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")


def _fact_evidence() -> dict[str, object]:
    payload = {key: True for key in (*REQUIRED_SOURCE_EVIDENCE, *REQUIRED_DATA_EVIDENCE)}
    payload["generated_at"] = "2026-06-06T14:00:00Z"
    payload["evidence_refs"] = ["replacement simple-switch test evidence"]
    return payload


def _fact_evidence_report() -> dict[str, object]:
    return {
        "status": "SIMPLE_SWITCH_EVIDENCE_COMPLETE",
        "complete": True,
        "missing_source_evidence": [],
        "missing_data_evidence": [],
        "evidence": _fact_evidence(),
    }


def test_simple_switch_bundle_ready_when_config_schema_facts_and_dry_run_ready(tmp_path) -> None:
    _write_files(tmp_path)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    bundle = build_replacement_forecast_simple_switch_bundle(
        tmp_path,
        optional_dependencies=(),
        current_fact_evidence=_fact_evidence(),
    )

    assert bundle.ready is True
    assert bundle.status == "SIMPLE_SWITCH_BUNDLE_READY"
    assert bundle.config_switch.json_patch == ()
    assert bundle.missing_replacement_shadow_tables == ()
    assert bundle.dry_run.ok is True


def test_simple_switch_bundle_accepts_full_evidence_report_payload(tmp_path) -> None:
    _write_files(tmp_path)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    bundle = build_replacement_forecast_simple_switch_bundle(
        tmp_path,
        optional_dependencies=(),
        current_fact_evidence=_fact_evidence_report(),
    )

    assert bundle.current_fact_patch.ready is True
    assert "REPLACEMENT_SIMPLE_SWITCH_CURRENT_FACT_EVIDENCE_REQUIRED" not in bundle.reason_codes


def test_simple_switch_bundle_reports_config_schema_and_fact_blockers(tmp_path) -> None:
    _write_files(tmp_path, current=False, flags=False)
    _create_db(
        tmp_path / "state" / "zeus-forecasts.db",
        tuple(table for table in REQUIRED_FORECAST_TABLES if table not in {"raw_forecast_artifacts", "forecast_posteriors"}),
    )
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    bundle = build_replacement_forecast_simple_switch_bundle(tmp_path)

    assert bundle.ready is False
    assert "REPLACEMENT_SIMPLE_SWITCH_CONFIG_PATCH_REQUIRED" in bundle.reason_codes
    assert "REPLACEMENT_SIMPLE_SWITCH_SCHEMA_INIT_REQUIRED" in bundle.reason_codes
    assert "REPLACEMENT_SIMPLE_SWITCH_SOURCE_FACT_UPDATE_REQUIRED" in bundle.reason_codes
    assert "REPLACEMENT_SIMPLE_SWITCH_DATA_FACT_UPDATE_REQUIRED" in bundle.reason_codes
    assert "REPLACEMENT_SIMPLE_SWITCH_CURRENT_FACT_EVIDENCE_REQUIRED" in bundle.reason_codes
    assert set(bundle.missing_replacement_shadow_tables) == {"raw_forecast_artifacts", "forecast_posteriors"}
    assert any("--apply --stdout" in command for command in bundle.next_commands)
    assert any("--commit --stdout" in command for command in bundle.next_commands)


def test_simple_switch_bundle_reports_missing_refit_handoff_as_explicit_blocker(tmp_path) -> None:
    _write_files(tmp_path)
    (tmp_path / REFIT_HANDOFF_FILE).unlink()
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state/zeus_trades.db", REQUIRED_TRADE_TABLES)

    bundle = build_replacement_forecast_simple_switch_bundle(
        tmp_path,
        optional_dependencies=(),
        current_fact_evidence=_fact_evidence(),
        refit_handoff_json_path=tmp_path / "ready_refit_handoff.json",
    )

    assert bundle.ready is False
    assert "REPLACEMENT_SIMPLE_SWITCH_REFIT_HANDOFF_REQUIRED" in bundle.reason_codes
    assert any("plan_replacement_forecast_refit_handoff_install.py" in command for command in bundle.next_commands)
    assert any(str(tmp_path / "ready_refit_handoff.json") in command for command in bundle.next_commands)
    assert all("REFIT_HANDOFF_JSON" not in command for command in bundle.next_commands)
    assert "state/replacement_forecast_shadow/refit_handoff.json" in bundle.dry_run.live_switch_report.missing_files


def test_simple_switch_bundle_cli_is_read_only(tmp_path) -> None:
    _write_files(tmp_path, flags=False)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_simple_switch_bundle.py",
            "--root",
            str(tmp_path),
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["config_switch"]["json_patch"]
    assert json.loads((tmp_path / "config" / "settings.json").read_text(encoding="utf-8"))["feature_flags"] == {}


def test_simple_switch_bundle_cli_reads_evidence_report(tmp_path) -> None:
    _write_files(tmp_path, flags=False)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)
    evidence_path = tmp_path / "evidence.json"
    refit_handoff_path = tmp_path / "ready_refit_handoff.json"
    evidence_path.write_text(json.dumps(_fact_evidence_report()), encoding="utf-8")
    refit_handoff_path.write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_replacement_forecast_simple_switch_bundle.py",
            "--root",
            str(tmp_path),
            "--evidence-json",
            str(evidence_path),
            "--refit-handoff-json",
            str(refit_handoff_path),
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["current_fact_patch"]["status"] == "CURRENT_FACT_PATCH_READY"
    assert "REPLACEMENT_SIMPLE_SWITCH_CURRENT_FACT_EVIDENCE_REQUIRED" not in payload["reason_codes"]
    assert all("REFIT_HANDOFF_JSON" not in command for command in payload["next_commands"])


def test_simple_switch_bundle_next_commands_include_fact_write_when_patch_ready(tmp_path) -> None:
    _write_files(tmp_path, current=False)
    _create_db(tmp_path / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(tmp_path / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(tmp_path / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)

    bundle = build_replacement_forecast_simple_switch_bundle(
        tmp_path,
        optional_dependencies=(),
        current_fact_evidence=_fact_evidence_report(),
        current_fact_evidence_path=tmp_path / "evidence.json",
    )

    assert "REPLACEMENT_SIMPLE_SWITCH_CURRENT_FACT_EVIDENCE_REQUIRED" not in bundle.reason_codes
    assert any(
        "plan_replacement_forecast_current_fact_patch.py" in command
        and "--write" in command
        and str(tmp_path / "evidence.json") in command
        for command in bundle.next_commands
    )
