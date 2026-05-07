# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/task_2026-05-06_calibration_quality_blockers/PLAN.md Slice C
"""Backfill LOW contract-window evidence into recovery snapshot rows.

Default mode is a read-only dry run.  Apply mode is intentionally explicit:
``--apply --force`` is required before this script inserts rows.  The script
does not mutate old LOW rows.  It copies matched legacy LOW snapshots into a
new contract-window data_version and attaches the evidence needed by
``rebuild_calibration_pairs_v2.py``.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_grib_to_snapshots import _contract_evidence_fields
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
)
from src.contracts.tigge_snapshot_payload import ProvenanceViolation, TiggeSnapshotPayload
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import LOW_LOCALDAY_MIN

def _default_fifty_one_raw_root() -> Path:
    """Return the extracted JSON cache root in normal and linked worktrees."""
    candidates = [
        PROJECT_ROOT.parent / "51 source data" / "raw",
        PROJECT_ROOT.parents[1] / "workspace-venus" / "51 source data" / "raw",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


FIFTY_ONE_RAW_ROOT = _default_fifty_one_raw_root()

_ALLOWED_CAUSALITY_STATUSES = {
    "OK",
    "N/A_CAUSAL_DAY_ALREADY_STARTED",
    "N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON",
    "REJECTED_BOUNDARY_AMBIGUOUS",
    "RUNTIME_ONLY_FALLBACK",
    "UNKNOWN",
}


@dataclass(frozen=True, slots=True)
class LowRecoverySource:
    name: str
    json_subdir: str
    legacy_data_version: str
    recovery_data_version: str
    source_id: str


LOW_RECOVERY_SOURCES: dict[str, LowRecoverySource] = {
    "tigge_mars": LowRecoverySource(
        name="tigge_mars",
        json_subdir="tigge_ecmwf_ens_mn2t6_localday_min",
        legacy_data_version=LOW_LOCALDAY_MIN.data_version,
        recovery_data_version=TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
        source_id="tigge_mars",
    ),
    "ecmwf_open_data": LowRecoverySource(
        name="ecmwf_open_data",
        json_subdir="open_ens_mn2t6_localday_min",
        legacy_data_version=ECMWF_OPENDATA_LOW_DATA_VERSION,
        recovery_data_version=ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        source_id="ecmwf_open_data",
    ),
}


@dataclass
class BackfillStats:
    files_scanned: int = 0
    payload_rejected: int = 0
    unsupported_data_version: int = 0
    cycle_filtered: int = 0
    no_matching_snapshot: int = 0
    already_recovered: int = 0
    would_insert: int = 0
    inserted: int = 0
    training_candidates: int = 0
    blocked_candidates: int = 0
    by_attribution_status: dict[str, int] = field(default_factory=dict)
    by_block_reason: dict[str, int] = field(default_factory=dict)
    by_cycle: dict[str, int] = field(default_factory=dict)

    def add_status(self, status: str | None) -> None:
        key = status or "UNKNOWN"
        self.by_attribution_status[key] = self.by_attribution_status.get(key, 0) + 1

    def add_reasons(self, reasons: Iterable[str]) -> None:
        for reason in reasons:
            self.by_block_reason[reason] = self.by_block_reason.get(reason, 0) + 1

    def add_cycle(self, cycle: str | None) -> None:
        key = cycle or "UNKNOWN"
        self.by_cycle[key] = self.by_cycle.get(key, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "payload_rejected": self.payload_rejected,
            "unsupported_data_version": self.unsupported_data_version,
            "cycle_filtered": self.cycle_filtered,
            "no_matching_snapshot": self.no_matching_snapshot,
            "already_recovered": self.already_recovered,
            "would_insert": self.would_insert,
            "inserted": self.inserted,
            "training_candidates": self.training_candidates,
            "blocked_candidates": self.blocked_candidates,
            "by_attribution_status": dict(sorted(self.by_attribution_status.items())),
            "by_block_reason": dict(sorted(self.by_block_reason.items())),
            "by_cycle": dict(sorted(self.by_cycle.items())),
        }


def _connect(db_path: Path, *, dry_run: bool) -> sqlite3.Connection:
    if dry_run:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _slug(city: str) -> str:
    return city.lower().replace(" ", "-")


def _iter_json_paths(
    json_root: Path,
    source: LowRecoverySource,
    *,
    cities: set[str] | None,
) -> Iterable[Path]:
    root = json_root / source.json_subdir
    if not root.exists():
        return ()
    if not cities:
        return sorted(root.rglob("*.json"))
    paths: list[Path] = []
    wanted = {_slug(city) for city in cities}
    for city_slug in sorted(wanted):
        city_root = root / city_slug
        if city_root.exists():
            paths.extend(sorted(city_root.rglob("*.json")))
    return paths


def _parse_payload(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    snapshot = TiggeSnapshotPayload.from_json_dict(raw)
    snapshot.validate()  # fail-closed: raises ProvenanceViolation on contract violations
    return snapshot.to_json_dict()


def _date_in_scope(value: str, *, start_date: str | None, end_date: str | None) -> bool:
    if start_date and value < start_date:
        return False
    if end_date and value > end_date:
        return False
    return True


def _payload_cycle(payload: dict[str, Any]) -> str | None:
    issue_time = str(payload.get("issue_time_utc") or "")
    if len(issue_time) >= 13:
        cycle = issue_time[11:13]
        if cycle.isdigit():
            return cycle
    return None


def _row_exists(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    issue_time: str,
    data_version: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM ensemble_snapshots_v2
            WHERE city = ?
              AND target_date = ?
              AND temperature_metric = 'low'
              AND issue_time = ?
              AND data_version = ?
            LIMIT 1
            """,
            (city, target_date, issue_time, data_version),
        ).fetchone()
        is not None
    )


def _source_snapshot_row(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    issue_time: str,
    data_version: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM ensemble_snapshots_v2
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = 'low'
          AND issue_time = ?
          AND data_version = ?
        LIMIT 1
        """,
        (city, target_date, issue_time, data_version),
    ).fetchone()


def _json_list(value: object | None) -> list[str]:
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return ["invalid_forecast_window_block_reasons_json"]
    if not isinstance(parsed, list):
        return ["invalid_forecast_window_block_reasons_json"]
    return [str(item) for item in parsed if str(item).strip()]


def _payload_causality_status(payload: dict[str, Any]) -> str:
    causality = payload.get("causality") if isinstance(payload.get("causality"), dict) else {}
    status = str(causality.get("status") or "UNKNOWN")
    return status if status in _ALLOWED_CAUSALITY_STATUSES else "UNKNOWN"


def _recovery_training_allowed(
    *,
    evidence: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    reasons = _json_list(evidence.get("forecast_window_block_reasons_json"))
    return (
        evidence.get("forecast_window_attribution_status") == "FULLY_INSIDE_TARGET_LOCAL_DAY"
        and int(evidence.get("contributes_to_target_extrema") or 0) == 1
        and not reasons
        and _payload_causality_status(payload) == "OK"
    )


def _recovery_causality_status(
    *,
    evidence: dict[str, Any],
    payload: dict[str, Any],
    training_allowed: bool,
) -> str:
    if training_allowed:
        return "OK"
    payload_status = _payload_causality_status(payload)
    if payload_status != "OK":
        return payload_status
    status = str(evidence.get("forecast_window_attribution_status") or "UNKNOWN")
    if status == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY":
        return "REJECTED_BOUNDARY_AMBIGUOUS"
    reasons = _json_list(evidence.get("forecast_window_block_reasons_json"))
    if any("issued_after_relevant_window" in reason for reason in reasons):
        return "N/A_CAUSAL_DAY_ALREADY_STARTED"
    return "UNKNOWN"


def _recovery_boundary_ambiguous(*, evidence: dict[str, Any], payload: dict[str, Any]) -> bool:
    boundary_policy = payload.get("boundary_policy")
    if isinstance(boundary_policy, dict) and bool(boundary_policy.get("boundary_ambiguous")):
        return True
    if bool(payload.get("boundary_ambiguous")):
        return True
    return str(evidence.get("forecast_window_attribution_status") or "") == (
        "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    )


def _merge_provenance(
    source_row: sqlite3.Row,
    *,
    source: LowRecoverySource,
    evidence: dict[str, Any],
    payload_path: Path,
) -> str:
    raw = source_row["provenance_json"] if "provenance_json" in source_row.keys() else None
    try:
        provenance = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        provenance = {"legacy_provenance_parse_error": True}
    provenance["low_contract_window_backfill"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_family": source.name,
        "legacy_data_version": source.legacy_data_version,
        "recovery_data_version": source.recovery_data_version,
        "json_path": str(payload_path),
        "contract_outcome_evidence": evidence,
        "live_promotion_authorized": False,
    }
    return json.dumps(provenance, ensure_ascii=False, sort_keys=True)


def _build_recovery_row(
    conn: sqlite3.Connection,
    source_row: sqlite3.Row,
    *,
    source: LowRecoverySource,
    payload: dict[str, Any],
    payload_path: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(ensemble_snapshots_v2)")]
    source_keys = set(source_row.keys())
    row = {
        column: (source_row[column] if column in source_keys else None)
        for column in columns
        if column not in {"snapshot_id", "recorded_at"}
    }
    training_allowed = _recovery_training_allowed(evidence=evidence, payload=payload)
    causality_status = _recovery_causality_status(
        evidence=evidence,
        payload=payload,
        training_allowed=training_allowed,
    )
    boundary_ambiguous = _recovery_boundary_ambiguous(evidence=evidence, payload=payload)
    row.update({
        "data_version": source.recovery_data_version,
        "observation_field": LOW_LOCALDAY_MIN.observation_field,
        "physical_quantity": LOW_LOCALDAY_MIN.physical_quantity,
        "source_id": row.get("source_id") or source.source_id,
        "unit": row.get("unit") or payload.get("unit"),
        "training_allowed": 1 if training_allowed else 0,
        "causality_status": causality_status,
        "boundary_ambiguous": 1 if boundary_ambiguous else 0,
        "ambiguous_member_count": int(
            payload["boundary_policy"].get("ambiguous_member_count", 0)
            if isinstance(payload.get("boundary_policy"), dict)
            else 0
        ),
        "provenance_json": _merge_provenance(
            source_row,
            source=source,
            evidence=evidence,
            payload_path=payload_path,
        ),
    })
    for key, value in evidence.items():
        if key in row:
            row[key] = value
    return row


def _insert_recovery_row(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    columns = list(row.keys())
    placeholders = ", ".join(f":{column}" for column in columns)
    sql = (
        f"INSERT OR IGNORE INTO ensemble_snapshots_v2 "
        f"({', '.join(columns)}) VALUES ({placeholders})"
    )
    before = conn.total_changes
    conn.execute(sql, row)
    return conn.total_changes - before


def process_payload(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    source: LowRecoverySource,
    path: Path,
    dry_run: bool,
    stats: BackfillStats,
) -> None:
    if payload.get("data_version") != source.legacy_data_version:
        stats.unsupported_data_version += 1
        return

    city = str(payload.get("city") or "")
    target_date = str(payload.get("target_date_local") or "")
    issue_time = str(payload.get("issue_time_utc") or "")
    if _row_exists(
        conn,
        city=city,
        target_date=target_date,
        issue_time=issue_time,
        data_version=source.recovery_data_version,
    ):
        stats.already_recovered += 1
        return
    source_row = _source_snapshot_row(
        conn,
        city=city,
        target_date=target_date,
        issue_time=issue_time,
        data_version=source.legacy_data_version,
    )
    if source_row is None:
        stats.no_matching_snapshot += 1
        return

    evidence_payload = dict(payload)
    evidence_payload["data_version"] = source.recovery_data_version
    evidence = _contract_evidence_fields(
        evidence_payload,
        LOW_LOCALDAY_MIN,
        source_id=source.source_id,
    )
    stats.add_status(str(evidence.get("forecast_window_attribution_status") or "UNKNOWN"))
    block_reasons = _json_list(evidence.get("forecast_window_block_reasons_json"))
    stats.add_reasons(block_reasons)
    training_allowed = _recovery_training_allowed(evidence=evidence, payload=payload)
    if training_allowed:
        stats.training_candidates += 1
    else:
        stats.blocked_candidates += 1
    stats.would_insert += 1

    if dry_run:
        return

    row = _build_recovery_row(
        conn,
        source_row,
        source=source,
        payload=payload,
        payload_path=path,
        evidence=evidence,
    )
    stats.inserted += _insert_recovery_row(conn, row)


def run_backfill(
    *,
    conn: sqlite3.Connection,
    json_root: Path,
    sources: Iterable[LowRecoverySource],
    dry_run: bool,
    force: bool,
    cities: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cycle: str | None = None,
    limit: int | None = None,
) -> dict[str, BackfillStats]:
    if not dry_run and not force:
        raise RuntimeError("--apply requires --force")
    per_source: dict[str, BackfillStats] = {}
    for source in sources:
        stats = BackfillStats()
        per_source[source.name] = stats
        for path in _iter_json_paths(json_root, source, cities=cities):
            if limit is not None and stats.files_scanned >= limit:
                break
            stats.files_scanned += 1
            try:
                payload = _parse_payload(path)
            except (OSError, json.JSONDecodeError, ProvenanceViolation, ValueError):
                stats.payload_rejected += 1
                continue
            target_date = str(payload.get("target_date_local") or "")
            if not _date_in_scope(target_date, start_date=start_date, end_date=end_date):
                continue
            payload_cycle = _payload_cycle(payload)
            stats.add_cycle(payload_cycle)
            if cycle is not None and payload_cycle != cycle:
                stats.cycle_filtered += 1
                continue
            process_payload(
                conn,
                payload,
                source=source,
                path=path,
                dry_run=dry_run,
                stats=stats,
            )
    return per_source


def _select_sources(source_family: str) -> list[LowRecoverySource]:
    if source_family == "all":
        return list(LOW_RECOVERY_SOURCES.values())
    return [LOW_RECOVERY_SOURCES[source_family]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill LOW contract-window evidence recovery snapshot rows.",
    )
    parser.add_argument("--db-path", default=str(PROJECT_ROOT / "state" / "zeus-world.db"))
    parser.add_argument("--json-root", default=str(FIFTY_ONE_RAW_ROOT))
    parser.add_argument(
        "--source-family",
        choices=("tigge_mars", "ecmwf_open_data", "all"),
        default="all",
    )
    parser.add_argument("--city", action="append", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--cycle", default=None, help="Limit to one issue UTC cycle, e.g. 00 or 12.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--force", action="store_true", default=False)
    args = parser.parse_args()

    dry_run = not args.apply
    _db_path = Path(args.db_path)
    _lock_ctx = db_writer_lock(_db_path, WriteClass.BULK) if not dry_run else None
    if _lock_ctx is not None:
        _lock_ctx.__enter__()
    try:
        try:
            conn = _connect(_db_path, dry_run=dry_run)
        except sqlite3.OperationalError as exc:
            print(f"ERROR: sqlite3.OperationalError: {exc}", file=sys.stderr)
            return 1

        try:
            if not dry_run:
                apply_v2_schema(conn)
            per_source = run_backfill(
                conn=conn,
                json_root=Path(args.json_root),
                sources=_select_sources(args.source_family),
                dry_run=dry_run,
                force=args.force,
                cities=set(args.city) if args.city else None,
                start_date=args.start_date,
                end_date=args.end_date,
                cycle=args.cycle,
                limit=args.limit,
            )
            if not dry_run:
                conn.commit()
        except Exception as exc:
            if not dry_run:
                conn.rollback()
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        finally:
            conn.close()
    finally:
        if _lock_ctx is not None:
            _lock_ctx.__exit__(None, None, None)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "live_behavior_changed": False,
        "old_rows_mutated": False,
        "apply_requires_force": True,
        "source_reports": {
            source: stats.as_dict()
            for source, stats in per_source.items()
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
