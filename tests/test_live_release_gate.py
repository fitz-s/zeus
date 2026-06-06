# Lifecycle: created=2026-05-21; last_reviewed=2026-05-23; last_reused=2026-05-23
# Purpose: Relationship tests for the read-only live release gate and its
#   fail-closed proof requirements.
# Reuse: Run when changing scripts/check_live_release_gate.py or live release
#   money-path proof requirements.
# Created: 2026-05-21
# Last reused/audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P0-1/P1-2/P1-7
#   + review5.23 P0-1 (forecasts DB gate) + P1-5 (stale redeem age-check)

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.check_live_release_gate import (
    PAPER_PROOF_KEYS, FAIL, PASS,
    STALE_REDEEM_TX_HASHED_SECONDS,
    _check_loaded_sha,
    evaluate_release_gate,
    parse_args,
)
from src.state.db import (
    init_schema,
    init_schema_forecasts,
)
SCHEMA_VERSION = 43          # B2: frozen PRAGMA user_version written by init_schema
SCHEMA_FORECASTS_VERSION = 7  # B2: frozen PRAGMA user_version written by init_schema_forecasts


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_world_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        init_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _make_forecasts_db(path: Path, *, with_live_eligible: bool = True) -> None:
    conn = sqlite3.connect(str(path))
    try:
        init_schema_forecasts(conn)
        if with_live_eligible:
            now_iso = datetime.now(timezone.utc).isoformat()
            expires_iso = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            conn.execute(
                """
                INSERT OR IGNORE INTO readiness_state
                    (readiness_id, scope_key, scope_type,
                     city_id, city_timezone, target_local_date, temperature_metric,
                     strategy_key, status, computed_at, expires_at, token_ids_json,
                     reason_codes_json, dependency_json, provenance_json)
                VALUES
                    ('test-r1', 'test:city_metric:test:UTC:2026-06-01:high:v1',
                     'city_metric', 'test', 'UTC', '2026-06-01', 'high',
                     'producer_readiness_v1', 'LIVE_ELIGIBLE', ?, ?, '[]', '[]', '{}', '{}')
                """,
                (now_iso, expires_iso),
            )
            conn.commit()
    finally:
        conn.close()


def _make_trade_db(
    path: Path,
    *,
    command_state: str = "ACKED",
    redeem_state: str = "REDEEM_CONFIRMED",
    redeem_requested_at: str | None = None,
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE settlement_commands (
                command_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO venue_commands(command_id, state) VALUES (?, ?)",
            ("entry-1", command_state),
        )
        if redeem_requested_at is not None:
            conn.execute(
                "INSERT INTO settlement_commands(command_id, state, requested_at) VALUES (?, ?, ?)",
                ("redeem-1", redeem_state, redeem_requested_at),
            )
        else:
            conn.execute(
                "INSERT INTO settlement_commands(command_id, state) VALUES (?, ?)",
                ("redeem-1", redeem_state),
            )
        conn.commit()
    finally:
        conn.close()


def _make_gate_args(
    tmp_path: Path,
    *,
    live_eligibility: str = "UNKNOWN",
    loaded_sha: str = "sha-a",
    with_forecasts_db: bool = True,
    settings_payload: dict[str, object] | None = None,
) -> object:
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    trade_db = tmp_path / "zeus_trades.db"
    loaded = tmp_path / "loaded_sha.json"
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    proof = tmp_path / "paper_proof.json"
    arm = tmp_path / "arm_gate_artifact.json"
    settings = tmp_path / "settings.json"

    _make_world_db(world_db)
    if with_forecasts_db:
        _make_forecasts_db(forecasts_db)
    _make_trade_db(trade_db)
    _write_json(loaded, {"loaded_sha": loaded_sha})
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(source, {"generated_at": generated_at})
    _write_json(status, {"generated_at": generated_at})
    _write_json(
        proof,
        {
            "status": PASS,
            "live_eligibility": live_eligibility,
            **{key: True for key in PAPER_PROOF_KEYS},
        },
    )
    _write_json(
        arm,
        {
            "schema": "edli_arm_gate_v1",
            "commit_sha": loaded_sha,
            "measurement_cmd_hash": "test-measurement",
            "capital_weighted_ev": 0.01,
            "production_n": 1,
            "per_city_n": {"London": 1},
            "ev_sigma": 1.0,
            "date_coverage": {"n_pairs": 1, "pairs": [["London", "2026-06-01"]]},
            "coverage_licensed": True,
        },
    )
    argv = [
        "--expected-sha", "sha-a",
        "--loaded-sha-file", str(loaded),
        "--world-db", str(world_db),
        "--forecasts-db", str(forecasts_db),
        "--trade-db", str(trade_db),
        "--source-health-json", str(source),
        "--status-json", str(status),
        "--paper-proof-json", str(proof),
        "--arm-artifact-json", str(arm),
        "--source-max-age-seconds", str(24 * 60 * 60),
        "--status-max-age-seconds", str(24 * 60 * 60),
    ]
    if settings_payload is not None:
        _write_json(settings, settings_payload)
        argv.extend(["--settings-json", str(settings)])
    return parse_args(argv)


def _settings_for_stage(stage: str, **overrides: object) -> dict[str, object]:
    reactor = {
        "legacy_cron": "disabled",
        "edli_submit_disabled_bridge": "submit_disabled_live_bridge",
        "edli_live_canary": "live",
        "edli_live": "live",
    }[stage]
    edli = {
        "enabled": stage != "legacy_cron",
        "live_execution_mode": stage,
        "reactor_mode": reactor,
        "market_channel_ingestor_enabled": stage in {"edli_submit_disabled_bridge", "edli_live_canary", "edli_live"},
        "edli_user_channel_reconcile_enabled": stage in {"edli_submit_disabled_bridge", "edli_live_canary", "edli_live"},
        "real_order_submit_enabled": stage in {"edli_live_canary", "edli_live"},
        "live_canary_enabled": stage in {"edli_live_canary", "edli_live"},
        "taker_fok_fak_live_enabled": stage in {"edli_live_canary", "edli_live"},
        "durable_submit_outbox_enabled": stage in {"edli_live_canary", "edli_live"},
        "edli_live_operator_authorized": stage == "edli_live",
    }
    edli.update(overrides)
    return {"edli_v1": edli}


def test_release_gate_passes_only_as_read_only_evidence(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path)

    report = evaluate_release_gate(args)

    assert report.status == PASS
    assert report.stage == "legacy_cron"
    assert report.passed_count == report.gate_count
    assert report.live_entries_allowed is False
    assert report.submit_allowed is False
    assert report.scaleout_allowed is False


def test_release_gate_is_stage_aware_for_edli_modes(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    bridge_args = _make_gate_args(bridge_root)
    bridge_args.stage = "edli_submit_disabled_bridge"
    bridge = evaluate_release_gate(bridge_args)
    assert bridge.status == PASS
    assert bridge.live_entries_allowed is False
    assert bridge.submit_allowed is False
    assert bridge.scaleout_allowed is False

    canary_root = tmp_path / "canary"
    canary_root.mkdir()
    canary_args = _make_gate_args(canary_root)
    canary_args.stage = "edli_live_canary"
    canary = evaluate_release_gate(canary_args)
    assert canary.status == PASS
    assert canary.stage_status == "WAITING_FOR_QUALIFYING_EVENT"
    assert canary.daemon_start_allowed is True
    assert canary.deploy_ready is False
    assert canary.live_entries_allowed is True
    assert canary.submit_allowed is True
    assert canary.scaleout_allowed is False

    live_root = tmp_path / "live"
    live_root.mkdir()
    live_args = _make_gate_args(live_root)
    live_args.stage = "edli_live"
    live = evaluate_release_gate(live_args)
    assert live.status == FAIL
    assert live.live_entries_allowed is False
    assert live.submit_allowed is False
    assert live.scaleout_allowed is False
    assert any(
        result.name == "edli_promotion_artifact" and result.status == FAIL
        for result in live.results
    )


def test_release_gate_canary_blocks_pending_reconcile(tmp_path: Path) -> None:
    from src.state.schema.edli_live_order_events_schema import ensure_tables

    args = _make_gate_args(tmp_path)
    args.stage = "edli_live_canary"
    conn = sqlite3.connect(str(args.world_db))
    try:
        ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state, last_sequence,
                last_event_type, last_event_hash, pending_reconcile, venue_order_id,
                updated_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-1:intent-1",
                "event-1",
                "intent-1",
                "SUBMIT_UNKNOWN",
                1,
                "SubmitUnknown",
                "hash-1",
                1,
                "venue-1",
                datetime.now(timezone.utc).isoformat(),
                1,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert report.submit_allowed is False
    assert any(
        result.name == "edli_stage_readiness" and "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN" in result.detail
        for result in report.results
    )


def test_release_gate_canary_does_not_require_paper_live_unknown(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, live_eligibility="READY")
    args.stage = "edli_live_canary"

    report = evaluate_release_gate(args)

    assert report.status == PASS
    assert report.submit_allowed is True
    assert all(result.name != "paper_money_path_proof" for result in report.results)


def test_release_gate_canary_does_not_consume_arm_gate_artifact(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path)
    args.stage = "edli_live_canary"
    args.arm_artifact_json.write_text(
        json.dumps(
            {
                "schema": "edli_arm_gate_v1",
                "commit_sha": "sha-a",
                "measurement_cmd_hash": "test-measurement",
                "capital_weighted_ev": 0.0,
                "production_n": 0,
                "per_city_n": {},
                "ev_sigma": 0.0,
                "date_coverage": {"n_pairs": 0, "pairs": []},
                "coverage_licensed": False,
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_release_gate(args)

    assert report.status == PASS
    assert report.submit_allowed is True
    assert all(result.name != "edli_arm_gate_artifact" for result in report.results)


def test_release_gate_live_still_requires_arm_gate_artifact(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, settings_payload=_settings_for_stage("edli_live"))
    args.stage = "edli_live"
    args.arm_artifact_json.write_text(
        json.dumps(
            {
                "schema": "edli_arm_gate_v1",
                "commit_sha": "sha-a",
                "measurement_cmd_hash": "test-measurement",
                "capital_weighted_ev": 0.0,
                "production_n": 0,
                "per_city_n": {},
                "ev_sigma": 0.0,
                "date_coverage": {"n_pairs": 0, "pairs": []},
                "coverage_licensed": False,
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert report.submit_allowed is False
    assert any(
        result.name == "edli_arm_gate_artifact"
        and result.status == FAIL
        and "EV_NOT_POSITIVE" in result.detail
        for result in report.results
    )


def test_release_gate_legacy_still_requires_paper_live_unknown(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, live_eligibility="READY")

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert any(result.name == "paper_money_path_proof" and result.status == FAIL for result in report.results)


def test_release_gate_stage_settings_reject_stage_mismatch(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, settings_payload=_settings_for_stage("legacy_cron"))
    args.stage = "edli_live_canary"

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert report.submit_allowed is False
    assert any(result.name == "stage_settings" and "live_execution_mode" in result.detail for result in report.results)


def test_release_gate_stage_settings_reject_reactor_mismatch(tmp_path: Path) -> None:
    args = _make_gate_args(
        tmp_path,
        settings_payload=_settings_for_stage("edli_live_canary", reactor_mode="live_no_submit"),
    )
    args.stage = "edli_live_canary"

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert any(result.name == "stage_settings" and "reactor_mode" in result.detail for result in report.results)


def test_release_gate_stage_settings_reject_live_submit_disabled(tmp_path: Path) -> None:
    args = _make_gate_args(
        tmp_path,
        settings_payload=_settings_for_stage("edli_live", real_order_submit_enabled=False),
    )
    args.stage = "edli_live"

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert report.scaleout_allowed is False
    assert any(result.name == "stage_settings" and "real_order_submit_enabled=false" in result.detail for result in report.results)


def test_release_gate_stage_settings_reject_live_taker_disabled(tmp_path: Path) -> None:
    args = _make_gate_args(
        tmp_path,
        settings_payload=_settings_for_stage("edli_live_canary", taker_fok_fak_live_enabled=False),
    )
    args.stage = "edli_live_canary"

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert report.submit_allowed is False
    assert any(
        result.name == "stage_settings" and "taker_fok_fak_live_enabled=false" in result.detail
        for result in report.results
    )


def test_release_gate_stage_settings_accept_matching_canary_config(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, settings_payload=_settings_for_stage("edli_live_canary"))
    args.stage = "edli_live_canary"

    report = evaluate_release_gate(args)

    assert report.status == PASS
    assert report.submit_allowed is True
    assert any(result.name == "stage_settings" and result.status == PASS for result in report.results)


def test_release_gate_fails_on_loaded_sha_mismatch(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, loaded_sha="stale-sha")

    report = evaluate_release_gate(args)

    assert report.status == "FAIL"
    assert any(result.name == "loaded_sha" and result.status == "FAIL" for result in report.results)


def test_release_gate_fails_on_old_world_schema(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path)
    conn = sqlite3.connect(str(args.world_db))
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        conn.commit()
    finally:
        conn.close()

    report = evaluate_release_gate(args)

    assert report.status == "FAIL"
    assert any(result.name == "world_schema" and result.status == "FAIL" for result in report.results)


def test_release_gate_fails_on_unknown_command_state(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path)
    conn = sqlite3.connect(str(args.trade_db))
    try:
        conn.execute("UPDATE venue_commands SET state='SUBMIT_UNKNOWN_SIDE_EFFECT'")
        conn.commit()
    finally:
        conn.close()

    report = evaluate_release_gate(args)

    assert report.status == "FAIL"
    assert any(result.name == "trade_state" and result.status == "FAIL" for result in report.results)


def test_release_gate_rejects_paper_proof_claiming_live_eligibility(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path, live_eligibility="READY")

    report = evaluate_release_gate(args)

    assert report.status == "FAIL"
    assert any(
        result.name == "paper_money_path_proof" and result.status == "FAIL"
        for result in report.results
    )


def test_loaded_sha_gate_fails_cleanly_when_git_sha_unavailable(monkeypatch) -> None:
    def _raise_git_error() -> str:
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr("scripts.check_live_release_gate._current_git_sha", _raise_git_error)

    result = _check_loaded_sha("", None)

    assert result.name == "loaded_sha"
    assert result.status == FAIL
    assert "expected_sha_unavailable" in result.detail


# ---------------------------------------------------------------------------
# P0-1 antibody: forecasts DB gates
# ---------------------------------------------------------------------------


def test_release_gate_fails_when_forecasts_db_missing(tmp_path: Path) -> None:
    """Gate must FAIL if forecasts DB does not exist. (review5.23 P0-1)"""
    args = _make_gate_args(tmp_path, with_forecasts_db=False)
    # forecasts_db path points to a non-existent file

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert any(r.name == "forecasts_schema" and r.status == FAIL for r in report.results)


def test_release_gate_fails_when_forecasts_schema_stale(tmp_path: Path) -> None:
    """Gate must FAIL if forecasts DB user_version != SCHEMA_FORECASTS_VERSION. (review5.23 P0-1)"""
    args = _make_gate_args(tmp_path)
    conn = sqlite3.connect(str(args.forecasts_db))
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_FORECASTS_VERSION - 1}")
        conn.commit()
    finally:
        conn.close()

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert any(r.name == "forecasts_schema" and r.status == FAIL for r in report.results)


def test_release_gate_fails_when_no_live_eligible_readiness(tmp_path: Path) -> None:
    """Gate must FAIL if no LIVE_ELIGIBLE readiness_state row exists. (review5.23 P0-1)"""
    # Build forecasts DB with BLOCKED readiness only
    forecasts_db = tmp_path / "zeus-forecasts.db"
    _make_forecasts_db(forecasts_db, with_live_eligible=False)
    conn = sqlite3.connect(str(forecasts_db))
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO readiness_state
                (readiness_id, scope_key, scope_type,
                 city_id, city_timezone, target_local_date, temperature_metric,
                 status, computed_at, token_ids_json, reason_codes_json,
                 dependency_json, provenance_json)
            VALUES
                ('blocked-r1', 'test:city_metric:test:UTC:2026-06-01:high:v1',
                 'city_metric', 'test', 'UTC', '2026-06-01', 'high',
                 'BLOCKED', ?, '[]', '[]', '{}', '{}')
            """,
            (now_iso,),
        )
        conn.commit()
    finally:
        conn.close()
    args = _make_gate_args(tmp_path)

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    assert any(r.name == "forecast_executable_bundle" and r.status == FAIL for r in report.results)


# ---------------------------------------------------------------------------
# P1-5 antibody: stale in-flight redeem state age-checks
# ---------------------------------------------------------------------------


def test_release_gate_fails_on_stale_redeem_tx_hashed(tmp_path: Path) -> None:
    """Gate must FAIL when REDEEM_TX_HASHED is older than the age threshold. (review5.23 P1-5)"""
    stale_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    trade_db = tmp_path / "zeus_trades.db"
    _make_world_db(world_db)
    _make_forecasts_db(forecasts_db)
    _make_trade_db(trade_db, redeem_state="REDEEM_TX_HASHED", redeem_requested_at=stale_ts)

    loaded = tmp_path / "loaded_sha.json"
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    proof = tmp_path / "paper_proof.json"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(loaded, {"loaded_sha": "sha-a"})
    _write_json(source, {"generated_at": generated_at})
    _write_json(status, {"generated_at": generated_at})
    _write_json(proof, {"status": PASS, "live_eligibility": "UNKNOWN", **{k: True for k in PAPER_PROOF_KEYS}})

    args = parse_args([
        "--expected-sha", "sha-a",
        "--loaded-sha-file", str(loaded),
        "--world-db", str(world_db),
        "--forecasts-db", str(forecasts_db),
        "--trade-db", str(trade_db),
        "--source-health-json", str(source),
        "--status-json", str(status),
        "--paper-proof-json", str(proof),
        "--source-max-age-seconds", str(24 * 60 * 60),
        "--status-max-age-seconds", str(24 * 60 * 60),
    ])

    report = evaluate_release_gate(args)

    assert report.status == FAIL
    stale_result = next((r for r in report.results if r.name == "redeem_state"), None)
    assert stale_result is not None
    assert stale_result.status == FAIL
    assert "stale_inflight_redeem" in stale_result.detail


def test_release_gate_passes_with_recent_redeem_tx_hashed(tmp_path: Path) -> None:
    """Gate must PASS when REDEEM_TX_HASHED is within the allowed age window. (review5.23 P1-5)"""
    # Recent timestamp: within STALE_REDEEM_TX_HASHED_SECONDS
    recent_ts = datetime.now(timezone.utc).isoformat()
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    trade_db = tmp_path / "zeus_trades.db"
    _make_world_db(world_db)
    _make_forecasts_db(forecasts_db)
    _make_trade_db(trade_db, redeem_state="REDEEM_TX_HASHED", redeem_requested_at=recent_ts)

    loaded = tmp_path / "loaded_sha.json"
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    proof = tmp_path / "paper_proof.json"
    generated_at = recent_ts
    _write_json(loaded, {"loaded_sha": "sha-a"})
    _write_json(source, {"generated_at": generated_at})
    _write_json(status, {"generated_at": generated_at})
    _write_json(proof, {"status": PASS, "live_eligibility": "UNKNOWN", **{k: True for k in PAPER_PROOF_KEYS}})

    args = parse_args([
        "--expected-sha", "sha-a",
        "--loaded-sha-file", str(loaded),
        "--world-db", str(world_db),
        "--forecasts-db", str(forecasts_db),
        "--trade-db", str(trade_db),
        "--source-health-json", str(source),
        "--status-json", str(status),
        "--paper-proof-json", str(proof),
        "--source-max-age-seconds", str(24 * 60 * 60),
        "--status-max-age-seconds", str(24 * 60 * 60),
    ])

    report = evaluate_release_gate(args)

    assert report.status == PASS
    assert any(r.name == "redeem_state" and r.status == PASS for r in report.results)


# ---------------------------------------------------------------------------
# P0-1/P1-6 antibody: NULL expires_at LIVE_ELIGIBLE row must fail gate
# ---------------------------------------------------------------------------


def test_release_gate_fails_when_live_eligible_has_null_expires_at(tmp_path: Path) -> None:
    """Gate must FAIL if LIVE_ELIGIBLE readiness row has NULL expires_at.

    Canonical write_readiness_state() rejects LIVE_ELIGIBLE without expires_at.
    The gate must enforce the same constraint so a non-canonical row cannot pass.
    review5.23 P0-1 + P1-6.
    """
    # Create supporting files via _make_gate_args (creates world/trade DBs + JSON files)
    # but skip forecasts DB so we can inject a null-expiry row ourselves.
    _make_gate_args(tmp_path, with_forecasts_db=False)

    forecasts_db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(forecasts_db))
    try:
        init_schema_forecasts(conn)
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO readiness_state
                (readiness_id, scope_key, scope_type,
                 city_id, city_timezone, target_local_date, temperature_metric,
                 status, computed_at, token_ids_json, reason_codes_json,
                 dependency_json, provenance_json)
            VALUES
                ('null-expiry-r1', 'test:city_metric:test:UTC:2026-06-01:high:v1',
                 'city_metric', 'test', 'UTC', '2026-06-01', 'high',
                 'LIVE_ELIGIBLE', ?, '[]', '[]', '{}', '{}')
            """,
            (now_iso,),
        )
        conn.commit()
    finally:
        conn.close()

    args = parse_args([
        "--expected-sha", "sha-a",
        "--loaded-sha-file", str(tmp_path / "loaded_sha.json"),
        "--world-db", str(tmp_path / "zeus-world.db"),
        "--forecasts-db", str(forecasts_db),
        "--trade-db", str(tmp_path / "zeus_trades.db"),
        "--source-health-json", str(tmp_path / "source_health.json"),
        "--status-json", str(tmp_path / "status_summary.json"),
        "--paper-proof-json", str(tmp_path / "paper_proof.json"),
        "--source-max-age-seconds", str(24 * 60 * 60),
        "--status-max-age-seconds", str(24 * 60 * 60),
    ])

    report = evaluate_release_gate(args)

    assert report.status == FAIL, "Gate must FAIL when LIVE_ELIGIBLE row has NULL expires_at"
    assert any(
        r.name == "forecast_executable_bundle" and r.status == FAIL for r in report.results
    ), "forecast_executable_bundle gate must FAIL for null-expiry row"


def test_release_gate_fails_when_live_eligible_has_null_strategy_key(tmp_path: Path) -> None:
    """Gate must FAIL if LIVE_ELIGIBLE readiness row has NULL strategy_key.
    Canonical writer always sets strategy_key; a row without it is non-canonical.
    review5.23 P0-1 (minimal fix) + P1-6."""
    _make_gate_args(tmp_path, with_forecasts_db=False)

    forecasts_db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(forecasts_db))
    try:
        init_schema_forecasts(conn)
        now_iso = datetime.now(timezone.utc).isoformat()
        expires_iso = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO readiness_state
                (readiness_id, scope_key, scope_type,
                 city_id, city_timezone, target_local_date, temperature_metric,
                 status, computed_at, expires_at, token_ids_json, reason_codes_json,
                 dependency_json, provenance_json)
            VALUES
                ('null-strategy-r1', 'test:city_metric:test:UTC:2026-06-01:high:v1',
                 'city_metric', 'test', 'UTC', '2026-06-01', 'high',
                 'LIVE_ELIGIBLE', ?, ?, '[]', '[]', '{}', '{}')
            """,
            (now_iso, expires_iso),
        )
        conn.commit()
    finally:
        conn.close()

    args = parse_args([
        "--expected-sha", "sha-a",
        "--loaded-sha-file", str(tmp_path / "loaded_sha.json"),
        "--world-db", str(tmp_path / "zeus-world.db"),
        "--forecasts-db", str(forecasts_db),
        "--trade-db", str(tmp_path / "zeus_trades.db"),
        "--source-health-json", str(tmp_path / "source_health.json"),
        "--status-json", str(tmp_path / "status_summary.json"),
        "--paper-proof-json", str(tmp_path / "paper_proof.json"),
        "--source-max-age-seconds", str(24 * 60 * 60),
        "--status-max-age-seconds", str(24 * 60 * 60),
    ])

    report = evaluate_release_gate(args)

    assert report.status == FAIL, "Gate must FAIL when LIVE_ELIGIBLE row has NULL strategy_key"
    assert any(
        r.name == "forecast_executable_bundle" and r.status == FAIL for r in report.results
    ), "forecast_executable_bundle gate must FAIL for null-strategy_key row"
