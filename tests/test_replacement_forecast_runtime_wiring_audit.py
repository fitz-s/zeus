# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Prove replacement forecast switch settings reach the runtime reactor hook path.
# Reuse: Run before enabling the Open-Meteo ECMWF IFS 9km plus AIFS sampled-2t shadow/veto switch.
# Authority basis: Operator requires end-to-end switch readiness from data download to pre-order/live path.
"""Replacement forecast runtime wiring audit tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_live_switch_surface import REQUIRED_FORECAST_TABLES, REQUIRED_TRADE_TABLES, REQUIRED_WORLD_TABLES
from src.data.replacement_forecast_runtime_wiring_audit import build_replacement_forecast_runtime_wiring_audit
from src.data.replacement_forecast_runtime_policy import DIRECTION_FLIP_FLAG, KELLY_INCREASE_FLAG, SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG


REPO_ROOT = Path(__file__).resolve().parents[1]


def _create_db(path: Path, tables: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        for table in tables:
            if table == "raw_forecast_artifacts":
                conn.execute(
                    """
                    CREATE TABLE raw_forecast_artifacts (
                        artifact_id INTEGER PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        product_id TEXT NOT NULL,
                        data_version TEXT NOT NULL,
                        source_cycle_time TEXT NOT NULL,
                        source_available_at TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        artifact_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        byte_size INTEGER NOT NULL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO raw_forecast_artifacts (
                        source_id, product_id, data_version, source_cycle_time,
                        source_available_at, captured_at, artifact_path, sha256, byte_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            "openmeteo_ecmwf_ifs_9km",
                            "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                            "openmeteo_ecmwf_ifs9_anchor_localday_high",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/openmeteo.json",
                            "a" * 64,
                            1,
                        ),
                        (
                            "ecmwf_aifs_ens",
                            "ecmwf_aifs_ens_sampled_2t_6h_v1",
                            "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
                            "2026-06-06T00:00:00+00:00",
                            "2026-06-06T07:00:00+00:00",
                            "2026-06-06T07:01:00+00:00",
                            "/tmp/aifs.grib",
                            "b" * 64,
                            1,
                        ),
                    ),
                )
            else:
                conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")


def _write_live_root(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "docs" / "operations").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    (root / "config" / "settings.json").write_text(
        json.dumps(
            {
                "feature_flags": {},
                "replacement_forecast_shadow": {
                    "refit_handoff_path": "state/replacement_forecast_shadow/refit_handoff.json"
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "config" / "cities.json").write_text("[]\n", encoding="utf-8")
    (root / "config" / "source_release_calendar.yaml").write_text("{}\n", encoding="utf-8")
    (root / "docs" / "operations" / "current_source_validity.md").write_text("Status: STALE_FOR_LIVE\n", encoding="utf-8")
    (root / "docs" / "operations" / "current_data_state.md").write_text("Status: STALE_FOR_LIVE\n", encoding="utf-8")
    _create_db(root / "state" / "zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    _create_db(root / "state" / "zeus-world.db", REQUIRED_WORLD_TABLES)
    _create_db(root / "state" / "zeus_trades.db", REQUIRED_TRADE_TABLES)


def _write_promotion_evidence(root: Path) -> None:
    path = root / "state" / "replacement_forecast_shadow" / "promotion_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                # FIX-1 AND (ITEM B): LIVE_AUTHORITY now requires BOTH a passing
                # promotion evidence AND a passing capital-objective evidence. This
                # promotion block is PASSING (official_days>=5, official_rows>=250,
                # after_cost_pnl>0, scored_rows>=official_rows) so the wiring audit can
                # reach LIVE_AUTHORITY under the conjunction law; the single-proof
                # (capital-only) path no longer reaches LIVE_AUTHORITY.
                "promotion_evidence": {
                    "official_days": 5,
                    "official_rows": 250,
                    "after_cost_pnl": 1.0,
                    "q_lcb_coverage": 0.95,
                    "anti_lookahead_violations": 0,
                    "source_availability_violations": 0,
                    "unresolved_regression_clusters": 0,
                    "same_clob_replay_passed": True,
                    "nested_walk_forward_passed": True,
                    "same_clob_replay_scored_rows": 250,
                    "same_clob_replay_blocked_rows": 0,
                    "fee_depth_fill_evidence_passed": True,
                    "unit_pnl_only": False,
                    "nested_holdout_brier": 0.2,
                    "nested_holdout_log_loss": 0.5,
                    "nested_selected_anchor_weight": 0.8,
                    "nested_selected_anchor_sigma_c": 3.0,
                    "nested_guardrail_bucket_count": 1,
                    "nested_guardrail_bucket_min_rows": 20,
                    "product_specific_refit_passed": True,
                },
                "capital_objective_evidence": {
                    "selected_label": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
                    "replay_status": "EMPIRICAL_WINNER",
                    "after_cost_pnl": 97.65,
                    "source_availability_observed": True,
                    "source_availability_violations": 0,
                    "anti_lookahead_violations": 0,
                    "same_clob_replay_passed": True,
                    "fee_depth_fill_evidence_passed": True,
                    "unit_pnl_only": False,
                    "product_specific_refit_passed": True,
                },
            }
        ),
        encoding="utf-8",
    )


def test_runtime_wiring_audit_ready_with_apply_receipt_assumptions(tmp_path) -> None:
    live_root = tmp_path / "live"
    receipt_path = tmp_path / "receipt.json"
    _write_live_root(live_root)
    receipt_path.write_text(
        json.dumps({"status": "SHADOW_VETO_SWITCH_READY", "live_root_written": False}),
        encoding="utf-8",
    )

    report = build_replacement_forecast_runtime_wiring_audit(
        live_root=live_root,
        repo_root=REPO_ROOT,
        apply_receipt_json=receipt_path,
        assume_shadow_veto=True,
        assume_current_facts=True,
        assume_shadow_schema=True,
        assume_refit_handoff=True,
        optional_dependencies=("requests",),
    )

    assert report.status == "RUNTIME_WIRING_READY", report.as_dict()
    assert report.runtime_policy_status == "SHADOW_VETO_ONLY"
    assert report.dry_run_status == "DRY_RUN_READY"
    assert report.refit_handoff_status == "ASSUMED_READY"
    assert report.raw_artifact_lineage_status == "READY"
    assert report.raw_artifact_lineage_counts["openmeteo_ecmwf_ifs_9km"] == 1
    assert report.raw_artifact_lineage_counts["ecmwf_aifs_ens"] == 1
    assert report.shadow_materialization_config_status == "READY"
    assert report.missing_shadow_materialization_config == ()
    assert report.shadow_materialization_paths_status == "READY"
    assert report.shadow_materialization_paths["forecast_db"] == str(live_root / "state/zeus-forecasts.db")
    assert report.shadow_materialization_paths["request_dir"] == str(live_root / "state/replacement_forecast_shadow/requests")
    assert report.live_authority_flags_false is True
    assert report.receipt_status == "SHADOW_VETO_SWITCH_READY"
    assert report.receipt_live_root_written is False
    assert "edli_no_submit_receipts.replacement_forecast" in report.write_surfaces
    assert all(report.main_anchor_status.values())
    assert all(report.adapter_anchor_status.values())
    assert all(report.hook_factory_anchor_status.values())


def test_runtime_wiring_audit_ready_for_live_authority_with_capital_evidence(tmp_path) -> None:
    live_root = tmp_path / "live"
    receipt_path = tmp_path / "receipt.json"
    _write_live_root(live_root)
    settings_path = live_root / "config" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    payload["feature_flags"] = {
        SHADOW_FLAG: True,
        VETO_FLAG: True,
        TRADE_AUTHORITY_FLAG: True,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }
    payload["replacement_forecast_shadow"].update(
        {
            "raw_manifest_dir": "state/replacement_forecast_shadow/raw_manifests",
            "request_dir": "state/replacement_forecast_shadow/requests",
            "processed_dir": "state/replacement_forecast_shadow/processed",
            "failed_dir": "state/replacement_forecast_shadow/failed",
            "seed_dir": "state/replacement_forecast_shadow/seeds",
            "seed_processed_dir": "state/replacement_forecast_shadow/seeds_processed",
            "seed_failed_dir": "state/replacement_forecast_shadow/seeds_failed",
            "forecast_db": "state/zeus-forecasts.db",
            "promotion_evidence_path": "state/replacement_forecast_shadow/promotion_evidence.json",
            "materialization_interval_min": 5,
            "seed_discovery_limit_per_cycle": 10,
            "seed_limit_per_cycle": 10,
            "materialization_limit_per_cycle": 10,
        }
    )
    settings_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_promotion_evidence(live_root)
    receipt_path.write_text(
        json.dumps({"status": "LIVE_AUTHORITY_SWITCH_APPLIED", "live_root_written": True}),
        encoding="utf-8",
    )

    report = build_replacement_forecast_runtime_wiring_audit(
        live_root=live_root,
        repo_root=REPO_ROOT,
        apply_receipt_json=receipt_path,
        assume_current_facts=True,
        assume_shadow_schema=True,
        assume_refit_handoff=True,
        optional_dependencies=("requests",),
    )

    assert report.status == "RUNTIME_WIRING_READY", report.as_dict()
    assert report.runtime_policy_status == "LIVE_AUTHORITY"
    assert report.live_authority_flags_false is False
    assert report.receipt_status == "LIVE_AUTHORITY_SWITCH_APPLIED"


def test_runtime_wiring_audit_blocks_when_runtime_anchor_missing(tmp_path) -> None:
    live_root = tmp_path / "live"
    repo_root = tmp_path / "repo"
    (repo_root / "src" / "engine").mkdir(parents=True)
    (repo_root / "src").mkdir(exist_ok=True)
    (repo_root / "src" / "main.py").write_text("", encoding="utf-8")
    (repo_root / "src" / "engine" / "event_reactor_adapter.py").write_text("", encoding="utf-8")
    (repo_root / "src" / "engine" / "replacement_forecast_hook_factory.py").write_text("", encoding="utf-8")
    _write_live_root(live_root)

    report = build_replacement_forecast_runtime_wiring_audit(
        live_root=live_root,
        repo_root=repo_root,
        assume_shadow_veto=True,
        assume_current_facts=True,
        assume_shadow_schema=True,
        assume_refit_handoff=True,
        optional_dependencies=("requests",),
    )

    assert report.status == "RUNTIME_WIRING_BLOCKED"
    assert "REPLACEMENT_RUNTIME_WIRING_MAIN_ANCHOR_MISSING" in report.reason_codes
    assert "REPLACEMENT_RUNTIME_WIRING_ADAPTER_ANCHOR_MISSING" in report.reason_codes
    assert "REPLACEMENT_RUNTIME_WIRING_HOOK_FACTORY_ANCHOR_MISSING" in report.reason_codes


def test_runtime_wiring_audit_blocks_without_shadow_materialization_config(tmp_path) -> None:
    live_root = tmp_path / "live"
    _write_live_root(live_root)

    report = build_replacement_forecast_runtime_wiring_audit(
        live_root=live_root,
        repo_root=REPO_ROOT,
        assume_shadow_veto=False,
        assume_current_facts=True,
        assume_shadow_schema=True,
        assume_refit_handoff=True,
        optional_dependencies=("requests",),
    )

    assert report.status == "RUNTIME_WIRING_BLOCKED"
    assert "REPLACEMENT_RUNTIME_WIRING_SHADOW_MATERIALIZATION_CONFIG_MISSING" in report.reason_codes
    assert "request_dir" in report.missing_shadow_materialization_config


def test_runtime_wiring_audit_blocks_materialization_paths_outside_live_root(tmp_path) -> None:
    live_root = tmp_path / "live"
    outside = tmp_path / "outside"
    _write_live_root(live_root)
    settings_path = live_root / "config" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    payload["feature_flags"] = {
        "openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled": True,
        "openmeteo_ecmwf_ifs9_aifs_soft_anchor_veto_enabled": True,
        "openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled": False,
        "openmeteo_ecmwf_ifs9_aifs_soft_anchor_kelly_increase_enabled": False,
        "openmeteo_ecmwf_ifs9_aifs_soft_anchor_direction_flip_enabled": False,
    }
    payload["replacement_forecast_shadow"] = {
        "raw_manifest_dir": str(outside / "raw"),
        "request_dir": "state/replacement_forecast_shadow/requests",
        "processed_dir": "state/replacement_forecast_shadow/processed",
        "failed_dir": "state/replacement_forecast_shadow/failed",
        "seed_dir": "state/replacement_forecast_shadow/seeds",
        "seed_processed_dir": "state/replacement_forecast_shadow/seeds_processed",
        "seed_failed_dir": "state/replacement_forecast_shadow/seeds_failed",
        "forecast_db": "state/zeus-forecasts.db",
        "refit_handoff_path": "state/replacement_forecast_shadow/refit_handoff.json",
    }
    settings_path.write_text(json.dumps(payload), encoding="utf-8")

    report = build_replacement_forecast_runtime_wiring_audit(
        live_root=live_root,
        repo_root=REPO_ROOT,
        assume_current_facts=True,
        assume_shadow_schema=True,
        assume_refit_handoff=True,
        optional_dependencies=("requests",),
    )

    assert report.status == "RUNTIME_WIRING_BLOCKED"
    assert report.shadow_materialization_paths_status == "OUTSIDE_LIVE_ROOT"
    assert "REPLACEMENT_RUNTIME_WIRING_SHADOW_MATERIALIZATION_PATHS_NOT_READY" in report.reason_codes


def test_runtime_wiring_audit_cli_returns_ready_json(tmp_path) -> None:
    live_root = tmp_path / "live"
    receipt_path = tmp_path / "receipt.json"
    audit_receipt_path = tmp_path / "audit_receipt.json"
    _write_live_root(live_root)
    receipt_path.write_text(
        json.dumps({"status": "SHADOW_VETO_SWITCH_READY", "live_root_written": False}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/audit_replacement_forecast_runtime_wiring.py",
            "--live-root",
            str(live_root),
            "--repo-root",
            str(REPO_ROOT),
            "--apply-receipt-json",
            str(receipt_path),
            "--assume-shadow-veto",
            "--assume-current-facts",
            "--assume-shadow-schema",
            "--assume-refit-handoff",
            "--optional-dependency",
            "requests",
            "--receipt-json",
            str(audit_receipt_path),
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
    assert payload["status"] == "RUNTIME_WIRING_READY"
    assert payload["runtime_policy_status"] == "SHADOW_VETO_ONLY"
    assert json.loads(audit_receipt_path.read_text(encoding="utf-8"))["status"] == "RUNTIME_WIRING_READY"
