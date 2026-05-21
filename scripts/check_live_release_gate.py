#!/usr/bin/env python3
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Read-only release gate proving loaded SHA, schema, order/redeem state,
#   freshness, and paper proof before any live-money enablement claim.
# Reuse: Run before live-release claims or when touching money-path release gates.
# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P0-1/P1-2/P1-7
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import SCHEMA_VERSION, init_schema
from src.state.no_trade_events import (
    NoTradeEventsSchemaCompatibilityError,
    assert_no_trade_events_schema_current_for_live,
)

PASS = "PASS"
FAIL = "FAIL"

UNKNOWN_COMMAND_STATES = (
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "UNKNOWN",
    "REVIEW_REQUIRED",
)
BLOCKING_REDEEM_STATES = (
    "REDEEM_OPERATOR_REQUIRED",
    "REDEEM_REVIEW_REQUIRED",
)
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
    gate_count: int
    passed_count: int
    live_entries_allowed: bool
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
            assert_no_trade_events_schema_current_for_live(
                conn,
                expected_schema_version=SCHEMA_VERSION,
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


def _check_redeem_state(trade_db: Path, allow_redeem_command: tuple[str, ...]) -> GateResult:
    if not trade_db.exists():
        return GateResult("redeem_state", FAIL, f"missing:{trade_db}")
    conn = sqlite3.connect(str(trade_db))
    try:
        if not _table_exists(conn, "settlement_commands"):
            return GateResult("redeem_state", FAIL, "missing_table:settlement_commands")
        allow = set(allow_redeem_command)
        placeholders = ",".join("?" for _ in BLOCKING_REDEEM_STATES)
        rows = conn.execute(
            f"""
            SELECT command_id, state
            FROM settlement_commands
            WHERE state IN ({placeholders})
            """,
            BLOCKING_REDEEM_STATES,
        ).fetchall()
        blocking = [(str(row[0]), str(row[1])) for row in rows if str(row[0]) not in allow]
        if blocking:
            return GateResult("redeem_state", FAIL, f"blocking_redeem_rows={blocking[:5]}")
        return GateResult("redeem_state", PASS, "no_unwhitelisted_blocking_redeem_rows")
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
        _check_trade_state(args.trade_db),
        _check_redeem_state(args.trade_db, tuple(args.allow_redeem_command or ())),
        _check_fresh_file("source_health", args.source_health_json, max_age_seconds=args.source_max_age_seconds, now=now),
        _check_fresh_file("status_summary", args.status_json, max_age_seconds=args.status_max_age_seconds, now=now),
        _check_paper_proof(args.paper_proof_json),
    ]
    passed = sum(1 for result in results if result.status == PASS)
    status = PASS if passed == len(results) else FAIL
    return ReleaseGateReport(
        status=status,
        gate_count=len(results),
        passed_count=passed,
        live_entries_allowed=False,
        results=tuple(results),
    )


def _write_fixture_files(root: Path) -> argparse.Namespace:
    now = _utc_now().isoformat()
    world_db = root / "zeus-world.db"
    trade_db = root / "zeus_trades.db"
    conn = sqlite3.connect(str(world_db))
    init_schema(conn)
    conn.close()
    tconn = sqlite3.connect(str(trade_db))
    tconn.executescript(
        """
        CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, state TEXT NOT NULL);
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
    source.write_text(json.dumps({"generated_at": now}))
    status.write_text(json.dumps({"generated_at": now}))
    proof.write_text(json.dumps({
        "status": PASS,
        "live_eligibility": "UNKNOWN",
        **{key: True for key in PAPER_PROOF_KEYS},
    }))
    return parse_args([
        "--expected-sha", expected,
        "--loaded-sha-file", str(loaded),
        "--world-db", str(world_db),
        "--trade-db", str(trade_db),
        "--source-health-json", str(source),
        "--status-json", str(status),
        "--paper-proof-json", str(proof),
        "--json",
    ])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--loaded-sha-file", type=Path)
    parser.add_argument("--world-db", type=Path, default=ROOT / "state" / "zeus-world.db")
    parser.add_argument("--trade-db", type=Path, default=ROOT / "state" / "zeus_trades.db")
    parser.add_argument("--source-health-json", type=Path, default=ROOT / "state" / "source_health.json")
    parser.add_argument("--status-json", type=Path, default=ROOT / "state" / "status_summary.json")
    parser.add_argument("--paper-proof-json", type=Path, default=ROOT / "state" / "paper_money_path_proof.json")
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
        print(f"live_release_gate={report.status} passed={report.passed_count}/{report.gate_count}")
        for result in report.results:
            print(f"{result.status} {result.name}: {result.detail}")
    return 0 if report.status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
