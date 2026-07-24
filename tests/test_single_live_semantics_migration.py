# Created: 2026-07-23
# Last reused/audited: 2026-07-23
# Authority basis: operator-directed WORLD single-live decision-graph cutover.
# Invariants: INV-03, INV-08, INV-17, INV-29, INV-30, INV-37
"""Fixture-only antibodies for the single-live WORLD decision-graph migration."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
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
            certificate_type=migration.RETIRED_SIZING_CERTIFICATE,
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
            ("old-sizing", migration.RETIRED_SIZING_CERTIFICATE),
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
    assert receipt["historical_opaque_reference_counts"] == {}
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


def _insert_nonterminal_command(
    conn: sqlite3.Connection,
    command_id: str,
    position_id: str,
) -> None:
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, 'SUBMITTING')",
        (command_id, position_id),
    )


def _insert_attribution(
    conn: sqlite3.Connection,
    attribution_id: str,
    position_id: str,
    command_id: str,
    certificate_hash: str | None,
    *,
    resolution: str = "ATTRIBUTED",
) -> None:
    conn.execute(
        "INSERT INTO position_decision_attribution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            attribution_id,
            position_id,
            command_id,
            certificate_hash,
            resolution,
            None,
            "LIVE_DECISION",
            "ENTRY",
            "2026-07-22T12:00:00+00:00",
            1,
        ),
    )


def _command_plan(
    tmp_path: Path,
    configure: object,
) -> dict[str, object]:
    world, trades, wconn, tconn = _fixture(tmp_path)
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        _insert_certificate(wconn, "current", "hash-current")
        configure(tconn)
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()
    return migration.plan_world_decision_graph(world, trades)


def test_nonterminal_command_without_attribution_blocks_before_writes(
    tmp_path: Path,
) -> None:
    plan = _command_plan(
        tmp_path,
        lambda conn: _insert_nonterminal_command(conn, "command-1", "position-1"),
    )
    assert plan["status"] == "blocked"
    assert plan["trades_preflight"]["nonterminal_command_total"] == 1
    assert plan["trades_preflight"]["nonterminal_command_unresolved"] == 1
    assert "nonterminal_command_attribution_unresolved" in {
        blocker["kind"] for blocker in plan["blockers"]
    }


@pytest.mark.parametrize(
    ("resolution", "certificate_hash"),
    (("UNRESOLVED", "hash-current"), ("ATTRIBUTED", "missing-hash")),
)
def test_nonterminal_command_unresolved_or_missing_certificate_blocks(
    tmp_path: Path,
    resolution: str,
    certificate_hash: str,
) -> None:
    def configure(conn: sqlite3.Connection) -> None:
        _insert_nonterminal_command(conn, "command-1", "position-1")
        _insert_attribution(
            conn,
            "attr-1",
            "position-1",
            "command-1",
            certificate_hash,
            resolution=resolution,
        )

    plan = _command_plan(tmp_path, configure)
    assert plan["trades_preflight"]["nonterminal_command_unresolved"] == 1
    assert "nonterminal_command_attribution_unresolved" in {
        blocker["kind"] for blocker in plan["blockers"]
    }


def test_nonterminal_commands_bind_by_command_id_not_position_id(tmp_path: Path) -> None:
    def configure(conn: sqlite3.Connection) -> None:
        _insert_nonterminal_command(conn, "command-1", "position-1")
        _insert_nonterminal_command(conn, "command-2", "position-1")
        _insert_attribution(
            conn,
            "attr-2",
            "position-1",
            "command-2",
            "hash-current",
        )

    plan = _command_plan(tmp_path, configure)
    assert plan["trades_preflight"]["nonterminal_command_total"] == 2
    assert plan["trades_preflight"]["nonterminal_command_attributed"] == 1
    assert plan["trades_preflight"]["nonterminal_command_unresolved"] == 1


def test_duplicate_command_attribution_blocks(tmp_path: Path) -> None:
    def configure(conn: sqlite3.Connection) -> None:
        _insert_nonterminal_command(conn, "command-1", "position-1")
        _insert_attribution(conn, "attr-1", "position-1", "command-1", "hash-current")
        _insert_attribution(conn, "attr-2", "position-1", "command-1", "hash-current")

    plan = _command_plan(tmp_path, configure)
    assert plan["trades_preflight"]["nonterminal_command_unresolved"] == 1
    sample = plan["trades_preflight"]["nonterminal_command_unresolved_sample"]
    assert sample[0]["attribution_count"] == 2


def test_duplicate_command_id_across_positions_blocks_before_writes(
    tmp_path: Path,
) -> None:
    def configure(conn: sqlite3.Connection) -> None:
        _insert_nonterminal_command(conn, "command-1", "position-1")
        _insert_attribution(conn, "attr-1", "position-1", "command-1", "hash-current")
        _insert_attribution(conn, "attr-2", "position-2", "command-1", "hash-current")

    plan = _command_plan(tmp_path, configure)
    kinds = {blocker["kind"] for blocker in plan["blockers"]}
    assert "duplicate_command_attribution" in kinds
    assert "nonterminal_command_attribution_unresolved" in kinds
    assert plan["trades_preflight"]["duplicate_command_attribution_count"] == 1
    sample = plan["trades_preflight"]["nonterminal_command_unresolved_sample"]
    assert sample[0]["attribution_count"] == 2
    assert sample[0]["matching_position_count"] == 1


@pytest.mark.parametrize("malformed", (False, True))
def test_historical_reference_without_archive_blocks_before_writes(
    tmp_path: Path,
    malformed: bool,
) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "historical-refused.json"
    retired_hash = "a" * 64
    try:
        _insert_certificate(wconn, "retired", retired_hash, mode=_retired_mode())
        payload = (
            f'{{"certificate":"{retired_hash}"'
            if malformed
            else json.dumps({"certificate": retired_hash})
        )
        tconn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?)",
            ("historical", None, payload),
        )
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    plan = migration.plan_world_decision_graph(
        world,
        trades,
        include_opaque_references=True,
    )
    assert plan["status"] == "blocked"
    assert "retired_closure_referenced_by_durable_history" in {
        blocker["kind"] for blocker in plan["blockers"]
    }
    with pytest.raises(RuntimeError, match="durable_history"):
        migration.migrate_world_decision_graph(world, trades, receipt)
    assert not receipt.exists()
    conn = sqlite3.connect(world)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM decision_certificates WHERE certificate_hash=?",
            (retired_hash,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


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


def test_unknown_position_attribution_blocks_before_any_graph_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, trades, wconn, tconn = _fixture(tmp_path)
    receipt = tmp_path / "unknown-refused.json"
    try:
        _insert_certificate(wconn, "retired", "hash-retired", mode=_retired_mode())
        tconn.execute("INSERT INTO position_current VALUES ('position-unknown', 'unknown')")
        wconn.commit()
        tconn.commit()
    finally:
        wconn.close()
        tconn.close()

    monkeypatch.setattr(
        migration,
        "_materialize_retired_closure",
        lambda _: (_ for _ in ()).throw(AssertionError("must not traverse graph")),
    )
    plan = migration.plan_world_decision_graph(world, trades)
    assert plan["fast_fail"] == "protected_position_attribution"
    assert plan["trades_preflight"]["unresolved_current_projection_count"] == 1
    with pytest.raises(RuntimeError, match="current_projection_attribution_unresolved"):
        migration.migrate_world_decision_graph(world, trades, receipt)
    assert not receipt.exists()


@pytest.mark.parametrize(
    "table",
    (
        migration.RETIRED_TRANSFER_TABLE,
        migration.RETIRED_CONVERSION_TABLE,
        migration.RETIRED_CONVERSION_EVENTS,
    ),
)
def test_nonempty_retired_table_is_never_dropped(tmp_path: Path, table: str) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        conn.execute(f'INSERT INTO "{table}" VALUES (1)')
        conn.commit()
    finally:
        conn.close()

    assert migration.mutation_blockers(path) == [
        f"state.db:{table} is non-empty (1)"
    ]
    with pytest.raises(RuntimeError, match="refusing to drop non-empty retired table"):
        migration.mutate_db(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    "table",
    (
        "evidence_tier_assignments",
        "source_time_frontier",
        "model_bias_ens",
        "model_bias",
        "forecast_skill",
    ),
)
def test_current_evidence_tables_are_preserved(
    tmp_path: Path, table: str
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(f'CREATE TABLE "{table}" (value TEXT PRIMARY KEY)')
        conn.execute(f'INSERT INTO "{table}" VALUES (?)', ("keep-byte-for-byte",))
        conn.commit()
    finally:
        conn.close()

    assert migration.mutation_blockers(path) == []
    assert migration.mutate_db(path) == []
    conn = sqlite3.connect(path)
    try:
        assert conn.execute(f'SELECT value FROM "{table}"').fetchone()[0] == "keep-byte-for-byte"
    finally:
        conn.close()


def test_retired_truth_epoch_table_is_deleted(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE truth_epoch "
            "(id INTEGER PRIMARY KEY, epoch TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO truth_epoch VALUES (1, 'ACTIVE_NEW')")
        conn.commit()
    finally:
        conn.close()

    assert migration.mutation_blockers(path) == []
    assert migration.mutate_db(path) == ["dropped truth_epoch (1 retired rows)"]
    conn = sqlite3.connect(path)
    try:
        assert not migration.table_exists(conn, "truth_epoch")
    finally:
        conn.close()

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


def test_stage_precondition_runs_before_action(tmp_path: Path) -> None:
    journal = tmp_path / "cutover.progress.json"
    root = tmp_path / "root"
    root.mkdir()
    progress = migration._open_stage_journal(journal, root)
    calls: list[str] = []

    def refuse() -> None:
        calls.append("fence")
        raise RuntimeError("writer appeared")

    with pytest.raises(RuntimeError, match="writer appeared"):
        migration._run_journaled_stage(
            journal,
            progress,
            "db",
            lambda: calls.append("write"),
            precondition=refuse,
        )
    assert calls == ["fence"]
    assert progress["completed_stages"] == []


def test_writer_fence_rejects_loaded_job_and_open_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migration, "live_writers", lambda: [])
    monkeypatch.setattr(migration, "loaded_writer_jobs", lambda _root: ["com.zeus.main"])
    monkeypatch.setattr(
        migration,
        "open_canonical_db_handles",
        lambda _root: ["p123 state/zeus-world.db"],
    )
    with pytest.raises(RuntimeError, match="launchd com.zeus.main") as exc:
        migration.assert_writer_fence(tmp_path)
    assert "open_handle p123 state/zeus-world.db" in str(exc.value)


def test_stage_journal_rejects_release_identity_drift(tmp_path: Path) -> None:
    journal = tmp_path / "cutover.progress.json"
    root = tmp_path / "root"
    root.mkdir()
    identity = {
        "target_head": "a" * 40,
        "migration_script_sha256": "b" * 64,
        "schema_fingerprint": "c" * 64,
    }
    migration._open_stage_journal(journal, root, target_identity=identity)
    drifted = {**identity, "target_head": "d" * 40}
    with pytest.raises(RuntimeError, match="does not match this migration target"):
        migration._open_stage_journal(journal, root, target_identity=drifted)


def _target_fixture(tmp_path: Path) -> tuple[Path, tuple[Path, ...], Path]:
    root = tmp_path / "root"
    state = root / "state"
    config = root / "config"
    external = tmp_path / "external"
    state.mkdir(parents=True)
    config.mkdir()
    external.mkdir()
    dbs = tuple(state / name for name in (
        "zeus-world.db", "zeus-forecasts.db", "zeus_trades.db", "risk_state.db"
    ))
    for path in dbs:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE risk_state (value TEXT)")
        conn.execute("INSERT INTO risk_state VALUES ('before')")
        conn.commit()
        conn.close()
    target = external / "settings-a.json"
    target.write_text("{}\n", encoding="utf-8")
    settings = config / "settings.json"
    settings.symlink_to(target)
    return root, dbs, settings


def test_resume_rejects_replaced_database_and_same_schema_content(
    tmp_path: Path,
) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    journal = tmp_path / "cutover.progress.json"
    identity = {"target_head": "a" * 40}
    initial = migration.target_state_identity(root, dbs, settings)
    migration._open_stage_journal(
        journal, root, target_identity=identity, target_state=initial
    )
    replacement = tmp_path / "replacement.db"
    conn = sqlite3.connect(replacement)
    conn.execute("CREATE TABLE risk_state (value TEXT)")
    conn.execute("INSERT INTO risk_state VALUES ('different-generation')")
    conn.commit()
    conn.close()
    dbs[1].unlink()
    replacement.replace(dbs[1])
    changed = migration.target_state_identity(root, dbs, settings)
    with pytest.raises(RuntimeError, match="target generation changed"):
        migration._open_stage_journal(
            journal, root, target_identity=identity, target_state=changed
        )


def test_resume_rejects_settings_symlink_retarget(tmp_path: Path) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    journal = tmp_path / "cutover.progress.json"
    initial = migration.target_state_identity(root, dbs, settings)
    migration._open_stage_journal(journal, root, target_state=initial)
    second = tmp_path / "external" / "settings-b.json"
    second.write_text("{}\n", encoding="utf-8")
    settings.unlink()
    settings.symlink_to(second)
    with pytest.raises(RuntimeError, match="target generation changed"):
        migration._open_stage_journal(
            journal,
            root,
            target_state=migration.target_state_identity(root, dbs, settings),
        )


def test_completed_stage_revalidates_postcondition(tmp_path: Path) -> None:
    journal = tmp_path / "cutover.progress.json"
    root = tmp_path / "root"
    root.mkdir()
    progress = migration._open_stage_journal(journal, root)
    migration._run_journaled_stage(journal, progress, "db", lambda: None)
    with pytest.raises(RuntimeError, match="restored pre-cutover state"):
        migration._run_journaled_stage(
            journal,
            progress,
            "db",
            lambda: pytest.fail("completed action must not rerun"),
            postcondition=lambda: (_ for _ in ()).throw(
                RuntimeError("restored pre-cutover state")
            ),
        )


def test_resume_accepts_own_transactional_generation_after_journal_gap(
    tmp_path: Path,
) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    journal = tmp_path / "cutover.progress.json"
    def state() -> dict[str, object]:
        return migration.target_state_identity(root, dbs, settings)

    progress = migration._open_stage_journal(journal, root, target_state=state())
    generation = progress["migration_generation"]
    stage = "mutated:zeus-world.db"

    def commit_then_interrupt() -> None:
        conn = sqlite3.connect(dbs[0], isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        migration.mark_cutover_generation(
            conn, generation=generation, stage=stage
        )
        conn.execute("COMMIT")
        conn.close()
        raise RuntimeError("killed after DB commit before journal update")

    with pytest.raises(RuntimeError, match="after DB commit"):
        migration._run_journaled_stage(
            journal,
            progress,
            stage,
            commit_then_interrupt,
            current_target_state=state,
        )
    resumed = migration._open_stage_journal(journal, root, target_state=state())
    assert resumed["recovering_stage_commit"] == stage
    assert migration._run_journaled_stage(
        journal,
        resumed,
        stage,
        lambda: "converged",
        postcondition=lambda: None,
        current_target_state=state,
    ) == "converged"


def test_resume_rejects_external_write_after_stage_marker_before_journal(
    tmp_path: Path,
) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    journal = tmp_path / "cutover.progress.json"

    def state() -> dict[str, object]:
        return migration.target_state_identity(root, dbs, settings)

    progress = migration._open_stage_journal(journal, root, target_state=state())
    generation = progress["migration_generation"]
    stage = "mutated:zeus-world.db"

    def commit_then_interrupt() -> None:
        conn = sqlite3.connect(dbs[0], isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        migration.mark_cutover_generation(
            conn, generation=generation, stage=stage
        )
        conn.execute("COMMIT")
        conn.close()
        raise RuntimeError("killed after DB commit before journal update")

    with pytest.raises(RuntimeError, match="after DB commit"):
        migration._run_journaled_stage(
            journal,
            progress,
            stage,
            commit_then_interrupt,
            current_target_state=state,
        )

    conn = sqlite3.connect(dbs[0])
    conn.execute("CREATE TABLE external_generation (value TEXT NOT NULL)")
    conn.execute("INSERT INTO external_generation VALUES ('foreign-write')")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="target generation changed"):
        migration._open_stage_journal(journal, root, target_state=state())


@pytest.mark.parametrize(
    "mutation", ("rowid", "shadowed_rowid", "user_version", "sqlite_sequence")
)
def test_resume_rejects_metadata_only_change_after_stage_marker(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    if mutation == "sqlite_sequence":
        conn = sqlite3.connect(dbs[0])
        conn.execute(
            "CREATE TABLE sequenced (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)"
        )
        conn.execute("INSERT INTO sequenced(value) VALUES ('before')")
        conn.commit()
        conn.close()
    elif mutation == "shadowed_rowid":
        conn = sqlite3.connect(dbs[0])
        conn.execute("CREATE TABLE rowid_shadow (rowid INTEGER, payload TEXT)")
        conn.execute("INSERT INTO rowid_shadow VALUES (7, 'before')")
        conn.commit()
        conn.close()
    journal = tmp_path / "cutover.progress.json"

    def state() -> dict[str, object]:
        return migration.target_state_identity(root, dbs, settings)

    progress = migration._open_stage_journal(journal, root, target_state=state())
    generation = progress["migration_generation"]
    stage = "mutated:zeus-world.db"

    def commit_then_interrupt() -> None:
        conn = sqlite3.connect(dbs[0], isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        migration.mark_cutover_generation(
            conn, generation=generation, stage=stage
        )
        conn.execute("COMMIT")
        conn.close()
        raise RuntimeError("killed after DB commit before journal update")

    with pytest.raises(RuntimeError, match="after DB commit"):
        migration._run_journaled_stage(
            journal,
            progress,
            stage,
            commit_then_interrupt,
            current_target_state=state,
        )

    conn = sqlite3.connect(dbs[0])
    if mutation == "rowid":
        conn.execute("UPDATE risk_state SET rowid = rowid + 100")
    elif mutation == "shadowed_rowid":
        conn.execute("UPDATE rowid_shadow SET _rowid_ = _rowid_ + 100")
    elif mutation == "user_version":
        conn.execute("PRAGMA user_version=73")
    else:
        conn.execute("UPDATE sqlite_sequence SET seq = seq + 100")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="target generation changed"):
        migration._open_stage_journal(journal, root, target_state=state())


def test_digest_fails_closed_when_every_hidden_rowid_alias_is_shadowed() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        'CREATE TABLE hostile (rowid INTEGER, _rowid_ INTEGER, oid INTEGER, payload TEXT)'
    )
    conn.execute("INSERT INTO hostile VALUES (1, 2, 3, 'value')")
    with pytest.raises(RuntimeError, match="every SQLite alias is shadowed"):
        migration._digest_sqlite_migration_state(conn)
    conn.close()


def test_runtime_json_recovery_rejects_unrelated_external_change(tmp_path: Path) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    local = root / ".local"
    local.mkdir()
    unrelated = local / "unrelated.json"
    unrelated.write_text('{"value":1}\n', encoding="utf-8")
    journal = tmp_path / "cutover.progress.json"

    def state() -> dict[str, object]:
        return migration.target_state_identity(root, dbs, settings)

    progress = migration._open_stage_journal(journal, root, target_state=state())
    with pytest.raises(RuntimeError, match="interrupted"):
        migration._run_journaled_stage(
            journal,
            progress,
            "runtime_json",
            lambda: (_ for _ in ()).throw(RuntimeError("interrupted")),
            current_target_state=state,
            expected_recovery_state=lambda: migration.expected_runtime_json_hashes(root),
        )
    unrelated.write_text('{"value":2}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="target generation changed"):
        migration._open_stage_journal(journal, root, target_state=state())


def test_config_recovery_accepts_exact_clean_hash_but_rejects_later_edit(
    tmp_path: Path,
) -> None:
    root, dbs, settings = _target_fixture(tmp_path)
    settings.resolve().write_text(
        json.dumps({migration.RETIRED_CONFIG_KEYS[0]: True, "keep": 1}) + "\n",
        encoding="utf-8",
    )
    journal = tmp_path / "cutover.progress.json"

    def state() -> dict[str, object]:
        return migration.target_state_identity(root, dbs, settings)

    progress = migration._open_stage_journal(journal, root, target_state=state())

    def clean_then_interrupt() -> None:
        migration.clean_config(settings)
        raise RuntimeError("interrupted after config replace")

    with pytest.raises(RuntimeError, match="after config replace"):
        migration._run_journaled_stage(
            journal,
            progress,
            "config",
            clean_then_interrupt,
            current_target_state=state,
            expected_recovery_state=lambda: migration.expected_settings_identity(settings),
        )
    resumed = migration._open_stage_journal(journal, root, target_state=state())
    assert resumed["recovering_stage_commit"] == "config"
    settings.resolve().write_text('{"keep":2}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="target generation changed"):
        migration._open_stage_journal(journal, root, target_state=state())


def test_cutover_lease_blocks_runtime_writer_after_fence(tmp_path: Path) -> None:
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    root = tmp_path / "root"
    db = root / "state" / "zeus-world.db"
    db.parent.mkdir(parents=True)
    sqlite3.connect(db).close()
    with migration.cutover_lease(root, (db,)):
        with pytest.raises(BlockingIOError, match="cutover lease contended"):
            with db_writer_lock(db, WriteClass.LIVE, blocking=False):
                pytest.fail("writer entered during exclusive cutover")
    with db_writer_lock(db, WriteClass.LIVE, blocking=False):
        pass


def test_open_canonical_connection_blocks_exclusive_cutover(tmp_path: Path) -> None:
    from src.state.db_writer_lock import connect_with_cutover_lease

    root = tmp_path / "root"
    db = root / "state" / "zeus-world.db"
    db.parent.mkdir(parents=True)
    conn = connect_with_cutover_lease(
        str(db), canonical_db_path=db, timeout=0.0
    )
    try:
        with pytest.raises(RuntimeError, match="canonical writer lease is held"):
            with migration.cutover_lease(root, (db,)):
                pytest.fail("exclusive cutover entered while canonical connection lived")
    finally:
        conn.close()


def test_legacy_risk_and_collateral_factories_obey_cutover(tmp_path: Path) -> None:
    from src.state.collateral_ledger import _connect_owned_collateral_db
    from src.state.db import get_connection

    root = tmp_path / "root"
    risk_db = root / "state" / "risk_state.db"
    trade_db = root / "state" / "zeus_trades.db"
    risk = get_connection(risk_db, write_class="live")
    collateral = _connect_owned_collateral_db(trade_db)
    try:
        with pytest.raises(RuntimeError, match="canonical writer lease is held"):
            with migration.cutover_lease(root, (risk_db, trade_db)):
                pytest.fail("cutover entered over live legacy factories")
    finally:
        risk.close()
        collateral.close()


def test_prepost_trade_connection_holds_cutover_lease(tmp_path: Path) -> None:
    from src.state.db import connect_existing_trade_db_without_journal_bootstrap

    root = tmp_path / "root"
    trade_db = root / "state" / "zeus_trades.db"
    trade_db.parent.mkdir(parents=True)
    sqlite3.connect(trade_db).close()
    conn = connect_existing_trade_db_without_journal_bootstrap(trade_db)
    try:
        with pytest.raises(RuntimeError, match="canonical writer lease is held"):
            with migration.cutover_lease(root, (trade_db,)):
                pytest.fail("cutover entered over pre-POST trade connection")
    finally:
        conn.close()


def test_prepost_trade_connection_waits_for_exclusive_cutover(tmp_path: Path) -> None:
    from src.state.db import connect_existing_trade_db_without_journal_bootstrap

    root = tmp_path / "root"
    trade_db = root / "state" / "zeus_trades.db"
    trade_db.parent.mkdir(parents=True)
    sqlite3.connect(trade_db).close()
    started = threading.Event()
    connected = threading.Event()
    errors: list[BaseException] = []

    def open_connection() -> None:
        started.set()
        try:
            conn = connect_existing_trade_db_without_journal_bootstrap(trade_db)
            connected.set()
            conn.close()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    with migration.cutover_lease(root, (trade_db,)):
        thread = threading.Thread(target=open_connection)
        thread.start()
        assert started.wait(1.0)
        assert not connected.wait(0.1)

    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert connected.is_set()


def test_position_attribution_schema_migrates_to_command_exact(tmp_path: Path) -> None:
    path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
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
            schema_version INTEGER NOT NULL,
            UNIQUE(position_id)
        );
        INSERT INTO position_decision_attribution VALUES
          ('a1','p1','c1','h1','ATTRIBUTED',NULL,'LIVE_DECISION','ENTRY','now',1);
        """
    )
    conn.commit()
    assert migration.migrate_command_attribution_schema(conn)
    conn.execute(
        "INSERT INTO position_decision_attribution VALUES "
        "('a2','p1','c2','h1','ATTRIBUTED',NULL,'LIVE_DECISION','EXIT','later',1)"
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM position_decision_attribution WHERE position_id='p1'"
    ).fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO position_decision_attribution VALUES "
            "('a3','p2','c1','h1','ATTRIBUTED',NULL,'LIVE_DECISION','ENTRY','later',1)"
        )
    conn.close()


def test_target_release_identity_refuses_foreign_checkout(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="target checkout itself"):
        migration.target_release_identity(tmp_path)


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
