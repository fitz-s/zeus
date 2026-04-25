# Created: 2026-04-25
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Verify P2 4.4.B-lite backfill completeness manifest and threshold behavior.
# Reuse: Keep tests isolated from network and production DB state.
# Last reused/audited: 2026-04-25
# Authority basis: P2 4.4.B-lite backfill completeness manifests and fail-threshold guardrails
"""P2 4.4.B-lite backfill completeness guardrail regressions."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from scripts.backfill_completeness import (
    evaluate_completeness,
    write_manifest,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"failed to load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_evaluate_completeness_allows_failure_rate_equal_to_threshold() -> None:
    decision = evaluate_completeness(
        actual_count=9,
        failed_count=1,
        attempted_count=10,
        expected_count=None,
        fail_threshold_percent=10.0,
    )

    assert decision["passed"] is True
    assert decision["exit_code"] == 0


def test_evaluate_completeness_fails_above_threshold() -> None:
    decision = evaluate_completeness(
        actual_count=8,
        failed_count=2,
        attempted_count=10,
        expected_count=None,
        fail_threshold_percent=10.0,
    )

    assert decision["passed"] is False
    assert decision["exit_code"] == 1
    assert "failure_rate_exceeded_threshold" in decision["reasons"]


def test_evaluate_completeness_expected_count_uses_terminal_units() -> None:
    decision = evaluate_completeness(
        actual_count=8,
        failed_count=0,
        attempted_count=10,
        expected_count=10,
        fail_threshold_percent=0.0,
        legitimate_gap_count=2,
    )

    assert decision["passed"] is True
    assert decision["expected_shortfall"] == 0
    assert decision["legitimate_gap_count"] == 2


def test_write_manifest_schema(tmp_path: Path) -> None:
    decision = evaluate_completeness(
        actual_count=1,
        failed_count=0,
        attempted_count=1,
        expected_count=1,
        fail_threshold_percent=0.0,
    )
    path = tmp_path / "manifest.json"

    write_manifest(
        path,
        script_name="unit_test.py",
        run_id="unit-test",
        dry_run=True,
        inputs={"city": "Chicago"},
        counters={"rows": 1},
        completeness=decision,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["script_name"] == "unit_test.py"
    assert payload["mode"] == "dry_run"
    assert payload["completeness"]["passed"] is True


def test_obs_v2_main_writes_manifest_and_fails_on_failed_windows(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_obs_v2.py",
        "test_backfill_obs_v2_guardrails",
    )

    def fake_backfill(*_args, **_kwargs):
        return module.BackfillStats(
            city="Chicago",
            tier="WU_ICAO",
            station="KORD",
            rows_written=1,
            rows_ready=1,
            rows_raw=1,
            row_build_errors=0,
            windows_attempted=2,
            windows_failed=1,
            empty_windows=0,
        )

    monkeypatch.setattr(module, "_backfill_wu_city", fake_backfill)
    monkeypatch.setattr(
        "src.state.schema.v2_schema.apply_v2_schema",
        lambda _conn: None,
    )
    manifest = tmp_path / "obs_v2_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Chicago",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-21",
            "--data-version",
            "v1.test",
            "--db",
            str(tmp_path / "obs_v2.db"),
            "--completeness-manifest",
            str(manifest),
            "--fail-threshold-percent",
            "0",
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["script_name"] == "backfill_obs_v2.py"
    assert payload["completeness"]["passed"] is False
    assert payload["counters"]["windows_failed"] == 1
    assert payload["completeness"]["hard_blocker_reasons"] == ["failed_windows"]


def test_obs_v2_failed_windows_are_hard_blockers_above_threshold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_obs_v2.py",
        "test_backfill_obs_v2_failed_window_hard_blocker",
    )

    def fake_backfill(*_args, **_kwargs):
        return module.BackfillStats(
            city="Chicago",
            tier="WU_ICAO",
            station="KORD",
            rows_written=100,
            rows_ready=100,
            rows_raw=100,
            row_build_errors=0,
            windows_attempted=2,
            windows_failed=1,
            empty_windows=0,
        )

    monkeypatch.setattr(module, "_backfill_wu_city", fake_backfill)
    monkeypatch.setattr(
        "src.state.schema.v2_schema.apply_v2_schema",
        lambda _conn: None,
    )
    manifest = tmp_path / "obs_v2_threshold_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Chicago",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-21",
            "--data-version",
            "v1.test",
            "--db",
            str(tmp_path / "obs_v2.db"),
            "--completeness-manifest",
            str(manifest),
            "--fail-threshold-percent",
            "99",
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["completeness"]["failure_rate_percent"] == 0.0
    assert payload["completeness"]["hard_blocker_reasons"] == ["failed_windows"]


def test_obs_v2_empty_windows_are_hard_blockers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_obs_v2.py",
        "test_backfill_obs_v2_empty_window_hard_blocker",
    )

    def fake_backfill(*_args, **_kwargs):
        return module.BackfillStats(
            city="Chicago",
            tier="WU_ICAO",
            station="KORD",
            rows_written=0,
            rows_ready=0,
            rows_raw=0,
            row_build_errors=0,
            windows_attempted=1,
            windows_failed=0,
            empty_windows=1,
        )

    monkeypatch.setattr(module, "_backfill_wu_city", fake_backfill)
    monkeypatch.setattr(
        "src.state.schema.v2_schema.apply_v2_schema",
        lambda _conn: None,
    )
    manifest = tmp_path / "obs_v2_empty_window_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Chicago",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-20",
            "--data-version",
            "v1.test",
            "--db",
            str(tmp_path / "obs_v2.db"),
            "--completeness-manifest",
            str(manifest),
            "--fail-threshold-percent",
            "99",
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["completeness"]["hard_blocker_reasons"] == ["empty_windows"]


def test_obs_v2_dry_run_counts_row_build_errors_as_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_obs_v2.py",
        "test_backfill_obs_v2_row_build_errors",
    )

    monkeypatch.setattr(module, "_retry_schedule", lambda: [])
    monkeypatch.setattr(
        module,
        "fetch_wu_hourly",
        lambda **_kwargs: SimpleNamespace(
            failed=False,
            retryable=False,
            failure_reason=None,
            raw_observation_count=1,
            observations=[SimpleNamespace(utc_timestamp="2026-04-20T12:00:00Z")],
        ),
    )
    monkeypatch.setattr(
        module,
        "_hourly_obs_to_v2_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad row")),
    )

    stats = module._backfill_wu_city(
        _memdb(),
        "Chicago",
        date(2026, 4, 20),
        date(2026, 4, 20),
        "v1.test",
        tmp_path / "obs_v2_backfill.jsonl",
        True,
    )

    assert stats.rows_ready == 0
    assert stats.row_build_errors == 1
    assert stats.empty_windows == 0


def test_obs_v2_main_fails_when_requested_city_is_unsupported(
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_obs_v2.py",
        "test_backfill_obs_v2_unsupported_city",
    )
    manifest = tmp_path / "obs_v2_unsupported_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Hong Kong",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-21",
            "--data-version",
            "v1.test",
            "--db",
            str(tmp_path / "obs_v2.db"),
            "--completeness-manifest",
            str(manifest),
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["counters"]["unsupported_cities"] == ["Hong Kong"]
    assert payload["completeness"]["passed"] is False
    assert payload["completeness"]["hard_blocker_reasons"] == ["unsupported_cities"]


def test_wu_daily_main_writes_manifest_and_fails_on_guardrail_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_wu_daily_all.py",
        "test_backfill_wu_daily_all_guardrails",
    )
    monkeypatch.setattr(module, "get_world_connection", _memdb)
    monkeypatch.setattr(module, "init_schema", lambda _conn: None)
    monkeypatch.setattr(
        module,
        "backfill_city",
        lambda *_args, **_kwargs: {
            "city": "Chicago",
            "collected": 1,
            "skip": 0,
            "err": 1,
            "guard_rejected": 0,
            "requests": 1,
        },
    )
    manifest = tmp_path / "wu_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Chicago",
            "--days",
            "2",
            "--dry-run",
            "--completeness-manifest",
            str(manifest),
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["script_name"] == "backfill_wu_daily_all.py"
    assert payload["counters"]["failed"] == 1


def test_wu_daily_manifest_run_id_matches_backfill_run_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_wu_daily_all.py",
        "test_backfill_wu_daily_all_run_id",
    )
    captured: dict[str, str | None] = {}
    monkeypatch.setattr(module, "get_world_connection", _memdb)
    monkeypatch.setattr(module, "init_schema", lambda _conn: None)

    def fake_backfill(*_args, **kwargs):
        captured["run_id"] = kwargs["rebuild_run_id"]
        return {
            "city": "Chicago",
            "collected": 1,
            "skip": 0,
            "err": 0,
            "guard_rejected": 0,
            "requests": 1,
        }

    monkeypatch.setattr(module, "backfill_city", fake_backfill)
    manifest = tmp_path / "wu_run_id_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Chicago",
            "--days",
            "1",
            "--dry-run",
            "--completeness-manifest",
            str(manifest),
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured["run_id"] == payload["run_id"]


def test_hko_daily_main_writes_manifest_and_fails_on_fetch_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_hko_daily.py",
        "test_backfill_hko_daily_guardrails",
    )
    monkeypatch.setattr(module, "get_world_connection", _memdb)
    monkeypatch.setattr(module, "init_schema", lambda _conn: None)
    stats = {
        "months_fetched": 1,
        "days_complete": 1,
        "days_incomplete": 0,
        "days_unavailable": 0,
        "inserted": 1,
        "guard_rejected": 0,
        "fetch_errors": 1,
        "insert_errors": 0,
    }
    monkeypatch.setattr(module, "run_backfill", lambda *_args, **_kwargs: stats)
    manifest = tmp_path / "hko_manifest.json"

    exit_code = module.main(
        [
            "--start",
            "2026-01",
            "--end",
            "2026-01",
            "--dry-run",
            "--completeness-manifest",
            str(manifest),
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["script_name"] == "backfill_hko_daily.py"
    assert payload["counters"]["fetch_errors"] == 1


def test_hko_daily_legitimate_gaps_do_not_fail_completeness(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_hko_daily.py",
        "test_backfill_hko_daily_legitimate_gaps",
    )
    monkeypatch.setattr(module, "get_world_connection", _memdb)
    monkeypatch.setattr(module, "init_schema", lambda _conn: None)
    stats = {
        "months_fetched": 1,
        "days_complete": 1,
        "days_incomplete": 1,
        "days_unavailable": 1,
        "inserted": 1,
        "guard_rejected": 0,
        "fetch_errors": 0,
        "insert_errors": 0,
    }
    monkeypatch.setattr(module, "run_backfill", lambda *_args, **_kwargs: stats)
    manifest = tmp_path / "hko_legitimate_gaps_manifest.json"

    exit_code = module.main(
        [
            "--start",
            "2026-01",
            "--end",
            "2026-01",
            "--dry-run",
            "--expected-count",
            "3",
            "--completeness-manifest",
            str(manifest),
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["counters"]["legitimate_gap_count"] == 2
    assert payload["completeness"]["expected_shortfall"] == 0


def test_ogimet_main_writes_manifest_and_fails_on_skipped_days(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        REPO_ROOT / "scripts" / "backfill_ogimet_metar.py",
        "test_backfill_ogimet_metar_guardrails",
    )
    monkeypatch.setattr(
        module,
        "backfill_city",
        lambda *_args, **_kwargs: {
            "city": "Istanbul",
            "days_written": 1,
            "days_skipped": 1,
        },
    )
    manifest = tmp_path / "ogimet_manifest.json"

    exit_code = module.main(
        [
            "--cities",
            "Istanbul",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-02",
            "--dry-run",
            "--db",
            str(tmp_path / "ogimet.db"),
            "--completeness-manifest",
            str(manifest),
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["script_name"] == "backfill_ogimet_metar.py"
    assert payload["counters"]["days_skipped"] == 1
