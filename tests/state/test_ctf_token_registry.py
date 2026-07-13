# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Attack F;
#   src/state/schema/ctf_token_registry_schema.py; src/state/ctf_token_registry.py
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: idempotent-open + first_source immutability + never-delete-on-absence
#   antibodies for ctf_token_registry.

"""Tests for ctf_token_registry schema + writer/reader."""

from __future__ import annotations

import sqlite3

import pytest

from src.state.ctf_token_registry import (
    CtfTokenRegistryRow,
    get_token_registry_row,
    known_token_ids,
    record_token_seen,
)
from src.state.schema.ctf_token_registry_schema import ensure_table


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


def test_first_observation_inserts_row():
    conn = _make_conn()
    row = record_token_seen(
        conn, token_id="tok1", condition_id="cond1", source="zeus_command", seen_at="t0",
    )
    assert row == CtfTokenRegistryRow("tok1", "cond1", "zeus_command", "t0", "t0")
    assert conn.execute("SELECT COUNT(*) FROM ctf_token_registry").fetchone()[0] == 1


def test_second_observation_advances_last_confirmed_only():
    """first_source/first_seen_at are immutable provenance -- only last_confirmed_at moves."""
    conn = _make_conn()
    record_token_seen(conn, token_id="tok1", condition_id="cond1", source="zeus_command", seen_at="t0")
    row = record_token_seen(
        conn, token_id="tok1", condition_id="cond1", source="positions_api_discovery", seen_at="t1",
    )
    assert row.first_source == "zeus_command"
    assert row.first_seen_at == "t0"
    assert row.last_confirmed_at == "t1"
    # still exactly one row -- not an append log.
    assert conn.execute("SELECT COUNT(*) FROM ctf_token_registry").fetchone()[0] == 1


def test_condition_id_conflict_keeps_original_condition_id(caplog):
    conn = _make_conn()
    record_token_seen(conn, token_id="tok1", condition_id="cond1", source="zeus_command", seen_at="t0")
    with caplog.at_level("ERROR"):
        row = record_token_seen(
            conn, token_id="tok1", condition_id="cond2_WRONG", source="market_topology", seen_at="t1",
        )
    assert row.condition_id == "cond1"
    assert "condition_id conflict" in caplog.text


def test_never_delete_on_absence_raises():
    """LAW: absence from a later /positions read never proves zero -- rows are never deleted."""
    conn = _make_conn()
    record_token_seen(conn, token_id="tok1", condition_id="cond1", source="zeus_command")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM ctf_token_registry WHERE token_id = 'tok1'")
    # row is untouched by the aborted delete attempt.
    assert get_token_registry_row(conn, token_id="tok1") is not None


def test_get_token_registry_row_absent_returns_none():
    conn = _make_conn()
    assert get_token_registry_row(conn, token_id="never-seen") is None


def test_known_token_ids_never_shrinks_across_multiple_records():
    conn = _make_conn()
    record_token_seen(conn, token_id="tok1", condition_id="cond1", source="zeus_command")
    record_token_seen(conn, token_id="tok2", condition_id="cond2", source="market_topology")
    assert known_token_ids(conn) == frozenset({"tok1", "tok2"})
    # A later pass that only re-observes tok1 (e.g. tok2 vanished from /positions)
    # must not remove tok2 -- known_token_ids only grows or stays flat.
    record_token_seen(conn, token_id="tok1", condition_id="cond1", source="positions_api_discovery")
    assert known_token_ids(conn) == frozenset({"tok1", "tok2"})


@pytest.mark.parametrize(
    "source",
    ["zeus_command", "market_topology", "attributed_fill", "transfer_observation", "positions_api_discovery"],
)
def test_all_five_first_sources_accepted(source):
    conn = _make_conn()
    row = record_token_seen(conn, token_id="tok1", condition_id="cond1", source=source)
    assert row.first_source == source


def test_invalid_source_rejected():
    conn = _make_conn()
    with pytest.raises(ValueError):
        record_token_seen(conn, token_id="tok1", condition_id="cond1", source="bogus_source")


def test_missing_token_id_or_condition_id_rejected():
    conn = _make_conn()
    with pytest.raises(ValueError):
        record_token_seen(conn, token_id="", condition_id="cond1", source="zeus_command")
    with pytest.raises(ValueError):
        record_token_seen(conn, token_id="tok1", condition_id="", source="zeus_command")
