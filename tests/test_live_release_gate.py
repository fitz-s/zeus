# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P0-1/P1-2/P1-7

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.check_live_release_gate import PAPER_PROOF_KEYS, PASS, evaluate_release_gate, parse_args
from src.state.db import SCHEMA_VERSION, init_schema


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_world_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        init_schema(conn)
    finally:
        conn.close()


def _make_trade_db(path: Path, *, command_state: str = "ACKED", redeem_state: str = "REDEEM_CONFIRMED") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            """
        )
        conn.execute(
            "INSERT INTO venue_commands(command_id, state) VALUES (?, ?)",
            ("entry-1", command_state),
        )
        conn.execute(
            "INSERT INTO settlement_commands(command_id, state) VALUES (?, ?)",
            ("redeem-1", redeem_state),
        )
        conn.commit()
    finally:
        conn.close()


def _make_gate_args(tmp_path: Path, *, live_eligibility: str = "UNKNOWN", loaded_sha: str = "sha-a") -> object:
    world_db = tmp_path / "zeus-world.db"
    trade_db = tmp_path / "zeus_trades.db"
    loaded = tmp_path / "loaded_sha.json"
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    proof = tmp_path / "paper_proof.json"

    _make_world_db(world_db)
    _make_trade_db(trade_db)
    _write_json(loaded, {"loaded_sha": loaded_sha})
    _write_json(source, {"generated_at": "2026-05-21T12:00:00+00:00"})
    _write_json(status, {"generated_at": "2026-05-21T12:00:00+00:00"})
    _write_json(
        proof,
        {
            "status": PASS,
            "live_eligibility": live_eligibility,
            **{key: True for key in PAPER_PROOF_KEYS},
        },
    )
    return parse_args(
        [
            "--expected-sha",
            "sha-a",
            "--loaded-sha-file",
            str(loaded),
            "--world-db",
            str(world_db),
            "--trade-db",
            str(trade_db),
            "--source-health-json",
            str(source),
            "--status-json",
            str(status),
            "--paper-proof-json",
            str(proof),
            "--source-max-age-seconds",
            str(24 * 60 * 60),
            "--status-max-age-seconds",
            str(24 * 60 * 60),
        ]
    )


def test_release_gate_passes_only_as_read_only_evidence(tmp_path: Path) -> None:
    args = _make_gate_args(tmp_path)

    report = evaluate_release_gate(args)

    assert report.status == PASS
    assert report.passed_count == report.gate_count
    assert report.live_entries_allowed is False


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
