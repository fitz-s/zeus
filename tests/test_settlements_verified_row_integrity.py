# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: S2.2 data-readiness hardening tail
# (docs/operations/task_2026-04-23_midstream_remediation/); closure-banner
# AP-2 structural prevention — trigger enforces minimum VERIFIED-row
# invariants at DB write time so a writer that bypasses
# SettlementSemantics.assert_settlement_value() still cannot emit a
# half-populated VERIFIED row.

"""Antibody for settlements VERIFIED-row integrity triggers.

Pre-S2.2: SettlementSemantics.assert_settlement_value() was a social
gate — any writer that bypassed the function could emit a row with
authority='VERIFIED' + settlement_value=NULL + winning_bin='' and the
DB would accept it silently. This is the AP-2 failure mode documented
in the data-readiness workstream App-C.

Post-S2.2: two triggers reject these structural-integrity violations
at DB write time:
- `settlements_verified_insert_integrity` — INSERT-path gate
- `settlements_verified_update_integrity` — UPDATE-path gate
  (covers the case where a QUARANTINED row gets flipped to VERIFIED
   via UPDATE but still has NULL settlement_value / empty winning_bin)

Both triggers fire ONLY when the NEW row has authority='VERIFIED' and
is missing a required field. QUARANTINED / UNVERIFIED rows with NULL
settlement_value are legitimate (quarantine semantic) and pass through.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema


def _setup() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _insert_sql(*, authority: str, settlement_value, winning_bin) -> tuple[str, tuple]:
    """Build a settlements INSERT with configurable VERIFIED-critical fields."""
    return (
        """
        INSERT INTO settlements (
            city, target_date, winning_bin, settlement_value,
            settlement_source, settled_at, authority, provenance_json,
            temperature_metric, physical_quantity, observation_field, data_version
        ) VALUES (
            'paris', '2026-04-23', ?, ?,
            'test', '2026-04-23T00:00:00', ?, '{"writer": "test"}',
            'high', 'daily_maximum_air_temperature', 'high_temp', 'wu_icao_history_v1'
        )
        """,
        (winning_bin, settlement_value, authority),
    )


# ---------------------------------------------------------------------------
# INSERT-path rejections — VERIFIED row with missing required field
# ---------------------------------------------------------------------------


def test_verified_insert_with_null_settlement_value_rejected():
    conn = _setup()
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=None, winning_bin="15-16"
    )
    with pytest.raises(sqlite3.IntegrityError, match="VERIFIED settlement INSERT requires"):
        conn.execute(sql, params)


def test_verified_insert_with_null_winning_bin_rejected():
    conn = _setup()
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=15.5, winning_bin=None
    )
    with pytest.raises(sqlite3.IntegrityError, match="VERIFIED settlement INSERT requires"):
        conn.execute(sql, params)


def test_verified_insert_with_empty_winning_bin_rejected():
    conn = _setup()
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=15.5, winning_bin=""
    )
    with pytest.raises(sqlite3.IntegrityError, match="VERIFIED settlement INSERT requires"):
        conn.execute(sql, params)


# ---------------------------------------------------------------------------
# INSERT-path acceptance — VERIFIED row with all required fields
# ---------------------------------------------------------------------------


def test_verified_insert_with_all_required_fields_accepted():
    conn = _setup()
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=15.0, winning_bin="15-16"
    )
    conn.execute(sql, params)
    row = conn.execute(
        "SELECT authority, settlement_value, winning_bin FROM settlements WHERE city='paris'"
    ).fetchone()
    assert row == ("VERIFIED", 15.0, "15-16")


# ---------------------------------------------------------------------------
# INSERT-path non-regression — QUARANTINED rows with NULLs still allowed
# ---------------------------------------------------------------------------


def test_quarantined_insert_with_null_settlement_value_accepted():
    """QUARANTINED is the semantic for rows EXCLUDED from authority. A
    QUARANTINED row with NULL settlement_value is legitimate — the row
    records that we saw a settlement event but could not determine the
    canonical value. The trigger MUST NOT fire on such rows."""
    conn = _setup()
    sql, params = _insert_sql(
        authority="QUARANTINED", settlement_value=None, winning_bin="15-16"
    )
    conn.execute(sql, params)
    row = conn.execute(
        "SELECT authority, settlement_value, winning_bin FROM settlements WHERE city='paris'"
    ).fetchone()
    assert row == ("QUARANTINED", None, "15-16")


def test_quarantined_insert_with_all_nulls_accepted():
    """Strongest QUARANTINED form — both settlement_value AND winning_bin
    null. Common for 'we can't even determine the bin' cases."""
    conn = _setup()
    sql, params = _insert_sql(
        authority="QUARANTINED", settlement_value=None, winning_bin=None
    )
    conn.execute(sql, params)


def test_unverified_insert_with_null_settlement_value_accepted():
    """UNVERIFIED (default on row creation before authority resolution)
    should also be permissive — the trigger is VERIFIED-only."""
    conn = _setup()
    sql, params = _insert_sql(
        authority="UNVERIFIED", settlement_value=None, winning_bin=""
    )
    conn.execute(sql, params)


# ---------------------------------------------------------------------------
# UPDATE-path rejections — flipping to VERIFIED with missing fields
# ---------------------------------------------------------------------------


def test_update_quarantined_to_verified_without_settlement_value_rejected():
    """A QUARANTINED row with NULL settlement_value must not become VERIFIED
    via UPDATE while still missing the value. Trigger on UPDATE of
    authority fires BEFORE the authority-monotonic trigger's reactivated_by
    gate, so caller sees the integrity-violation message first."""
    conn = _setup()
    sql, params = _insert_sql(
        authority="QUARANTINED", settlement_value=None, winning_bin="15-16"
    )
    conn.execute(sql, params)
    with pytest.raises(sqlite3.IntegrityError) as exc_info:
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )
    # Either the S2.2 integrity trigger OR the S2.1 authority_monotonic
    # trigger can fire first — both are correct rejections. We accept
    # either message.
    assert (
        "VERIFIED settlement UPDATE requires" in str(exc_info.value)
        or "authority transition forbidden" in str(exc_info.value)
    )


def test_update_verified_settlement_value_to_null_rejected():
    """A VERIFIED row whose settlement_value is set to NULL via UPDATE must
    be rejected. (Downgrades of required fields on VERIFIED rows are
    corruption events.)"""
    conn = _setup()
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=15.0, winning_bin="15-16"
    )
    conn.execute(sql, params)
    with pytest.raises(sqlite3.IntegrityError, match="VERIFIED settlement UPDATE requires"):
        conn.execute(
            "UPDATE settlements SET settlement_value = NULL WHERE city = 'paris'"
        )


def test_update_verified_winning_bin_to_empty_rejected():
    """Similar to above — setting winning_bin='' on a VERIFIED row is a
    structural corruption. Trigger must reject."""
    conn = _setup()
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=15.0, winning_bin="15-16"
    )
    conn.execute(sql, params)
    with pytest.raises(sqlite3.IntegrityError, match="VERIFIED settlement UPDATE requires"):
        conn.execute(
            "UPDATE settlements SET winning_bin = '' WHERE city = 'paris'"
        )


# ---------------------------------------------------------------------------
# Idempotency — DROP+CREATE survives re-init
# ---------------------------------------------------------------------------


def test_triggers_idempotent_on_repeat_init_schema():
    """Re-running init_schema must not raise on existing triggers — the
    DROP IF EXISTS + CREATE pattern must survive multiple calls."""
    conn = _setup()
    init_schema(conn)  # second call
    init_schema(conn)  # third call
    # Confirm triggers still functional
    sql, params = _insert_sql(
        authority="VERIFIED", settlement_value=None, winning_bin="x"
    )
    with pytest.raises(sqlite3.IntegrityError, match="VERIFIED settlement INSERT requires"):
        conn.execute(sql, params)


def test_both_triggers_installed_after_init_schema():
    """Confirm both INSERT + UPDATE triggers are installed — catches
    a future refactor that silently drops one."""
    conn = _setup()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'settlements_verified%' ORDER BY name"
    ).fetchall()
    names = {row[0] for row in rows}
    assert "settlements_verified_insert_integrity" in names
    assert "settlements_verified_update_integrity" in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
