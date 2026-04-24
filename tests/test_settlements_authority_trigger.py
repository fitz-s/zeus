# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: S2.1 data-readiness hardening tail
# (docs/operations/task_2026-04-23_midstream_remediation/); closure-banner
# item 4 of data-readiness workstream — reactivated_by value-validation
# in settlements_authority_monotonic trigger. Prevents
# presence-only-bypass (false / 0 / empty-string / object / array).

"""Antibody for `settlements_authority_monotonic` trigger hardening.

Pre-S2.1 trigger rejected `QUARANTINED->VERIFIED` transitions only when
`provenance_json.reactivated_by` was NULL or missing. A non-null but
semantically-empty value (boolean `false`, integer `0`, empty string
`""`, JSON object `{}`, JSON array `[]`) silently satisfied the
`IS NOT NULL` check, allowing the reactivation to proceed with no
accountable operator identity.

Post-S2.1 trigger additionally rejects:
- `reactivated_by` not a text value (`json_type != 'text'`)
- `reactivated_by` empty string (`length == 0`)

Only a non-empty text value satisfies the trigger. This closes the
"presence-only" bypass category per Fitz K<<N (structural invariant on
trigger, not on the caller).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.state.db import init_schema


def _insert_quarantined_row(conn: sqlite3.Connection, provenance: dict | None) -> None:
    """Seed a QUARANTINED settlement row that we'll then try to update."""
    conn.execute(
        """
        INSERT INTO settlements (
            city, target_date, winning_bin, settlement_value,
            settlement_source, settled_at, authority, provenance_json,
            temperature_metric, physical_quantity, observation_field, data_version
        ) VALUES (
            'paris', '2026-04-23', '15-16', 15.5,
            'test', '2026-04-23T00:00:00', 'QUARANTINED', ?,
            'high', 'daily_maximum_air_temperature', 'high_temp', 'wu_icao_history_v1'
        )
        """,
        (json.dumps(provenance) if provenance is not None else None,),
    )


def _setup() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Rejection antibodies — each of these BYPASSES was allowed pre-S2.1
# ---------------------------------------------------------------------------


def test_reactivation_rejected_when_reactivated_by_is_false():
    """Boolean false passes IS NOT NULL but fails json_type != 'text'."""
    conn = _setup()
    _insert_quarantined_row(conn, {"reactivated_by": False, "note": "bypass"})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


def test_reactivation_rejected_when_reactivated_by_is_integer_zero():
    """Integer 0 passes IS NOT NULL but fails json_type != 'text'."""
    conn = _setup()
    _insert_quarantined_row(conn, {"reactivated_by": 0})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


def test_reactivation_rejected_when_reactivated_by_is_empty_string():
    """Empty string has json_type == 'text' but length == 0."""
    conn = _setup()
    _insert_quarantined_row(conn, {"reactivated_by": ""})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


def test_reactivation_rejected_when_reactivated_by_is_object():
    """JSON object passes IS NOT NULL but has json_type == 'object'."""
    conn = _setup()
    _insert_quarantined_row(conn, {"reactivated_by": {"who": "operator"}})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


def test_reactivation_rejected_when_reactivated_by_is_array():
    """JSON array passes IS NOT NULL but has json_type == 'array'."""
    conn = _setup()
    _insert_quarantined_row(conn, {"reactivated_by": ["operator"]})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


# ---------------------------------------------------------------------------
# Backward-compat — these rejections were already correct pre-S2.1
# ---------------------------------------------------------------------------


def test_reactivation_rejected_when_reactivated_by_missing():
    """Missing key — already covered by pre-S2.1 trigger."""
    conn = _setup()
    _insert_quarantined_row(conn, {"other_key": "value"})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


def test_reactivation_rejected_when_reactivated_by_is_null():
    """JSON null — already covered by pre-S2.1 trigger (IS NULL check)."""
    conn = _setup()
    _insert_quarantined_row(conn, {"reactivated_by": None})
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


def test_reactivation_rejected_when_provenance_json_null():
    """provenance_json itself NULL — already covered."""
    conn = _setup()
    _insert_quarantined_row(conn, None)
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


# ---------------------------------------------------------------------------
# Acceptance path — a real operator identity string must work
# ---------------------------------------------------------------------------


def test_reactivation_accepted_when_reactivated_by_is_non_empty_text():
    """The only valid reactivation — text value with length > 0."""
    conn = _setup()
    _insert_quarantined_row(
        conn,
        {
            "reactivated_by": "operator_audit_2026-04-23",
            "rationale": "post-hoc correction",
        },
    )
    conn.execute(
        "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
    )
    row = conn.execute(
        "SELECT authority FROM settlements WHERE city = 'paris'"
    ).fetchone()
    assert row[0] == "VERIFIED"


def test_verified_to_unverified_always_rejected():
    """The other half of the authority-monotonic contract — pinned for safety."""
    conn = _setup()
    conn.execute(
        """
        INSERT INTO settlements (
            city, target_date, winning_bin, settlement_value,
            settlement_source, settled_at, authority, provenance_json,
            temperature_metric, physical_quantity, observation_field, data_version
        ) VALUES (
            'paris', '2026-04-23', '15-16', 15.5,
            'test', '2026-04-23T00:00:00', 'VERIFIED', '{}',
            'high', 'daily_maximum_air_temperature', 'high_temp', 'wu_icao_history_v1'
        )
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'UNVERIFIED' WHERE city = 'paris'"
        )


def test_trigger_drop_and_recreate_is_idempotent():
    """Re-running init_schema must not raise on existing trigger — the DROP
    IF EXISTS + CREATE pattern must survive multiple calls."""
    conn = _setup()
    init_schema(conn)  # second call
    init_schema(conn)  # third call — should still succeed
    # Confirm trigger exists and is functional
    conn.execute(
        """
        INSERT INTO settlements (
            city, target_date, winning_bin, settlement_value,
            settlement_source, settled_at, authority, provenance_json,
            temperature_metric, physical_quantity, observation_field, data_version
        ) VALUES (
            'paris', '2026-04-23', '15-16', 15.5,
            'test', '2026-04-23T00:00:00', 'QUARANTINED', '{"reactivated_by": false}',
            'high', 'daily_maximum_air_temperature', 'high_temp', 'wu_icao_history_v1'
        )
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="authority transition forbidden"):
        conn.execute(
            "UPDATE settlements SET authority = 'VERIFIED' WHERE city = 'paris'"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
