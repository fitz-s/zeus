#!/usr/bin/env python3
# Lifecycle: created=2026-06-17; last_reviewed=2026-06-17; last_reused=never
# Purpose: Dry-run/apply repair for replacement forecast posterior live-authority schema/status.
# Reuse: Run dry-run before an operator-approved zeus-forecasts.db live-authority repair.
# Authority basis: docs/operations/current/plans/live_redecision_repair/PLAN.md Slice G.
"""Repair replacement forecast live-authority schema/status.

Default mode is read-only dry-run. ``--apply`` is required for any DB write.
The repair is deliberately narrow:

1. Migrate ``forecast_posteriors.trade_authority_status`` CHECK so
   ``LIVE_AUTHORITY`` is representable.
2. Re-stamp only existing replacement posterior rows that already carry the
   fused-Normal point q plus certified bootstrap q_lcb/q_ucb evidence required
   by the live reader and whose runtime policy flags are currently live.

It does not materialize forecasts, submit orders, restart daemons, or turn a
diagnostic/fallback posterior into live authority by assumption.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_materializer import (  # noqa: E402
    REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
    REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL,
    _QLCB_BASIS,
    _ensure_forecast_posteriors_live_authority_check,
)
from src.data.replacement_forecast_readiness import PRODUCT_ID, STRATEGY_KEY  # noqa: E402
from src.data.replacement_forecast_runtime_policy import (  # noqa: E402
    DIAGNOSTIC_ONLY_STATUS,
    LIVE_AUTHORITY_STATUS,
    REQUIRED_FLAGS,
    resolve_replacement_forecast_runtime_policy,
)
from src.state.db import ZEUS_FORECASTS_DB_PATH  # noqa: E402
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402


LIVE_Q_MODES = {REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL, REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL}
REPAIR_MARKER = "repair_replacement_live_authority_2026_06_17"
LEGACY_STATUSES = {"SHADOW_ONLY", "SHADOW_VETO_ONLY"}
REPAIR_INPUT_STATUSES = {*LEGACY_STATUSES, DIAGNOSTIC_ONLY_STATUS}


@dataclass(frozen=True)
class RowEligibility:
    posterior_id: int
    eligible: bool
    reason_codes: tuple[str, ...]
    computed_at: str | None


@dataclass(frozen=True)
class RepairReport:
    status: str
    apply: bool
    db_path: str
    generated_at: str
    runtime_policy_status: str
    runtime_policy_reasons: tuple[str, ...]
    schema_has_live_authority: bool
    would_migrate_schema: bool
    eligible_rows: int
    latest_eligible_computed_at: str | None
    updated_rows: int
    updated_readiness_rows: int
    blocked_reasons: tuple[str, ...]


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_readwrite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _schema_has_live_authority(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'forecast_posteriors'"
    ).fetchone()
    if row is None:
        return False
    return LIVE_AUTHORITY_STATUS in str(row["sql"])


def _runtime_policy_status() -> tuple[str, tuple[str, ...]]:
    try:
        from src.config import settings  # noqa: PLC0415

        feature_flags = settings["feature_flags"]
        flags = {key: bool(feature_flags.get(key, False)) for key in REQUIRED_FLAGS}
        policy = resolve_replacement_forecast_runtime_policy(
            flags,
            promotion_evidence=None,
            capital_objective_evidence=None,
        )
        return policy.status, tuple(policy.reason_codes)
    except Exception as exc:  # pragma: no cover - defensive operator path
        return "POLICY_READ_FAILED", (f"REPLACEMENT_RUNTIME_POLICY_READ_FAILED:{type(exc).__name__}",)


def _load_json_object(raw: str | None, *, field: str) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None or not str(raw).strip():
        return None, f"{field}_MISSING"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"{field}_INVALID_JSON"
    if not isinstance(parsed, dict):
        return None, f"{field}_NOT_OBJECT"
    return parsed, None


def _probability_map(value: Mapping[str, Any] | None, *, field: str) -> tuple[dict[str, float] | None, str | None]:
    if not value:
        return None, f"{field}_EMPTY"
    out: dict[str, float] = {}
    for key, raw in value.items():
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            return None, f"{field}_NON_NUMERIC"
        if not math.isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
            return None, f"{field}_OUT_OF_RANGE"
        out[str(key)] = parsed
    return out, None


def _row_eligibility(row: sqlite3.Row) -> RowEligibility:
    reasons: list[str] = []
    posterior_id = int(row["posterior_id"])
    computed_at = str(row["computed_at"]) if row["computed_at"] is not None else None

    if str(row["product_id"] or "") != PRODUCT_ID:
        reasons.append("PRODUCT_ID_MISMATCH")
    if str(row["trade_authority_status"] or "") not in REPAIR_INPUT_STATUSES:
        reasons.append("TRADE_AUTHORITY_STATUS_NOT_REPAIR_INPUT")

    q_raw, q_error = _load_json_object(row["q_json"], field="q_json")
    lcb_raw, lcb_error = _load_json_object(row["q_lcb_json"], field="q_lcb_json")
    ucb_raw, ucb_error = _load_json_object(row["q_ucb_json"], field="q_ucb_json")
    provenance, provenance_error = _load_json_object(row["provenance_json"], field="provenance_json")
    for error in (q_error, lcb_error, ucb_error, provenance_error):
        if error:
            reasons.append(error)

    q_map, q_map_error = _probability_map(q_raw, field="q_json")
    lcb_map, lcb_map_error = _probability_map(lcb_raw, field="q_lcb_json")
    ucb_map, ucb_map_error = _probability_map(ucb_raw, field="q_ucb_json")
    for error in (q_map_error, lcb_map_error, ucb_map_error):
        if error:
            reasons.append(error)

    if provenance is not None:
        if provenance.get("replacement_q_mode") not in LIVE_Q_MODES:
            reasons.append("REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE")
        if provenance.get("q_lcb_basis") != _QLCB_BASIS:
            reasons.append("QLCB_BASIS_NOT_CERTIFIED_BOOTSTRAP")

    if q_map and lcb_map and ucb_map:
        keys = set(q_map)
        if set(lcb_map) != keys or set(ucb_map) != keys:
            reasons.append("PROBABILITY_BOUND_KEYS_MISMATCH")
        q_sum = sum(q_map.values())
        if abs(q_sum - 1.0) > 1e-6:
            reasons.append("Q_JSON_DOES_NOT_SUM_TO_ONE")
        for key in keys:
            if lcb_map[key] > q_map[key] + 1e-12:
                reasons.append("QLCB_EXCEEDS_Q_POINT")
                break
            if ucb_map[key] + 1e-12 < q_map[key]:
                reasons.append("QUCB_BELOW_Q_POINT")
                break

    return RowEligibility(
        posterior_id=posterior_id,
        eligible=not reasons,
        reason_codes=tuple(dict.fromkeys(reasons)),
        computed_at=computed_at,
    )


def _candidate_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                posterior_id,
                product_id,
                trade_authority_status,
                computed_at,
                q_json,
                q_lcb_json,
                q_ucb_json,
                provenance_json
            FROM forecast_posteriors
            WHERE product_id = ?
              AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY', 'DIAGNOSTIC_ONLY')
            ORDER BY computed_at DESC, posterior_id DESC
            """,
            (PRODUCT_ID,),
        ).fetchall()
    )


def evaluate_connection(conn: sqlite3.Connection, *, db_path: Path, apply: bool, updated_rows: int = 0) -> RepairReport:
    schema_has_live = _schema_has_live_authority(conn)
    policy_status, policy_reasons = _runtime_policy_status()
    eligibilities = [_row_eligibility(row) for row in _candidate_rows(conn)]
    eligible = [item for item in eligibilities if item.eligible]
    blocked: list[str] = []
    if policy_status != LIVE_AUTHORITY_STATUS:
        blocked.append("RUNTIME_POLICY_NOT_LIVE_AUTHORITY")

    return RepairReport(
        status="READY" if not blocked else "BLOCKED",
        apply=apply,
        db_path=str(db_path),
        generated_at=_utc_now_text(),
        runtime_policy_status=policy_status,
        runtime_policy_reasons=policy_reasons,
        schema_has_live_authority=schema_has_live,
        would_migrate_schema=not schema_has_live,
        eligible_rows=len(eligible),
        latest_eligible_computed_at=max((item.computed_at or "" for item in eligible), default=None) or None,
        updated_rows=updated_rows,
        updated_readiness_rows=0,
        blocked_reasons=tuple(blocked),
    )


def _updated_provenance(raw: str) -> str:
    provenance = json.loads(raw)
    if not isinstance(provenance, dict):
        raise ValueError("provenance_json must be an object")
    provenance["posterior_authority_status"] = LIVE_AUTHORITY_STATUS
    provenance["runtime_policy_status"] = LIVE_AUTHORITY_STATUS
    provenance["trade_authority_status"] = LIVE_AUTHORITY_STATUS
    provenance[REPAIR_MARKER] = {
        "applied_at": _utc_now_text(),
        "basis": "row_already_carries_fused_normal_point_q_and_certified_bootstrap_bounds",
    }
    return json.dumps(provenance, sort_keys=True, separators=(",", ":"))


def _updated_readiness_provenance(raw: str, *, readiness_status: str) -> str:
    provenance = json.loads(raw)
    if not isinstance(provenance, dict):
        raise ValueError("readiness provenance_json must be an object")
    authority_status = LIVE_AUTHORITY_STATUS if readiness_status == "READY" else DIAGNOSTIC_ONLY_STATUS
    for key in ("posterior_authority_status", "runtime_policy_status", "trade_authority_status"):
        if provenance.get(key) in LEGACY_STATUSES or key == "trade_authority_status":
            provenance[key] = authority_status
    provenance[REPAIR_MARKER] = {
        "applied_at": _utc_now_text(),
        "basis": "readiness_provenance_status_migrated_to_live_or_diagnostic_authority",
    }
    return json.dumps(provenance, sort_keys=True, separators=(",", ":"))


def apply_connection(conn: sqlite3.Connection, *, db_path: Path) -> RepairReport:
    before = evaluate_connection(conn, db_path=db_path, apply=True)
    if before.status != "READY":
        return before

    eligible_ids = {
        item.posterior_id
        for item in (_row_eligibility(row) for row in _candidate_rows(conn))
        if item.eligible
    }
    with conn:
        _ensure_forecast_posteriors_live_authority_check(conn)
        updated_rows = 0
        rows = conn.execute(
            """
            SELECT posterior_id, trade_authority_status, provenance_json
            FROM forecast_posteriors
            WHERE product_id = ?
              AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY', 'DIAGNOSTIC_ONLY')
            """,
            (PRODUCT_ID,),
        ).fetchall()
        for row in rows:
            posterior_id = int(row["posterior_id"])
            if posterior_id not in eligible_ids:
                continue
            cursor = conn.execute(
                """
                UPDATE forecast_posteriors
                SET trade_authority_status = ?,
                    provenance_json = ?
                WHERE posterior_id = ?
                  AND trade_authority_status = ?
                """,
                (
                    LIVE_AUTHORITY_STATUS,
                    _updated_provenance(str(row["provenance_json"])),
                    posterior_id,
                    str(row["trade_authority_status"]),
                ),
            )
            updated_rows += int(cursor.rowcount or 0)
        conn.execute(
            """
            UPDATE forecast_posteriors
            SET trade_authority_status = ?
            WHERE product_id = ?
              AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
            """,
            (DIAGNOSTIC_ONLY_STATUS, PRODUCT_ID),
        )
        updated_readiness_rows = 0
        readiness_rows = conn.execute(
            """
            SELECT rowid, status, provenance_json
            FROM readiness_state
            WHERE strategy_key = ?
              AND json_extract(provenance_json, '$.trade_authority_status') IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
            """,
            (STRATEGY_KEY,),
        ).fetchall()
        for row in readiness_rows:
            cursor = conn.execute(
                """
                UPDATE readiness_state
                SET provenance_json = ?
                WHERE rowid = ?
                """,
                (
                    _updated_readiness_provenance(
                        str(row["provenance_json"]),
                        readiness_status=str(row["status"] or ""),
                    ),
                    int(row["rowid"]),
                ),
            )
            updated_readiness_rows += int(cursor.rowcount or 0)

    after = evaluate_connection(conn, db_path=db_path, apply=True, updated_rows=updated_rows)
    return replace(after, updated_readiness_rows=updated_readiness_rows)


def run(db_path: Path, *, apply: bool) -> RepairReport:
    if apply:
        with db_writer_lock(db_path, WriteClass.BULK):
            conn = _connect_readwrite(db_path)
            try:
                return apply_connection(conn, db_path=db_path)
            finally:
                conn.close()
    conn = _connect_readonly(db_path)
    try:
        if apply:
            return apply_connection(conn, db_path=db_path)
        return evaluate_connection(conn, db_path=db_path, apply=False)
    finally:
        conn.close()


def _print_report(report: RepairReport, *, json_output: bool) -> None:
    payload = asdict(report)
    if json_output:
        print(json.dumps(payload, sort_keys=True, indent=2))
        return
    print(f"status={report.status}")
    print(f"apply={report.apply}")
    print(f"db_path={report.db_path}")
    print(f"runtime_policy_status={report.runtime_policy_status}")
    print(f"schema_has_live_authority={report.schema_has_live_authority}")
    print(f"would_migrate_schema={report.would_migrate_schema}")
    print(f"eligible_rows={report.eligible_rows}")
    print(f"latest_eligible_computed_at={report.latest_eligible_computed_at}")
    print(f"updated_rows={report.updated_rows}")
    print(f"updated_readiness_rows={report.updated_readiness_rows}")
    if report.blocked_reasons:
        print(f"blocked_reasons={','.join(report.blocked_reasons)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ZEUS_FORECASTS_DB_PATH)
    parser.add_argument("--apply", action="store_true", help="Write schema/status repair. Default is dry-run.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = parser.parse_args()

    report = run(args.db, apply=args.apply)
    _print_report(report, json_output=args.json)
    if report.status != "READY":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
