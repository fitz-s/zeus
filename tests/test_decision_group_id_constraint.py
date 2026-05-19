# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.1, topology packet "phase0-pr4-decision-group-id"
"""R-4.1: DecisionGroupId NOT NULL constraint tests.

Test plan:
    T1: SQLite schema enforces NOT NULL — INSERT with NULL decision_group_id
        must raise IntegrityError (via trigger).
    T2: Schema enforces NOT NULL — INSERT with empty-string decision_group_id
        must succeed (empty string is not NULL in SQLite).
    T3: decision_group_id_v1_hash() returns a DecisionGroupId (str subtype),
        not None, not empty.
    T4: Round-trip: decision_group_id_v1_hash() is deterministic —
        same args always return identical value.
    T5: decision_group_id_v1_hash() raises ValueError for missing source_id.
"""

import sqlite3

import pytest

from src.contracts.decision_group_id import decision_group_id_v1_hash

_FULL_KWARGS = dict(
    market_id="0xabc",
    target_date="2026-06-01",
    forecast_available_at="2026-05-25T12:00:00",
    source_id="tigge_mars",
    data_version="v2.3",
    bin_index=0,
    lead_days_bucket=1,
)


def test_decision_group_id_v1_hash_raises_value_error_on_empty_market_id():
    """LIVE: empty market_id raises ValueError — input validation contract is pinned."""
    with pytest.raises(ValueError, match="market_id"):
        decision_group_id_v1_hash(**{**_FULL_KWARGS, "market_id": ""})


def test_decision_group_id_v1_hash_raises_value_error_on_negative_bin_index():
    """LIVE: negative bin_index raises ValueError."""
    with pytest.raises(ValueError, match="bin_index"):
        decision_group_id_v1_hash(**{**_FULL_KWARGS, "bin_index": -1})


def test_decision_group_id_v1_hash_raises_value_error_on_zero_lead_days():
    """LIVE: lead_days_bucket <= 0 raises ValueError."""
    with pytest.raises(ValueError, match="lead_days_bucket"):
        decision_group_id_v1_hash(**{**_FULL_KWARGS, "lead_days_bucket": 0})


def _make_trigger_db(tmp_path):
    """Create a minimal calibration_pairs_v2 fixture with NOT NULL triggers."""
    db = tmp_path / "fixture.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE calibration_pairs_v2 (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            decision_group_id TEXT
        )
    """)
    conn.execute("""
        CREATE TRIGGER calibration_pairs_v2_dgid_not_null_ins
        BEFORE INSERT ON calibration_pairs_v2
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: calibration_pairs_v2.decision_group_id');
        END
    """)
    conn.execute("""
        CREATE TRIGGER calibration_pairs_v2_dgid_not_null_upd
        BEFORE UPDATE OF decision_group_id ON calibration_pairs_v2
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: calibration_pairs_v2.decision_group_id');
        END
    """)
    conn.commit()
    return conn


def test_not_null_constraint_rejects_null_decision_group_id(tmp_path):
    """T1: INSERT with NULL decision_group_id must raise IntegrityError (trigger enforced)."""
    conn = _make_trigger_db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO calibration_pairs_v2 (city, decision_group_id) VALUES (?, ?)",
            ("Chicago", None),
        )
    conn.close()


def test_not_null_constraint_allows_empty_string(tmp_path):
    """T2: Empty-string decision_group_id is NOT NULL — must succeed."""
    conn = _make_trigger_db(tmp_path)
    conn.execute(
        "INSERT INTO calibration_pairs_v2 (city, decision_group_id) VALUES (?, ?)",
        ("Chicago", ""),
    )
    conn.commit()
    row = conn.execute("SELECT decision_group_id FROM calibration_pairs_v2").fetchone()
    assert row[0] == ""
    conn.close()


def test_decision_group_id_v1_hash_returns_nonempty_string():
    """T3: decision_group_id_v1_hash() returns a non-empty str (DecisionGroupId)."""
    result = decision_group_id_v1_hash(**_FULL_KWARGS)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result.startswith("dgid_v1_")


def test_decision_group_id_v1_hash_is_deterministic():
    """T4: Same args always return identical DecisionGroupId."""
    a = decision_group_id_v1_hash(**_FULL_KWARGS)
    b = decision_group_id_v1_hash(**_FULL_KWARGS)
    assert a == b


def test_decision_group_id_v1_hash_raises_on_invalid_args():
    """T5: Empty source_id raises ValueError — distinct from T1/T2/T3 validation cases."""
    with pytest.raises(ValueError, match="source_id"):
        decision_group_id_v1_hash(**{**_FULL_KWARGS, "source_id": ""})
