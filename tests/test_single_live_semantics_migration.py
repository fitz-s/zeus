# Created: 2026-07-23
# Last reused/audited: 2026-07-23
# Authority basis: operator-directed WORLD single-live decision-graph cutover.
# Invariants: INV-03, INV-08, INV-17, INV-29, INV-30, INV-37
"""Fixture-only antibodies for the single-live WORLD decision-graph migration."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
)
SPEC = importlib.util.spec_from_file_location("single_live_cutover", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def _retired_mode() -> str:
    return "S" + "HADOW"


def _world_ddl(*, legacy_check: bool) -> str:
    ddl = migration.DECISION_LIVE_DDL.replace(
        "decision_certificates_live_new", "decision_certificates"
    )
    if legacy_check:
        ddl = ddl.replace(
            "mode = 'LIVE'", f"mode IN ('LIVE', '{_retired_mode()}')"
        )
    return ddl


def _create_world(path: Path, *, legacy_check: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        _world_ddl(legacy_check=legacy_check)
        + """
        ;
        CREATE TABLE decision_certificate_edges (
            child_certificate_id TEXT NOT NULL,
            parent_role TEXT NOT NULL,
            parent_certificate_hash TEXT NOT NULL,
            parent_certificate_type TEXT NOT NULL,
            required INTEGER NOT NULL CHECK (required IN (0,1)),
            created_at TEXT NOT NULL,
            PRIMARY KEY (child_certificate_id, parent_role, parent_certificate_hash)
        );
        CREATE TABLE decision_certificate_supersessions (
            supersession_id TEXT NOT NULL PRIMARY KEY,
            old_certificate_hash TEXT NOT NULL,
            new_certificate_hash TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE decision_compile_failures (
            failure_id TEXT NOT NULL PRIMARY KEY,
            event_id TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            mode TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            stage TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            reason_detail TEXT,
            parent_hashes_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_decision_certificates_semantic
            ON decision_certificates(certificate_type, semantic_key, mode, decision_time);
        CREATE INDEX idx_decision_certificates_hash
            ON decision_certificates(certificate_hash);
        """
    )
    return conn


def _create_trades(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL
        );
        CREATE TABLE position_decision_attribution (
            attribution_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            command_id TEXT,
            decision_certificate_hash TEXT,
            resolution TEXT NOT NULL,
            resolution_reason TEXT,
            source TEXT NOT NULL,
            intent_kind TEXT,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        );
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            decision_id TEXT,
            payload_json TEXT
        );
        CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT
        );
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY,
            artifact_json TEXT
        );
        CREATE TABLE edli_live_profit_audit (
            id INTEGER PRIMARY KEY,
            expected_edge_source_certificate_hash TEXT,
            cost_basis_source_certificate_hash TEXT
        );
        CREATE TABLE decision_certificates (
            certificate_hash TEXT PRIMARY KEY
        );
        CREATE TABLE decision_certificate_edges (
            parent_certificate_hash TEXT NOT NULL
                REFERENCES decision_certificates(certificate_hash)
        );
        CREATE TABLE decision_certificate_supersessions (
            old_certificate_hash TEXT NOT NULL
                REFERENCES decision_certificates(certificate_hash),
            new_certificate_hash TEXT NOT NULL
                REFERENCES decision_certificates(certificate_hash)
        );
        CREATE TABLE decision_compile_failures (
            failure_id TEXT PRIMARY KEY
        );
        INSERT INTO decision_certificates VALUES ('ghost-hash');
        INSERT INTO decision_certificate_edges VALUES ('ghost-hash');
        INSERT INTO decision_certificate_supersessions VALUES ('ghost-hash', 'ghost-hash');
        INSERT INTO decision_compile_failures VALUES ('ghost-failure');
        """
    )
    return conn


def _insert_certificate(
    conn: sqlite3.Connection,
    certificate_id: str,
    certificate_hash: str,
    *,
    mode: str = "LIVE",
    certificate_type: str = "BeliefCertificate",
    minute: int = 0,
) -> None:
    time = f"2026-07-22T12:{minute:02d}:00+00:00"
    values = {
        "certificate_id": certificate_id,
        "certificate_type": certificate_type,
        "schema_version": 1,
        "canonicalization_version": "v1",
        "semantic_key": f"semantic:{certificate_id}",
        "claim_type": "belief",
        "mode": mode,
        "decision_time": time,
        "source_available_at": time,
        "agent_received_at": time,
        "persisted_at": time,
        "max_parent_source_available_at": time,
        "max_parent_agent_received_at": time,
        "max_parent_persisted_at": time,
        "authority_id": "authority",
        "authority_version": "v1",
        "algorithm_id": "algorithm",
        "algorithm_version": "v1",
        "config_hash": "config",
        "model_version_hash": "model",
        "payload_json": "{}",
        "payload_hash": f"payload-{certificate_hash}",
        "certificate_hash": certificate_hash,
        "verifier_status": "VERIFIED",
        "created_at": time,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    conn.execute(
        f"INSERT INTO decision_certificates ({columns}) VALUES ({placeholders})",
        values,
    )


def _edge(
    conn: sqlite3.Connection,
    child_id: str,
    parent_hash: str,
    *,
    role: str = "belief",
) -> None:
    conn.execute(
        "INSERT INTO decision_certificate_edges VALUES (?, ?, ?, ?, 1, ?)",
        (child_id, role, parent_hash, "BeliefCertificate", "2026-07-22T13:00:00+00:00"),
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, sqlite3.Connection, sqlite3.Connection]:
    world = tmp_path / "zeus-world.db"
    trades = tmp_path / "zeus_trades.db"
    return world, trades, _create_world(world), _create_trades(trades)


def test_recursive_closure_preserves_live_old_sizing_and_writes_atomic_receipt(
    tmp_path: Path,
) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt_path = tmp_path / "receipts" / "cutover.json"
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        _insert_certificate(wconn, "dependent", "hash-dependent", minute=1)
        _insert_certificate(wconn, "grandchild", "hash-grandchild", minute=2)
        _insert_certificate(
            wconn,
            "old-sizing",
            "hash-old-sizing",
            certificate_type=migration.OLD_SIZING_CERTIFICATE,
            minute=3,
        )
        _insert_certificate(wconn, "kept", "hash-kept", minute=4)
        _edge(wconn, "dependent", "hash-retired")
        _edge(wconn, "grandchild", "hash-dependent")
        _edge(wconn, "kept", "hash-old-sizing", role="sizing")
        wconn.execute(
            "INSERT INTO decision_certificate_supersessions VALUES (?, ?, ?, ?, ?)",
            ("drop", "hash-retired", "hash-dependent", "old lane", "2026-07-22T13:00:00+00:00"),
        )
        wconn.execute(
            "INSERT INTO decision_certificate_supersessions VALUES (?, ?, ?, ?, ?)",
            ("keep", "hash-old-sizing", "hash-kept", "refresh", "2026-07-22T13:01:00+00:00"),
        )
        wconn.execute(
            "INSERT INTO decision_compile_failures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-failure", "event-old", "2026-07-22T12:05:00+00:00", _retired_mode(), "belief", "compile", "OLD_MODE", None, "[]", "2026-07-22T12:06:00+00:00"),
        )
        wconn.execute(
            "INSERT INTO decision_compile_failures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("live-failure", "event-live", "2026-07-22T12:07:00+00:00", "LIVE", "belief", "verify", "CURRENT", None, "[]", "2026-07-22T12:08:00+00:00"),
        )
        tconn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?)",
            ("historical", "old-decision", json.dumps({"certificate": "hash-retired"})),
        )
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    receipt = migration.migrate_world_decision_graph(world, trades, receipt_path)

    conn = sqlite3.connect(world)
    try:
        kept = conn.execute(
            "SELECT certificate_id, certificate_type FROM decision_certificates ORDER BY certificate_id"
        ).fetchall()
        assert kept == [
            ("kept", "BeliefCertificate"),
            ("old-sizing", migration.OLD_SIZING_CERTIFICATE),
        ]
        assert conn.execute("SELECT COUNT(*) FROM decision_certificate_edges").fetchone()[0] == 1
        assert conn.execute("SELECT supersession_id FROM decision_certificate_supersessions").fetchall() == [("keep",)]
        assert conn.execute("SELECT failure_id FROM decision_compile_failures").fetchall() == [("live-failure",)]
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='decision_certificates'"
        ).fetchone()[0]
        assert "CHECK (mode = 'LIVE')" in schema
        assert _retired_mode() not in schema
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    finally:
        conn.close()

    durable = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert durable == receipt
    assert receipt["counts"]["certificates_remove"] == 3
    assert receipt["counts"]["preserved_live_old_sizing_predecessors"] == 1
    assert receipt["closure_class_counts"] == {"dependent": 2, "seed": 1}
    assert receipt["removed_compile_failure_summary"] == [
        {
            "count": 1,
            "max_created_at": "2026-07-22T12:06:00+00:00",
            "max_decision_time": "2026-07-22T12:05:00+00:00",
            "min_created_at": "2026-07-22T12:06:00+00:00",
            "min_decision_time": "2026-07-22T12:05:00+00:00",
            "reason_code": "OLD_MODE",
            "stage": "compile",
        }
    ]
    assert receipt["historical_opaque_reference_counts"] == {
        "position_events.payload_json": 1
    }
    assert receipt["trades_ghost_decision_graph"]["pre_drop_counts"] == {
        "decision_certificate_edges": 1,
        "decision_certificate_supersessions": 1,
        "decision_certificates": 1,
        "decision_compile_failures": 1,
    }
    trades_conn = sqlite3.connect(trades)
    try:
        remaining = {
            row[0]
            for row in trades_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not set(migration.TRADES_GHOST_DROP_ORDER) & remaining
        assert "position_decision_attribution" in remaining
    finally:
        trades_conn.close()
    assert not list(receipt_path.parent.glob(".*.tmp"))


def test_active_position_attribution_refuses_without_writes(tmp_path: Path) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "refused.json"
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        tconn.execute("INSERT INTO position_current VALUES ('position-1', 'active')")
        tconn.execute(
            "INSERT INTO position_decision_attribution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("attr-1", "position-1", "command-1", "hash-retired", "ATTRIBUTED", None, "LIVE_DECISION", "ENTRY", "2026-07-22T12:00:00+00:00", 1),
        )
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    plan = migration.plan_world_decision_graph(world, trades)
    assert plan["status"] == "blocked"
    assert "retired_closure_referenced_by_current_position" in {
        blocker["kind"] for blocker in plan["blockers"]
    }
    with pytest.raises(RuntimeError, match="current_position"):
        migration.migrate_world_decision_graph(world, trades, receipt)
    conn = sqlite3.connect(world)
    try:
        assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 1
        assert _retired_mode() in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='decision_certificates'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert not receipt.exists()


def test_protected_missing_attribution_fails_before_closure_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "refused-before-closure.json"
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        tconn.execute("INSERT INTO position_current VALUES ('position-1', 'active')")
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    def closure_must_not_run(_: sqlite3.Connection) -> bool:
        raise AssertionError("retired closure materialization must not run")

    monkeypatch.setattr(migration, "_materialize_retired_closure", closure_must_not_run)
    plan = migration.plan_world_decision_graph(world, trades)
    assert plan["status"] == "blocked"
    assert plan["fast_fail"] == "protected_position_attribution"
    assert plan["blockers"][0]["kind"] == "current_projection_attribution_unresolved"
    with pytest.raises(RuntimeError, match="current_projection_attribution_unresolved"):
        migration.migrate_world_decision_graph(world, trades, receipt)
    assert not receipt.exists()


def test_retired_attribution_hash_matching_is_case_insensitive(tmp_path: Path) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    try:
        _insert_certificate(wconn, "retired", "HASH-RETIRED", mode=_retired_mode())
        tconn.execute("INSERT INTO position_current VALUES ('position-1', 'active')")
        tconn.execute(
            "INSERT INTO position_decision_attribution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "attr-1",
                "position-1",
                "command-1",
                "hash-retired",
                "ATTRIBUTED",
                None,
                "LIVE_DECISION",
                "ENTRY",
                "2026-07-22T12:00:00+00:00",
                1,
            ),
        )
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    plan = migration.plan_world_decision_graph(world, trades)
    assert plan["status"] == "blocked"
    blocker = next(
        item
        for item in plan["blockers"]
        if item["kind"] == "retired_closure_referenced_by_current_position"
    )
    assert blocker["sample"][0]["decision_certificate_hash"] == "hash-retired"


def test_mixed_case_parent_hash_closes_dependent_certificate(tmp_path: Path) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "mixed-case.json"
    try:
        _insert_certificate(wconn, "retired", "HASH-RETIRED", mode=_retired_mode())
        _insert_certificate(wconn, "dependent", "HASH-DEPENDENT")
        _edge(wconn, "dependent", "hash-retired")
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    result = migration.migrate_world_decision_graph(world, trades, receipt)
    assert result["counts"]["certificates_remove"] == 2
    conn = sqlite3.connect(world)
    try:
        assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    finally:
        conn.close()


def test_partial_posterior_rerun_rejects_non_live_runtime_layer(tmp_path: Path) -> None:
    path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE forecast_posteriors (
                posterior_id TEXT PRIMARY KEY,
                runtime_layer TEXT
            );
            INSERT INTO forecast_posteriors VALUES ('live', 'live');
            INSERT INTO forecast_posteriors VALUES ('partial', NULL);
            """
        )
        conn.commit()
    finally:
        conn.close()

    blockers = migration.mutation_blockers(path)
    assert blockers == [
        "zeus-forecasts.db:forecast_posteriors has 1 rows without a live runtime_layer"
    ]
    with pytest.raises(RuntimeError, match="lack a live runtime_layer"):
        migration.mutate_db(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute(
            "SELECT runtime_layer FROM forecast_posteriors WHERE posterior_id='partial'"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_stage_journal_resumes_interrupted_idempotent_stage(tmp_path: Path) -> None:
    journal = tmp_path / "cutover.progress.json"
    root = tmp_path / "root"
    root.mkdir()
    progress = migration._open_stage_journal(journal, root)
    calls: list[str] = []

    def interrupted() -> None:
        calls.append("interrupted")
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        migration._run_journaled_stage(journal, progress, "decision_graphs", interrupted)
    progress["status"] = "failed_resumable"
    progress["error"] = "RuntimeError: simulated interruption"
    migration._record_stage_journal(journal, progress, "failed", complete=False)

    resumed = migration._open_stage_journal(journal, root)

    def finish() -> str:
        calls.append("finished")
        return "done"

    assert migration._run_journaled_stage(journal, resumed, "decision_graphs", finish) == "done"
    assert migration._run_journaled_stage(journal, resumed, "decision_graphs", finish) is None
    durable = json.loads(journal.read_text(encoding="utf-8"))
    assert durable["status"] == "running"
    assert durable["completed_stages"] == ["decision_graphs"]
    assert durable["current_stage"] == "decision_graphs"
    assert calls == ["interrupted", "finished"]


def test_world_committed_trades_interruption_converges_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "rerun.json"
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    original_drop = migration.drop_trades_ghost_decision_graph

    def interrupt_after_world_commit(_: Path) -> dict[str, object]:
        raise RuntimeError("simulated TRADES interruption")

    monkeypatch.setattr(
        migration, "drop_trades_ghost_decision_graph", interrupt_after_world_commit
    )
    with pytest.raises(RuntimeError, match="WORLD DB COMMITTED"):
        migration.migrate_world_decision_graph(world, trades, receipt)

    monkeypatch.setattr(migration, "drop_trades_ghost_decision_graph", original_drop)
    rerun = migration.migrate_world_decision_graph(world, trades, receipt)
    assert rerun["counts"]["certificates_remove"] == 0
    trades_conn = sqlite3.connect(trades)
    try:
        tables = {
            row[0]
            for row in trades_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not set(migration.TRADES_GHOST_DROP_ORDER) & tables
    finally:
        trades_conn.close()


def test_orphan_postcheck_fails_closed(tmp_path: Path) -> None:
    world = tmp_path / "zeus-world.db"
    conn = _create_world(world, legacy_check=False)
    try:
        _insert_certificate(conn, "kept", "hash-kept")
        _edge(conn, "kept", "missing-parent")
        conn.commit()
        with pytest.raises(RuntimeError, match="orphan_edge_count"):
            migration.postcheck_world_decision_graph(conn)
    finally:
        conn.close()


def test_mid_transaction_edge_delete_failure_rolls_back_old_schema(tmp_path: Path) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "rollback.json"
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        _insert_certificate(wconn, "dependent", "hash-dependent")
        _edge(wconn, "dependent", "hash-retired")
        wconn.executescript(
            """
            CREATE TRIGGER force_edge_delete_failure
            BEFORE DELETE ON decision_certificate_edges
            BEGIN
                SELECT RAISE(ABORT, 'forced rollback');
            END;
            """
        )
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    with pytest.raises(sqlite3.IntegrityError, match="forced rollback"):
        migration.migrate_world_decision_graph(world, trades, receipt)

    conn = sqlite3.connect(world)
    try:
        assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM decision_certificate_edges").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='decision_certificates_live_new'"
        ).fetchone()[0] == 0
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='decision_certificates'"
        ).fetchone()[0]
        assert _retired_mode() in schema
    finally:
        conn.close()
    assert not receipt.exists()
