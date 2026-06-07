# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect read-only simple-switch evidence generation.
# Reuse: Run before changing current-fact evidence collection.
# Authority basis: Current fact patch evidence must be generated from inspectable artifacts or explicit overrides.
"""Replacement forecast simple-switch evidence tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.replacement_forecast_live_switch_surface import REQUIRED_FORECAST_TABLES, REQUIRED_LIVE_READ_FILES
from src.data.replacement_forecast_simple_switch_evidence import (
    AIFS_META_PATH,
    AIFS_SAMPLE_POINTS_PATH,
    COMPLETION_AUDIT_PATH,
    EVENT_REACTOR_NO_BYPASS_REPORT_PATH,
    FULL_REPLACEMENT_SUITE_REPORT_PATH,
    SHADOW_SCHEMA_DRY_RUN_REPORT_PATH,
    OpenMeteoIfs9EndpointProbeConfig,
    build_replacement_forecast_simple_switch_evidence_report,
    default_openmeteo_probe_run,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_artifacts(root: Path, worktree: Path) -> None:
    for relative in REQUIRED_LIVE_READ_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".db"):
            continue
        path.write_text("source routing current\n", encoding="utf-8")
    source_doc = root / "docs" / "operations" / "current_source_validity.md"
    source_doc.parent.mkdir(parents=True, exist_ok=True)
    source_doc.write_text("Status: CURRENT_FOR_LIVE\nsource routing unchanged\n", encoding="utf-8")
    with sqlite3.connect(root / "state" / "zeus-forecasts.db") as conn:
        for table in REQUIRED_FORECAST_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for db_name in ("zeus-world.db", "zeus_trades.db"):
        with sqlite3.connect(root / "state" / db_name) as conn:
            conn.execute("CREATE TABLE market_events (id INTEGER PRIMARY KEY)")
    raw_dir = worktree / ".local" / "replacement_raw"
    raw_dir.mkdir(parents=True)
    grib = raw_dir / "aifs_ens_20260605_00z_step0_pf_member001_2t.grib2"
    grib.write_bytes(b"fake-grib-for-sha")
    meta = {
        "path": str(grib),
        "sha256": _sha(b"fake-grib-for-sha"),
        "model": "aifs-ens",
        "param": "2t",
        "index_record": {"class": "ai", "model": "aifs-ens", "stream": "enfo", "type": "pf", "param": "2t"},
    }
    (worktree / AIFS_META_PATH).write_text(json.dumps(meta), encoding="utf-8")
    sample = {"points": [{"product_label": "A1", "value": 10.0}]}
    (worktree / AIFS_SAMPLE_POINTS_PATH).write_text(json.dumps(sample), encoding="utf-8")
    report_dir = worktree / ".local" / "replacement_reports"
    report_dir.mkdir(parents=True)
    audit = {
        "requirements": [
            {"requirement": "avoid fake/unreliable data", "status": "proved"},
            {"requirement": "extract raw data into evaluation/trading path", "status": "proved"},
        ]
    }
    (worktree / COMPLETION_AUDIT_PATH).write_text(json.dumps(audit), encoding="utf-8")


def test_simple_switch_evidence_reports_missing_without_overrides(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)

    report = build_replacement_forecast_simple_switch_evidence_report(root=root, worktree=worktree)

    assert report.complete is False
    assert "openmeteo_ecmwf_ifs9_endpoint_verified" in report.missing_source_evidence
    assert "full_replacement_test_suite_passed" in report.missing_data_evidence
    assert report.evidence["ecmwf_aifs_ens_download_verified"] is True
    assert report.evidence["aifs_sampled_2t_identity_verified"] is True


def test_simple_switch_evidence_can_probe_openmeteo_endpoint(monkeypatch, tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)
    calls = []

    def fake_fetch(request, *, timeout, max_retries):
        calls.append((request, timeout, max_retries))
        return {
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {
                "time": [f"2026-06-07T{hour:02d}:00" for hour in range(24)],
                "temperature_2m": [19.0 + hour * 0.25 for hour in range(24)],
            },
        }

    monkeypatch.setattr(
        "src.data.replacement_forecast_simple_switch_evidence.fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        fake_fetch,
    )

    report = build_replacement_forecast_simple_switch_evidence_report(
        root=root,
        worktree=worktree,
        openmeteo_probe=OpenMeteoIfs9EndpointProbeConfig(
            latitude=31.2304,
            longitude=121.4737,
            timezone_name="Asia/Shanghai",
            run=datetime(2026, 6, 6, 6, tzinfo=timezone.utc),
            target_local_date=date(2026, 6, 7),
            min_hourly_samples=20,
            timeout=3.0,
            max_retries=1,
        ),
    )

    assert report.evidence["openmeteo_ecmwf_ifs9_endpoint_verified"] is True
    assert "openmeteo_ecmwf_ifs9_endpoint_verified" not in report.missing_source_evidence
    assert calls[0][0].run_iso == "2026-06-06T06:00"
    assert calls[0][0].model == "ecmwf_ifs"
    assert calls[0][1:] == (3.0, 1)
    assert any("samples=24" in ref and "high_c=24.75" in ref for ref in report.evidence["evidence_refs"])


def test_simple_switch_evidence_probe_fails_closed(monkeypatch, tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)

    def fake_fetch(request, *, timeout, max_retries):
        return {"hourly": {"time": ["2026-06-06T00:00"], "temperature_2m": [20.0]}}

    monkeypatch.setattr(
        "src.data.replacement_forecast_simple_switch_evidence.fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        fake_fetch,
    )

    report = build_replacement_forecast_simple_switch_evidence_report(
        root=root,
        worktree=worktree,
        openmeteo_probe=OpenMeteoIfs9EndpointProbeConfig(
            latitude=31.2304,
            longitude=121.4737,
            timezone_name="Asia/Shanghai",
            run=datetime(2026, 6, 6, 6, tzinfo=timezone.utc),
            target_local_date=date(2026, 6, 7),
            min_hourly_samples=20,
        ),
    )

    assert report.evidence["openmeteo_ecmwf_ifs9_endpoint_verified"] is False
    assert "openmeteo_ecmwf_ifs9_endpoint_verified" in report.missing_source_evidence
    assert any("endpoint probe failed" in ref for ref in report.evidence["evidence_refs"])


def test_simple_switch_evidence_complete_with_explicit_remaining_overrides(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)

    report = build_replacement_forecast_simple_switch_evidence_report(
        root=root,
        worktree=worktree,
        overrides={
            "openmeteo_ecmwf_ifs9_endpoint_verified": True,
            "emos_product_identity_isolated": True,
            "refit_gate_blocks_promotion": True,
            "materialization_seed_builder_verified": True,
            "materialization_seed_discovery_verified": True,
            "materialization_request_builder_verified": True,
            "finetune_artifact_builder_verified": True,
            "refit_handoff_builder_verified": True,
            "refit_handoff_install_plan_verified": True,
            "promotion_evidence_composer_verified": True,
            "full_replacement_test_suite_passed": True,
            "event_reactor_no_bypass_suite_passed": True,
        },
    )

    assert report.complete is True
    assert report.missing_source_evidence == ()
    assert report.missing_data_evidence == ()
    assert report.evidence["evidence_refs"]


def test_simple_switch_evidence_reads_full_suite_report(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)
    report_path = worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH
    report_path.write_text(
        json.dumps(
            {
                "command": [
                    "-q",
                    "tests/test_replacement_forecast_*.py",
                    "tests/test_openmeteo_ecmwf_ifs9_*.py",
                    "tests/test_ecmwf_aifs_*.py",
                ],
                "returncode": 0,
                "summary": "237 passed in 10.68s",
            }
        ),
        encoding="utf-8",
    )

    report = build_replacement_forecast_simple_switch_evidence_report(root=root, worktree=worktree)

    assert report.evidence["full_replacement_test_suite_passed"] is True
    assert report.evidence["materialization_seed_builder_verified"] is True
    assert report.evidence["materialization_seed_discovery_verified"] is True
    assert report.evidence["materialization_request_builder_verified"] is True
    assert report.evidence["finetune_artifact_builder_verified"] is True
    assert report.evidence["refit_handoff_builder_verified"] is True
    assert report.evidence["refit_handoff_install_plan_verified"] is True
    assert report.evidence["promotion_evidence_composer_verified"] is True
    assert "full_replacement_test_suite_passed" not in report.missing_data_evidence
    assert "materialization_seed_builder_verified" not in report.missing_data_evidence
    assert "materialization_seed_discovery_verified" not in report.missing_data_evidence
    assert "materialization_request_builder_verified" not in report.missing_data_evidence
    assert "finetune_artifact_builder_verified" not in report.missing_data_evidence
    assert "refit_handoff_builder_verified" not in report.missing_data_evidence
    assert "refit_handoff_install_plan_verified" not in report.missing_data_evidence
    assert "promotion_evidence_composer_verified" not in report.missing_data_evidence
    assert any("237 passed" in ref for ref in report.evidence["evidence_refs"])


def test_simple_switch_evidence_requires_event_reactor_no_bypass_report(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)
    report_path = worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH
    report_path.write_text(
        json.dumps(
            {
                "command": [
                    "-q",
                    "tests/test_replacement_forecast_*.py",
                    "tests/test_openmeteo_ecmwf_ifs9_*.py",
                    "tests/test_ecmwf_aifs_*.py",
                ],
                "returncode": 0,
                "summary": "237 passed in 10.68s",
            }
        ),
        encoding="utf-8",
    )

    report = build_replacement_forecast_simple_switch_evidence_report(root=root, worktree=worktree)

    assert report.evidence["event_reactor_no_bypass_suite_passed"] is False
    assert "event_reactor_no_bypass_suite_passed" in report.missing_data_evidence


def test_simple_switch_evidence_reads_event_reactor_no_bypass_report(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)
    report_path = worktree / EVENT_REACTOR_NO_BYPASS_REPORT_PATH
    report_path.write_text(
        json.dumps(
            {
                "command": ["-q", "tests/engine/test_event_reactor_no_bypass.py"],
                "returncode": 0,
                "summary": "95 passed, 1 xfailed in 8.12s",
            }
        ),
        encoding="utf-8",
    )

    report = build_replacement_forecast_simple_switch_evidence_report(root=root, worktree=worktree)

    assert report.evidence["event_reactor_no_bypass_suite_passed"] is True
    assert "event_reactor_no_bypass_suite_passed" not in report.missing_data_evidence
    assert any("95 passed" in ref for ref in report.evidence["evidence_refs"])


def test_simple_switch_evidence_reads_schema_dry_run_report(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)
    report_path = worktree / SHADOW_SCHEMA_DRY_RUN_REPORT_PATH
    report_path.write_text(
        json.dumps(
            {
                "status": "READY",
                "committed": False,
                "created_tables": [
                    "raw_forecast_artifacts",
                    "deterministic_forecast_anchors",
                    "forecast_posteriors",
                    "replacement_shadow_decisions",
                ],
                "missing_replacement_shadow_tables": [],
                "missing_live_switch_forecast_tables_after": [],
            }
        ),
        encoding="utf-8",
    )

    report = build_replacement_forecast_simple_switch_evidence_report(root=root, worktree=worktree)

    assert report.evidence["replacement_shadow_schema_dry_run_passed"] is True
    assert "replacement_shadow_schema_dry_run_passed" not in report.missing_data_evidence
    assert any("schema dry-run verified" in ref for ref in report.evidence["evidence_refs"])


def test_simple_switch_evidence_cli_is_read_only(tmp_path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    _write_artifacts(root, worktree)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_simple_switch_evidence.py",
            "--root",
            str(root),
            "--worktree",
            str(worktree),
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
    assert payload["status"] == "SIMPLE_SWITCH_EVIDENCE_INCOMPLETE"
    assert "openmeteo_ecmwf_ifs9_endpoint_verified" in payload["missing_source_evidence"]


def test_default_openmeteo_probe_run_uses_published_cycle_lag() -> None:
    run = default_openmeteo_probe_run(datetime(2026, 6, 6, 8, 15, tzinfo=timezone.utc))

    assert run == datetime(2026, 6, 6, 6, tzinfo=timezone.utc)
