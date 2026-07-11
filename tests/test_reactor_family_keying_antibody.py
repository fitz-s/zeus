# Lifecycle: created=2026-06-04; last_reviewed=2026-07-11; last_reused=2026-07-11
# Purpose: Antibody — a NULL/empty condition_id sibling at the reactor family seam
#   must fail LOUD (FamilyKeyingError), never silently shrink the MECE family.
# Reuse: Re-run whenever the market_events->reactor family-binding seam or its
#   condition_id keying changes.
# Authority basis: Fitz Constraint #4 (data provenance / silent-keying-loss). Reactor
#   family-binding seam: a market_events sibling whose condition_id is NULL must NOT be
#   silently dropped from the MECE family — a silently-shrunk family either kills all
#   siblings (FDR_FAMILY_TOPOLOGY_INCOMPLETE) or renormalizes q over a subset
#   (~1.2x inflation at 3/11 missing). The keying loss must surface as a LOUD, named
#   error, never a vanished/corrupted market.
"""Antibody: NULL-condition_id sibling at the reactor family seam fails LOUD.

Relationship test (crosses the producer->consumer boundary): the market_events
table (producer of family topology) feeds ``_event_family_market_topology_rows``
(consumer that builds the MECE family the reactor binds and prices). The
relationship invariant is:

    For a (city, target_date, metric) family, EVERY admitted sibling must carry
    a resolved condition_id. If ANY matching market_events row has a NULL/empty
    condition_id, the family is keying-broken and MUST raise a named error —
    NOT silently return the surviving siblings (which corrupts the MECE
    partition that q/FDR are computed over).

This is the antibody for the silent ``COALESCE(condition_id,'') != ''`` /
``if not condition_id: continue`` drop. It is byte-identical to legacy behavior
when condition_id is clean (live: 0/21018 NULL today), so it changes no current
trade — it only converts a future silent keying loss into a loud, diagnosable
failure.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.engine.event_reactor_adapter import (
    FamilyKeyingError,
    _event_family_market_topology_rows,
)


def _market_events_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            outcome TEXT,
            market_slug TEXT
        )
        """
    )
    return conn


def _insert(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO market_events "
        "(city, target_date, temperature_metric, condition_id, token_id, range_label) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


_PAYLOAD = {"city": "TestCity", "target_date": "2026-06-05", "metric": "high"}


def test_clean_family_returns_all_siblings() -> None:
    """Control: a fully-keyed MECE family returns every sibling (no behavior change)."""
    conn = _market_events_conn()
    _insert(
        conn,
        [
            ("TestCity", "2026-06-05", "high", "cid_1", "tok1", "<=10"),
            ("TestCity", "2026-06-05", "high", "cid_2", "tok2", "10-11"),
            ("TestCity", "2026-06-05", "high", "cid_3", "tok3", ">=11"),
        ],
    )
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    rows = _event_family_market_topology_rows(conn, _PAYLOAD)
    conn.set_trace_callback(None)
    assert [r["condition_id"] for r in rows] == ["cid_1", "cid_2", "cid_3"]
    assert sum("FROM MARKET_EVENTS" in statement.upper() for statement in traced) == 1


def test_null_condition_id_sibling_fails_loud_not_silent_drop() -> None:
    """RED->GREEN antibody: a NULL-condition_id sibling raises FamilyKeyingError.

    Pre-antibody, this family silently returned 2 of 3 bins (the NULL sibling
    vanished), corrupting the MECE partition with no diagnosable signal. The
    antibody makes the keying loss LOUD.
    """
    conn = _market_events_conn()
    _insert(
        conn,
        [
            ("TestCity", "2026-06-05", "high", "cid_1", "tok1", "<=10"),
            ("TestCity", "2026-06-05", "high", None, "tok2", "10-11"),  # keying break
            ("TestCity", "2026-06-05", "high", "cid_3", "tok3", ">=11"),
        ],
    )
    with pytest.raises(FamilyKeyingError) as exc:
        _event_family_market_topology_rows(conn, _PAYLOAD)
    # The error must name the keying defect + the affected family so the loss is
    # diagnosable, never a generic downstream "snapshot missing".
    message = str(exc.value)
    assert "condition_id" in message
    assert "TestCity" in message
    assert "2026-06-05" in message


def test_empty_string_condition_id_also_fails_loud() -> None:
    """Empty-string condition_id is the same keying defect as NULL (both unresolved)."""
    conn = _market_events_conn()
    _insert(
        conn,
        [
            ("TestCity", "2026-06-05", "high", "cid_1", "tok1", "<=10"),
            ("TestCity", "2026-06-05", "high", "", "tok2", "10-11"),  # empty == unresolved
        ],
    )
    with pytest.raises(FamilyKeyingError):
        _event_family_market_topology_rows(conn, _PAYLOAD)


def test_duplicate_condition_id_also_fails_loud() -> None:
    conn = _market_events_conn()
    _insert(
        conn,
        [
            ("TestCity", "2026-06-05", "high", "cid_1", "tok1", "<=10"),
            ("TestCity", "2026-06-05", "high", "cid_1", "tok2", "10-11"),
        ],
    )
    with pytest.raises(FamilyKeyingError, match="duplicate condition_id"):
        _event_family_market_topology_rows(conn, _PAYLOAD)


def test_unrelated_city_null_does_not_poison_target_family() -> None:
    """A NULL-keyed row for a DIFFERENT family must not affect the queried family.

    The antibody scopes the keying check to the (city, target_date, metric)
    family actually being bound — a malformed row in a sibling city's family is
    not the target family's problem and must not raise here.
    """
    conn = _market_events_conn()
    _insert(
        conn,
        [
            ("TestCity", "2026-06-05", "high", "cid_1", "tok1", "<=10"),
            ("TestCity", "2026-06-05", "high", "cid_2", "tok2", ">=10"),
            ("OtherCity", "2026-06-05", "high", None, "tokX", "11-12"),  # other family
        ],
    )
    rows = _event_family_market_topology_rows(conn, _PAYLOAD)
    assert [r["condition_id"] for r in rows] == ["cid_1", "cid_2"]
