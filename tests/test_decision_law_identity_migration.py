# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: prove COLLISION.md A2 (decision-law identity schema migration) is additive,
#   idempotent, and does not disturb table-registry coherence.
# Reuse: run on any change to decision_law_id / position_origin columns, DECISION_LAW_IDS /
#   POSITION_ORIGINS taxonomies, assert_law_identity, or the strategy_key NOT NULL relaxation.
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/COLLISION.md §保号
#   ("10 表 schema 迁移复用现有 rebuild 机器"), FINAL_SPEC.md §唯一决策律.
"""COLLISION.md A2 antibodies: 10-table decision-law identity migration.

New Zeus decisions will eventually write decision_law_id="predicted_bin_ev_v1",
strategy_key=NULL, position_origin="zeus_decision" (FINAL_SPEC.md §唯一决策律);
external co-trades write decision_law_id=NULL, position_origin="operator_cotrade"
or "external_wallet". This slice (group A2) lands the schema only — columns,
constants, and the write-boundary validator — with NO writer wired yet (group
B/C does that).

Additive posture, same idiom as capture_policy Track A
(tests/test_capture_policy_track_a.py, src/state/snapshot_repo.py:176-198):
plain nullable TEXT, no CHECK (a CHECK-constrained ADD COLUMN forces a full
table scan on the live ~110GB money DB), domain enforced at the write boundary
by assert_law_identity() instead of by the DB.

Fixture DBs only (init_schema / init_schema_trade_only on in-memory sqlite) —
never the live DB.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import (
    DECISION_LAW_IDS,
    POSITION_ORIGINS,
    _LAW_IDENTITY_TABLES,
    _POSITION_ORIGIN_TABLES,
    assert_law_identity,
    init_schema,
    init_schema_trade_only,
)
from src.state.table_registry import DBIdentity, assert_db_matches_registry

# The 5 tables whose CREATE TABLE declares strategy_key NOT NULL today (the
# other 5 of the 10 — readiness_state, execution_fact, outcome_fact,
# opportunity_fact, trade_decisions.strategy — are already nullable).
_NOT_NULL_RELAXED_TABLES = (
    "strategy_health",
    "decision_events",
    "position_events",
    "position_current",
    "risk_actions",
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _notnull(conn: sqlite3.Connection, table: str, column: str) -> int:
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == column:
            return int(row[3])
    raise AssertionError(f"{table}.{column} not found")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.commit()
    return conn


@pytest.fixture()
def trade_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema_trade_only(conn)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Columns exist after fresh init, on whichever DB each table physically lives.
# ---------------------------------------------------------------------------


def test_world_fresh_init_has_decision_law_id_on_every_present_table(
    world_conn: sqlite3.Connection,
) -> None:
    present = [t for t in _LAW_IDENTITY_TABLES if _table_exists(world_conn, t)]
    assert present, "expected at least one law-identity table to exist on world.db"
    for table in present:
        assert "decision_law_id" in _columns(world_conn, table), table


def test_trade_fresh_init_has_decision_law_id_on_every_present_table(
    trade_conn: sqlite3.Connection,
) -> None:
    present = [t for t in _LAW_IDENTITY_TABLES if _table_exists(trade_conn, t)]
    assert present
    for table in present:
        assert "decision_law_id" in _columns(trade_conn, table), table
    # decision_events / readiness_state are world-only — confirm the migration
    # correctly no-oped rather than erroring on their absence.
    assert not _table_exists(trade_conn, "decision_events") or "decision_law_id" in _columns(
        trade_conn, "decision_events"
    )


def test_position_origin_on_position_current_and_position_events(
    trade_conn: sqlite3.Connection,
) -> None:
    for table in _POSITION_ORIGIN_TABLES:
        assert _table_exists(trade_conn, table), table
        assert "position_origin" in _columns(trade_conn, table), table


def test_position_origin_absent_from_unrelated_tables(
    trade_conn: sqlite3.Connection,
) -> None:
    for table in _LAW_IDENTITY_TABLES:
        if table in _POSITION_ORIGIN_TABLES or not _table_exists(trade_conn, table):
            continue
        assert "position_origin" not in _columns(trade_conn, table), table


# ---------------------------------------------------------------------------
# Idempotent: re-running init on an already-migrated conn changes nothing.
# ---------------------------------------------------------------------------


def test_world_reinit_is_idempotent_and_preserves_rows(
    world_conn: sqlite3.Connection,
) -> None:
    world_conn.execute(
        """
        INSERT INTO decision_events (
            market_slug, temperature_metric, target_date, observation_time,
            decision_seq, decision_time, outcome, side, strategy_key,
            decision_law_id, observation_available_at,
            polymarket_end_anchor_source, schema_version, source
        ) VALUES (
            'slug-a', 'high', '2026-01-01', '2026-01-01T00:00:00+00:00', 1,
            '2026-01-01T00:00:00+00:00', 'win', 'buy_yes', NULL,
            'predicted_bin_ev_v1', '2026-01-01T00:00:00+00:00',
            'gamma_explicit', 13, 'live_decision'
        )
        """
    )
    world_conn.commit()
    before = world_conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]

    init_schema(world_conn)  # second run
    world_conn.commit()

    after = world_conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
    assert after == before
    row = world_conn.execute(
        "SELECT strategy_key, decision_law_id FROM decision_events WHERE market_slug='slug-a'"
    ).fetchone()
    assert row == (None, "predicted_bin_ev_v1")
    # decision_events has an AFTER INSERT trigger backstopping a NULL
    # decision_event_id — confirm the rebuild preserved it.
    triggers = {
        r[0]
        for r in world_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='decision_events'"
        )
    }
    assert "decision_events_event_id_backstop" in triggers


def test_trade_reinit_is_idempotent_and_preserves_rows(
    trade_conn: sqlite3.Connection,
) -> None:
    trade_conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, strategy_key, source_module, env, payload_json
        ) VALUES (
            'e-legacy', 'p1', 1, 1, 'POSITION_OPEN_INTENT',
            '2026-01-01T00:00:00+00:00', 'legacy_strat', 'mod', 'test', '{}'
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, decision_law_id, position_origin, source_module, env,
            payload_json
        ) VALUES (
            'e-new', 'p2', 1, 1, 'POSITION_OPEN_INTENT',
            '2026-01-01T00:00:00+00:00', 'predicted_bin_ev_v1', 'zeus_decision',
            'mod', 'test', '{}'
        )
        """
    )
    trade_conn.commit()
    before = trade_conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]

    init_schema_trade_only(trade_conn)  # second run
    trade_conn.commit()

    after = trade_conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    assert after == before == 2
    rows = dict(
        trade_conn.execute(
            "SELECT event_id, COALESCE(strategy_key,'') || '|' || "
            "COALESCE(decision_law_id,'') || '|' || COALESCE(position_origin,'') "
            "FROM position_events"
        ).fetchall()
    )
    assert rows["e-legacy"] == "legacy_strat||"
    assert rows["e-new"] == "|predicted_bin_ev_v1|zeus_decision"
    # append-only triggers must survive the rebuild.
    with pytest.raises(sqlite3.IntegrityError):
        trade_conn.execute(
            "UPDATE position_events SET strategy_key='x' WHERE event_id='e-legacy'"
        )


# ---------------------------------------------------------------------------
# NOT NULL relaxed exactly where claimed — nowhere else.
# ---------------------------------------------------------------------------


def test_strategy_key_nullable_on_relaxed_tables_world(
    world_conn: sqlite3.Connection,
) -> None:
    for table in _NOT_NULL_RELAXED_TABLES:
        if not _table_exists(world_conn, table):
            continue
        assert _notnull(world_conn, table, "strategy_key") == 0, table


def test_strategy_key_nullable_on_relaxed_tables_trade(
    trade_conn: sqlite3.Connection,
) -> None:
    for table in _NOT_NULL_RELAXED_TABLES:
        if not _table_exists(trade_conn, table):
            continue
        assert _notnull(trade_conn, table, "strategy_key") == 0, table


def test_readiness_state_strategy_key_already_nullable(
    world_conn: sqlite3.Connection,
) -> None:
    # readiness_state was already nullable in every live DDL variant
    # (db.py:2751/4254/4694) — the migration must not need to touch it, and it
    # must remain nullable.
    assert _notnull(world_conn, "readiness_state", "strategy_key") == 0


def test_already_nullable_trade_tables_untouched(trade_conn: sqlite3.Connection) -> None:
    for table in ("execution_fact", "outcome_fact", "opportunity_fact"):
        assert _table_exists(trade_conn, table), table
        assert _notnull(trade_conn, table, "strategy_key") == 0, table


def test_can_insert_null_strategy_key_row_on_relaxed_tables(
    trade_conn: sqlite3.Connection,
) -> None:
    """The whole point: a new-law row with strategy_key=NULL must not be
    rejected by a stale NOT NULL constraint."""
    trade_conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, strategy_key, decision_law_id, position_origin,
            source_module, env, payload_json
        ) VALUES (
            'e-null', 'p3', 1, 1, 'POSITION_OPEN_INTENT',
            '2026-01-01T00:00:00+00:00', NULL, 'predicted_bin_ev_v1',
            'zeus_decision', 'mod', 'test', '{}'
        )
        """
    )
    trade_conn.execute(
        "INSERT INTO risk_actions (action_id, strategy_key, action_type, value, "
        "issued_at, reason, source, precedence, status) VALUES "
        "('a1', NULL, 'gate', 'v', '2026-01-01T00:00:00+00:00', 'r', 'system', 1, 'active')"
    )
    trade_conn.execute(
        "INSERT INTO strategy_health (strategy_key, as_of, decision_law_id) "
        "VALUES (NULL, '2026-01-01', 'predicted_bin_ev_v1')"
    )
    trade_conn.commit()
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE event_id='e-null'"
    ).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# assert_law_identity validator: domain + None accepted, junk rejected.
# ---------------------------------------------------------------------------


def test_validator_accepts_none_for_both_fields() -> None:
    assert_law_identity(None, None)


def test_validator_accepts_domain_members() -> None:
    for law_id in DECISION_LAW_IDS:
        assert_law_identity(law_id, None)
    for origin in POSITION_ORIGINS:
        assert_law_identity(None, origin)
    assert_law_identity("predicted_bin_ev_v1", "zeus_decision")


def test_validator_rejects_unknown_decision_law_id() -> None:
    with pytest.raises(ValueError, match="decision_law_id"):
        assert_law_identity("not_a_real_law", None)


def test_validator_rejects_unknown_position_origin() -> None:
    with pytest.raises(ValueError, match="position_origin"):
        assert_law_identity(None, "some_other_origin")


def test_taxonomies_are_frozensets_with_expected_members() -> None:
    assert DECISION_LAW_IDS == frozenset({"predicted_bin_ev_v1"})
    assert POSITION_ORIGINS == frozenset(
        {"zeus_decision", "operator_cotrade", "external_wallet"}
    )


# ---------------------------------------------------------------------------
# Registry gate: extra columns are subset-legal, table-set must still match.
# ---------------------------------------------------------------------------


def test_world_still_matches_registry_after_migration(
    world_conn: sqlite3.Connection,
) -> None:
    assert_db_matches_registry(world_conn, DBIdentity.WORLD)


def test_trade_still_matches_registry_after_migration(
    trade_conn: sqlite3.Connection,
) -> None:
    assert_db_matches_registry(trade_conn, DBIdentity.TRADE)
