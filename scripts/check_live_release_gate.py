#!/usr/bin/env python3
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-23; last_reused=never
# Purpose: Read-only release gate proving loaded SHA, schema, order/redeem state,
#   freshness, forecasts DB schema, executable forecast bundle, and paper proof
#   before any live-money enablement claim.
# Reuse: Run before live-release claims or when touching money-path release gates.
# Created: 2026-05-21
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P0-1/P1-2/P1-7
#   + review5.23 P0-1 (forecasts DB gate) + P1-5 (stale redeem age-check)
"""Read-only live-release gate for the Zeus money path.

This script does not authorize live trading by itself. It fails unless the
operator-provided runtime evidence proves one coherent release reality:
loaded SHA, current schemas, clean order/redeem state, fresh business-plane
status, and a paper money-path proof.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import (
    SchemaOutOfDateError,
    assert_schema_current_forecasts,
    init_schema,
    init_schema_forecasts,
)
SCHEMA_VERSION = 43           # B2: frozen PRAGMA user_version written by init_schema
SCHEMA_FORECASTS_VERSION = 7  # B2: frozen PRAGMA user_version written by init_schema_forecasts
# B2: row-provenance value frozen in no_trade_events / decision_events schema_version columns.
# Distinct from SCHEMA_VERSION (PRAGMA user_version) because the row-level counter
# was last bumped at 42 and never reached 43 before the counter was cancelled.
_ROW_SCHEMA_VERSION = 42
from src.state.no_trade_events import (
    NoTradeEventsSchemaCompatibilityError,
    assert_no_trade_events_schema_current_for_live,
)

PASS = "PASS"
FAIL = "FAIL"
LIVE_RELEASE_STAGES = (
    "legacy_cron",
    "edli_submit_disabled_bridge",
    "edli_live_canary",
    "edli_live",
)

UNKNOWN_COMMAND_STATES = (
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "UNKNOWN",
    "REVIEW_REQUIRED",
)
BLOCKING_REDEEM_STATES = (
    "REDEEM_OPERATOR_REQUIRED",
    "REDEEM_REVIEW_REQUIRED",
)
# In-flight redeem states that block release if older than these thresholds.
# Chain confirmation should complete well within these windows; older rows are stuck.
STALE_REDEEM_SUBMITTED_SECONDS = 30 * 60   # 30 min
STALE_REDEEM_TX_HASHED_SECONDS = 60 * 60   # 60 min — awaiting receipt
STALE_REDEEM_RETRYING_SECONDS = 60 * 60    # 60 min — retry loop should self-terminate
PAPER_PROOF_KEYS = (
    "scanner",
    "forecast",
    "evaluator",
    "event_persistence",
    "command_repo",
    "reconcile",
    "redeem_reconciler",
)


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class ReleaseGateReport:
    status: str
    stage_status: str
    gate_count: int
    passed_count: int
    stage: str
    daemon_start_allowed: bool
    deploy_ready: bool
    live_entries_allowed: bool
    submit_allowed: bool
    scaleout_allowed: bool
    gate_basis: tuple[str, ...]
    results: tuple[GateResult, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [asdict(result) for result in self.results]
        return payload


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fresh(payload: dict[str, Any], *, max_age_seconds: int, now: datetime) -> tuple[bool, str]:
    stamp = (
        payload.get("generated_at")
        or payload.get("updated_at")
        or payload.get("observed_at")
        or payload.get("captured_at")
    )
    parsed = _parse_time(stamp)
    if parsed is None:
        return False, "missing_timestamp"
    age = (now - parsed).total_seconds()
    if age < 0:
        return False, f"timestamp_in_future:{parsed.isoformat()}"
    if age > max_age_seconds:
        return False, f"stale:{age:.0f}s>{max_age_seconds}s"
    return True, f"fresh:{age:.0f}s"


def _current_git_sha(repo: Path = ROOT) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()


def _check_loaded_sha(expected_sha: str, loaded_sha_file: Path | None) -> GateResult:
    expected = expected_sha.strip()
    if not expected:
        try:
            expected = _current_git_sha()
        except (OSError, subprocess.CalledProcessError) as exc:
            return GateResult(
                "loaded_sha",
                FAIL,
                f"expected_sha_unavailable:{type(exc).__name__}:{exc}",
            )
    payload = _json_load(loaded_sha_file) if loaded_sha_file else {}
    loaded = (
        payload.get("loaded_sha")
        or payload.get("boot_sha")
        or payload.get("current_sha")
        or ""
    )
    if not loaded:
        return GateResult("loaded_sha", FAIL, "missing_loaded_sha")
    status = PASS if str(loaded).strip() == expected else FAIL
    return GateResult("loaded_sha", status, f"loaded={loaded} expected={expected}")


def _check_world_schema(world_db: Path) -> GateResult:
    if not world_db.exists():
        return GateResult("world_schema", FAIL, f"missing:{world_db}")
    conn = sqlite3.connect(str(world_db))
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            return GateResult("world_schema", FAIL, f"user_version={version} expected={SCHEMA_VERSION}")
        try:
            # B2: use _ROW_SCHEMA_VERSION (42), not SCHEMA_VERSION (43 PRAGMA user_version).
            # Row-provenance counter was frozen at 42; no_trade_events CHECK only goes to 42.
            assert_no_trade_events_schema_current_for_live(
                conn,
                expected_schema_version=_ROW_SCHEMA_VERSION,
            )
        except NoTradeEventsSchemaCompatibilityError as exc:
            return GateResult("world_schema", FAIL, str(exc))
        return GateResult("world_schema", PASS, f"user_version={version}")
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count_where_in(conn: sqlite3.Connection, table: str, column: str, values: Iterable[str]) -> int:
    values = tuple(values)
    placeholders = ",".join("?" for _ in values)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders})",
        values,
    ).fetchone()
    return int(row[0] if row else 0)


def _check_forecasts_schema(forecasts_db: Path) -> GateResult:
    """Gate: forecasts DB exists and schema version matches SCHEMA_FORECASTS_VERSION."""
    if not forecasts_db.exists():
        return GateResult(
            "forecasts_schema",
            FAIL,
            f"missing:{forecasts_db} — forecasts DB does not exist; init_schema_forecasts not run",
        )
    conn = sqlite3.connect(str(forecasts_db))
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_FORECASTS_VERSION:
            return GateResult(
                "forecasts_schema",
                FAIL,
                f"user_version={version} expected={SCHEMA_FORECASTS_VERSION}",
            )
        try:
            assert_schema_current_forecasts(conn)
        except SchemaOutOfDateError as exc:
            return GateResult("forecasts_schema", FAIL, str(exc))
        return GateResult("forecasts_schema", PASS, f"user_version={version}")
    finally:
        conn.close()


def _check_forecast_executable_bundle(forecasts_db: Path, now: datetime) -> GateResult:
    """Gate: at least one non-expired LIVE_ELIGIBLE readiness_state row in forecasts DB.

    This is a lightweight proxy for 'read_executable_forecast() can return LIVE_ELIGIBLE
    for at least one scope'. It does not exercise the full bundle-selection logic but
    proves the readiness graph has been populated and has not fully expired.
    """
    if not forecasts_db.exists():
        return GateResult(
            "forecast_executable_bundle",
            FAIL,
            f"missing:{forecasts_db}",
        )
    conn = sqlite3.connect(str(forecasts_db))
    try:
        if not _table_exists(conn, "readiness_state"):
            return GateResult(
                "forecast_executable_bundle",
                FAIL,
                "missing_table:readiness_state — init_schema_forecasts not run",
            )
        now_iso = now.isoformat()
        row = conn.execute(
            """
            SELECT COUNT(*) FROM readiness_state
            WHERE status = 'LIVE_ELIGIBLE'
              AND expires_at IS NOT NULL
              AND expires_at > ?
              AND strategy_key IS NOT NULL
            """,
            (now_iso,),
        ).fetchone()
        count = int(row[0] if row else 0)
        if count == 0:
            return GateResult(
                "forecast_executable_bundle",
                FAIL,
                "no_live_eligible_readiness_state:forecast_live_daemon_not_running_or_all_expired",
            )
        return GateResult("forecast_executable_bundle", PASS, f"live_eligible_count={count}")
    finally:
        conn.close()


def _check_trade_state(trade_db: Path) -> GateResult:
    if not trade_db.exists():
        return GateResult("trade_state", FAIL, f"missing:{trade_db}")
    conn = sqlite3.connect(str(trade_db))
    try:
        if not _table_exists(conn, "venue_commands"):
            return GateResult("trade_state", FAIL, "missing_table:venue_commands")
        blocking = _count_where_in(conn, "venue_commands", "state", UNKNOWN_COMMAND_STATES)
        if blocking:
            return GateResult("trade_state", FAIL, f"blocking_unknown_commands={blocking}")
        return GateResult("trade_state", PASS, "no_unknown_commands")
    finally:
        conn.close()


def _check_redeem_state(
    trade_db: Path, allow_redeem_command: tuple[str, ...], now: datetime
) -> GateResult:
    if not trade_db.exists():
        return GateResult("redeem_state", FAIL, f"missing:{trade_db}")
    conn = sqlite3.connect(str(trade_db))
    try:
        if not _table_exists(conn, "settlement_commands"):
            return GateResult("redeem_state", FAIL, "missing_table:settlement_commands")
        allow = set(allow_redeem_command)

        # Hard-block states: always fail unless explicitly whitelisted.
        placeholders = ",".join("?" for _ in BLOCKING_REDEEM_STATES)
        hard_rows = conn.execute(
            f"""
            SELECT command_id, state
            FROM settlement_commands
            WHERE state IN ({placeholders})
            """,
            BLOCKING_REDEEM_STATES,
        ).fetchall()
        blocking = [(str(r[0]), str(r[1])) for r in hard_rows if str(r[0]) not in allow]
        if blocking:
            return GateResult("redeem_state", FAIL, f"blocking_redeem_rows={blocking[:5]}")

        # Age-check in-flight states: fail if stuck beyond expected confirmation window.
        age_states = (
            ("REDEEM_SUBMITTED", STALE_REDEEM_SUBMITTED_SECONDS),
            ("REDEEM_TX_HASHED", STALE_REDEEM_TX_HASHED_SECONDS),
            ("REDEEM_RETRYING", STALE_REDEEM_RETRYING_SECONDS),
        )
        stale_findings: list[str] = []
        for state, max_age in age_states:
            rows = conn.execute(
                """
                SELECT command_id, requested_at
                FROM settlement_commands
                WHERE state = ?
                """,
                (state,),
            ).fetchall()
            for command_id, requested_at in rows:
                if str(command_id) in allow:
                    continue
                parsed = _parse_time(requested_at)
                if parsed is None:
                    stale_findings.append(f"{command_id}:{state}:no_timestamp")
                    continue
                age = (now - parsed).total_seconds()
                if age > max_age:
                    stale_findings.append(f"{command_id}:{state}:age={age:.0f}s>{max_age}s")
        if stale_findings:
            return GateResult(
                "redeem_state", FAIL, f"stale_inflight_redeem={stale_findings[:5]}"
            )
        return GateResult("redeem_state", PASS, "no_unwhitelisted_blocking_or_stale_redeem_rows")
    finally:
        conn.close()


def _check_fresh_file(name: str, path: Path, *, max_age_seconds: int, now: datetime) -> GateResult:
    if not path.exists():
        return GateResult(name, FAIL, f"missing:{path}")
    ok, detail = _fresh(_json_load(path), max_age_seconds=max_age_seconds, now=now)
    return GateResult(name, PASS if ok else FAIL, detail)


def _check_paper_proof(path: Path) -> GateResult:
    if not path.exists():
        return GateResult("paper_money_path_proof", FAIL, f"missing:{path}")
    payload = _json_load(path)
    if payload.get("status") != PASS:
        return GateResult("paper_money_path_proof", FAIL, f"status={payload.get('status')!r}")
    missing = [key for key in PAPER_PROOF_KEYS if payload.get(key) is not True]
    if missing:
        return GateResult("paper_money_path_proof", FAIL, f"missing_or_false={missing}")
    if payload.get("live_eligibility") != "UNKNOWN":
        return GateResult("paper_money_path_proof", FAIL, "live_eligibility_not_unknown")
    return GateResult("paper_money_path_proof", PASS, "paper_path_proven_live_unknown")


def evaluate_release_gate(args: argparse.Namespace) -> ReleaseGateReport:
    now = _utc_now()
    results = [
        _check_loaded_sha(args.expected_sha, args.loaded_sha_file),
        _check_world_schema(args.world_db),
        _check_forecasts_schema(args.forecasts_db),
        _check_forecast_executable_bundle(args.forecasts_db, now),
        _check_trade_state(args.trade_db),
        _check_redeem_state(args.trade_db, tuple(args.allow_redeem_command or ()), now),
        _check_fresh_file("source_health", args.source_health_json, max_age_seconds=args.source_max_age_seconds, now=now),
        _check_fresh_file("status_summary", args.status_json, max_age_seconds=args.status_max_age_seconds, now=now),
    ]
    if str(args.stage) in {"legacy_cron", "edli_submit_disabled_bridge"}:
        results.append(_check_paper_proof(args.paper_proof_json))
    if args.settings_json:
        results.append(_check_stage_settings(args.stage, args.settings_json))
    results.extend(_edli_stage_results(args))
    passed = sum(1 for result in results if result.status == PASS)
    status = PASS if passed == len(results) else FAIL
    live_entries_allowed, submit_allowed, scaleout_allowed, gate_basis = _stage_allowance(
        str(args.stage),
        status=status,
        results=tuple(results),
    )
    stage_status = _stage_status(str(args.stage), status=status, results=tuple(results))
    daemon_start_allowed = status == PASS and stage_status in {PASS, "WAITING_FOR_QUALIFYING_EVENT"}
    deploy_ready = status == PASS and stage_status == PASS
    return ReleaseGateReport(
        status=status,
        stage_status=stage_status,
        gate_count=len(results),
        passed_count=passed,
        stage=str(args.stage),
        daemon_start_allowed=daemon_start_allowed,
        deploy_ready=deploy_ready,
        live_entries_allowed=live_entries_allowed,
        submit_allowed=submit_allowed,
        scaleout_allowed=scaleout_allowed,
        gate_basis=gate_basis,
        results=tuple(results),
    )


def _edli_stage_results(args: argparse.Namespace) -> list[GateResult]:
    stage = str(args.stage)
    if stage not in {"edli_submit_disabled_bridge", "edli_live_canary", "edli_live"}:
        return []
    import src.main as main

    if args.expected_sha:
        main._BOOT_STATE["sha"] = str(args.expected_sha)

    report = main.evaluate_edli_stage_readiness(
        stage=stage,
        world_db_path=str(args.world_db),
        trade_db_path=str(args.trade_db),
        forecasts_db_path=str(args.forecasts_db),
        loaded_sha_file=str(args.loaded_sha_file) if args.loaded_sha_file else "",
        canary_artifact_path=str(args.canary_artifact_json) if args.canary_artifact_json else "",
        promotion_artifact_path=str(args.promotion_artifact_json) if args.promotion_artifact_json else "",
        source_health_json=str(args.source_health_json),
        status_json=str(args.status_json),
        max_age_seconds=min(int(args.source_max_age_seconds), int(args.status_max_age_seconds)),
    )
    if stage == "edli_live_canary":
        ok = report.status in {main.EDLI_STAGE_PASS, main.EDLI_STAGE_WAITING} and report.submit_allowed
        return [
            GateResult(
                "edli_stage_readiness",
                PASS if ok else FAIL,
                f"status={report.status}:submit_allowed={report.submit_allowed}:reasons={list(report.reasons)}",
            )
        ]
    if stage == "edli_submit_disabled_bridge":
        ok = report.status in {main.EDLI_STAGE_PASS, main.EDLI_STAGE_WAITING} and not report.submit_allowed
        return [
            GateResult(
                "edli_stage_readiness",
                PASS if ok else FAIL,
                f"status={report.status}:submit_allowed={report.submit_allowed}:reasons={list(report.reasons)}",
            )
        ]
    results = [
        GateResult(
            "edli_stage_readiness",
            PASS if report.status == main.EDLI_STAGE_PASS else FAIL,
            f"status={report.status}:scaleout_allowed={report.scaleout_allowed}:reasons={list(report.reasons)}",
        )
    ]
    results.append(_check_edli_arm_gate_artifact(args))
    results.append(_check_edli_promotion_artifact(args))
    return results


def _check_edli_arm_gate_artifact(args: argparse.Namespace) -> GateResult:
    path = getattr(args, "arm_artifact_json", None)
    if not path:
        return GateResult("edli_arm_gate_artifact", FAIL, "missing_arm_artifact_path")
    if not path.exists():
        return GateResult("edli_arm_gate_artifact", FAIL, f"missing:{path}")
    from src.events.live_profit_audit import verify_edli_arm_gate_artifact

    try:
        artifact = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return GateResult("edli_arm_gate_artifact", FAIL, f"invalid_json:{exc}")
    verified = verify_edli_arm_gate_artifact(artifact, head_sha=str(args.expected_sha or ""))
    return GateResult(
        "edli_arm_gate_artifact",
        PASS if verified.ok else FAIL,
        verified.reason,
    )


def _check_edli_promotion_artifact(args: argparse.Namespace) -> GateResult:
    if not args.promotion_artifact_json:
        return GateResult("edli_promotion_artifact", FAIL, "missing_promotion_artifact_path")
    if not args.promotion_artifact_json.exists():
        return GateResult("edli_promotion_artifact", FAIL, f"missing:{args.promotion_artifact_json}")
    from src.events.live_profit_audit import verify_edli_live_promotion_artifact

    conn = sqlite3.connect(str(args.world_db))
    conn.row_factory = sqlite3.Row
    try:
        artifact = json.loads(args.promotion_artifact_json.read_text())
        verified = verify_edli_live_promotion_artifact(
            conn,
            artifact,
            min_canary_count=int(args.edli_live_min_canary_count),
            max_unresolved_unknowns=int(args.edli_live_max_unresolved_unknowns),
            min_realized_edge_bps=float(args.edli_live_min_realized_edge_bps),
        )
    finally:
        conn.close()
    return GateResult(
        "edli_promotion_artifact",
        PASS if verified.ok else FAIL,
        verified.reason,
    )


_REACTOR_MODE_BY_STAGE = {
    "legacy_cron": "disabled",
    "edli_submit_disabled_bridge": "submit_disabled_live_bridge",
    "edli_live_canary": "live",
    "edli_live": "live",
}


def _check_stage_settings(stage: str, settings_json: Path) -> GateResult:
    if not settings_json.exists():
        return GateResult("stage_settings", FAIL, f"missing:{settings_json}")
    try:
        settings = json.loads(settings_json.read_text())
    except json.JSONDecodeError as exc:
        return GateResult("stage_settings", FAIL, f"invalid_json:{exc}")
    edli_cfg = settings.get("edli_v1") if isinstance(settings, dict) else None
    if not isinstance(edli_cfg, dict):
        return GateResult("stage_settings", FAIL, "missing_edli_v1")
    errors: list[str] = []
    if str(edli_cfg.get("live_execution_mode") or "") != stage:
        errors.append(f"live_execution_mode={edli_cfg.get('live_execution_mode')!r}")
    expected_reactor = _REACTOR_MODE_BY_STAGE.get(stage)
    if expected_reactor and str(edli_cfg.get("reactor_mode") or "disabled") != expected_reactor:
        errors.append(f"reactor_mode={edli_cfg.get('reactor_mode')!r}:expected={expected_reactor}")
    if stage == "legacy_cron":
        if bool(edli_cfg.get("enabled", False)):
            errors.append("enabled=true")
        if bool(edli_cfg.get("real_order_submit_enabled", False)):
            errors.append("real_order_submit_enabled=true")
    if stage in {"edli_submit_disabled_bridge", "edli_live_canary", "edli_live"}:
        if not bool(edli_cfg.get("enabled", False)):
            errors.append("enabled=false")
        if not bool(edli_cfg.get("market_channel_ingestor_enabled", False)):
            errors.append("market_channel_ingestor_enabled=false")
        if not bool(edli_cfg.get("edli_user_channel_reconcile_enabled", False)):
            errors.append("edli_user_channel_reconcile_enabled=false")
    if stage == "edli_submit_disabled_bridge" and bool(edli_cfg.get("real_order_submit_enabled", False)):
        errors.append("real_order_submit_enabled=true")
    if stage in {"edli_live_canary", "edli_live"}:
        if not bool(edli_cfg.get("real_order_submit_enabled", False)):
            errors.append("real_order_submit_enabled=false")
        if not bool(edli_cfg.get("live_canary_enabled", False)):
            errors.append("live_canary_enabled=false")
        if not bool(edli_cfg.get("durable_submit_outbox_enabled", False)):
            errors.append("durable_submit_outbox_enabled=false")
    if stage == "edli_live" and not bool(edli_cfg.get("edli_live_operator_authorized", False)):
        errors.append("edli_live_operator_authorized=false")
    if errors:
        return GateResult("stage_settings", FAIL, ",".join(errors))
    return GateResult("stage_settings", PASS, f"settings_match_stage:{stage}")


def _result_status(results: tuple[GateResult, ...], name: str) -> str | None:
    for result in results:
        if result.name == name:
            return result.status
    return None


def _result_detail(results: tuple[GateResult, ...], name: str) -> str:
    for result in results:
        if result.name == name:
            return result.detail
    return ""


def _stage_status(stage: str, *, status: str, results: tuple[GateResult, ...]) -> str:
    if status != PASS:
        return FAIL
    if stage == "edli_live_canary":
        detail = _result_detail(results, "edli_stage_readiness")
        if "status=WAITING_FOR_QUALIFYING_EVENT" in detail:
            return "WAITING_FOR_QUALIFYING_EVENT"
    return PASS


def _stage_allowance(
    stage: str,
    *,
    status: str,
    results: tuple[GateResult, ...],
) -> tuple[bool, bool, bool, tuple[str, ...]]:
    if stage not in LIVE_RELEASE_STAGES:
        raise ValueError(f"UNSUPPORTED_LIVE_RELEASE_STAGE:{stage}")
    basis = (
        "schema_current",
        "loaded_sha",
        "source_fresh",
        "forecast_executable_bundle",
        "trade_state_clear",
        "redeem_state_clear",
    )
    if status != PASS:
        return False, False, False, basis
    if stage == "legacy_cron":
        return False, False, False, basis + ("legacy_cron_preservation",)
    if stage == "edli_submit_disabled_bridge":
        if _result_status(results, "edli_stage_readiness") != PASS:
            return False, False, False, basis + ("submit_disabled", "edli_stage_readiness_required")
        return False, False, False, basis + ("submit_disabled",)
    if stage == "edli_live_canary":
        if _result_status(results, "edli_stage_readiness") != PASS:
            return False, False, False, basis + ("canary_preflight_required", "tiny_cap_only")
        return True, True, False, basis + ("canary_preflight", "tiny_cap_only")
    if stage == "edli_live":
        if _result_status(results, "edli_stage_readiness") != PASS:
            return False, False, False, basis + ("edli_stage_readiness_required", "scaleout_blocked")
        if _result_status(results, "edli_promotion_artifact") != PASS:
            return False, False, False, basis + ("verified_promotion_artifact_required", "scaleout_blocked")
        return True, True, True, basis + ("verified_promotion_artifact", "scaleout_allowed")
    return False, False, False, basis


def _write_fixture_files(root: Path) -> argparse.Namespace:
    now = _utc_now()
    now_iso = now.isoformat()
    world_db = root / "zeus-world.db"
    forecasts_db = root / "zeus-forecasts.db"
    trade_db = root / "zeus_trades.db"

    # World DB
    conn = sqlite3.connect(str(world_db))
    init_schema(conn)
    # PR D0b (2026-05-27): init_schema sets `PRAGMA user_version = SCHEMA_VERSION`
    # but leaves an open transaction (in_transaction=True); without an explicit
    # commit the PRAGMA write is rolled back on close() and the fixture world.db
    # reopens with user_version=0, failing _check_world_schema. Production
    # callers commit explicitly (per the world-DB migration contract); the
    # fixture must too. Surfaced when SCHEMA_VERSION bumped 37→39.
    conn.commit()
    conn.close()

    # Forecasts DB — must exist with current schema and a LIVE_ELIGIBLE readiness row
    expires_iso = (now + timedelta(hours=24)).isoformat()
    fconn = sqlite3.connect(str(forecasts_db))
    init_schema_forecasts(fconn)
    fconn.execute(
        """
        INSERT OR IGNORE INTO readiness_state
            (readiness_id, scope_key, scope_type,
             city_id, city_timezone, target_local_date, temperature_metric,
             strategy_key, status, computed_at, expires_at, token_ids_json, reason_codes_json,
             dependency_json, provenance_json)
        VALUES
            ('fixture-readiness-1', 'fixture:city_metric:test-city:UTC:2026-06-01:high:v1',
             'city_metric', 'test-city', 'UTC', '2026-06-01', 'high',
             'producer_readiness_v1', 'LIVE_ELIGIBLE', ?, ?, '[]', '[]', '{}', '{}')
        """,
        (now_iso, expires_iso),
    )
    fconn.commit()
    fconn.close()

    # Trade DB
    tconn = sqlite3.connect(str(trade_db))
    tconn.executescript(
        """
        CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE settlement_commands (
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    tconn.commit()
    tconn.close()

    expected = _current_git_sha()
    loaded = root / "loaded_sha.json"
    source = root / "source_health.json"
    status = root / "status_summary.json"
    proof = root / "paper_proof.json"
    loaded.write_text(json.dumps({"loaded_sha": expected}))
    source.write_text(json.dumps({"generated_at": now_iso}))
    status.write_text(json.dumps({"generated_at": now_iso}))
    proof.write_text(json.dumps({
        "status": PASS,
        "live_eligibility": "UNKNOWN",
        **{key: True for key in PAPER_PROOF_KEYS},
    }))
    return parse_args([
        "--stage", "legacy_cron",
        "--expected-sha", expected,
        "--loaded-sha-file", str(loaded),
        "--world-db", str(world_db),
        "--forecasts-db", str(forecasts_db),
        "--trade-db", str(trade_db),
        "--source-health-json", str(source),
        "--status-json", str(status),
        "--paper-proof-json", str(proof),
        "--json",
    ])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=LIVE_RELEASE_STAGES, default="legacy_cron")
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--loaded-sha-file", type=Path)
    parser.add_argument("--world-db", type=Path, default=ROOT / "state" / "zeus-world.db")
    parser.add_argument("--forecasts-db", type=Path, default=ROOT / "state" / "zeus-forecasts.db")
    parser.add_argument("--trade-db", type=Path, default=ROOT / "state" / "zeus_trades.db")
    parser.add_argument("--source-health-json", type=Path, default=ROOT / "state" / "source_health.json")
    parser.add_argument("--status-json", type=Path, default=ROOT / "state" / "status_summary.json")
    parser.add_argument("--paper-proof-json", type=Path, default=ROOT / "state" / "paper_money_path_proof.json")
    parser.add_argument("--settings-json", type=Path)
    parser.add_argument("--canary-artifact-json", type=Path)
    parser.add_argument("--arm-artifact-json", type=Path, default=ROOT / "state" / "edli_arm_gate_artifact.json")
    parser.add_argument("--promotion-artifact-json", type=Path)
    parser.add_argument("--edli-live-min-canary-count", type=int, default=1)
    parser.add_argument("--edli-live-max-unresolved-unknowns", type=int, default=0)
    parser.add_argument("--edli-live-min-realized-edge-bps", type=float, default=0.0)
    parser.add_argument("--source-max-age-seconds", type=int, default=15 * 60)
    parser.add_argument("--status-max-age-seconds", type=int, default=15 * 60)
    parser.add_argument("--allow-redeem-command", action="append", default=[])
    parser.add_argument("--self-test-fixture", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    if args.self_test_fixture:
        with tempfile.TemporaryDirectory(prefix="zeus-release-gate-") as tmp:
            args = _write_fixture_files(Path(tmp))
            report = evaluate_release_gate(args)
    else:
        report = evaluate_release_gate(args)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"live_release_gate={report.status} stage={report.stage} "
            f"passed={report.passed_count}/{report.gate_count} "
            f"submit_allowed={report.submit_allowed} scaleout_allowed={report.scaleout_allowed}"
        )
        for result in report.results:
            print(f"{result.status} {result.name}: {result.detail}")
    return 0 if report.status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
