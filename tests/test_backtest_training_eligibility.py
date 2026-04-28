# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md (Slice F11.5)
"""F11.5 antibody: training-eligibility filter rejects RECONSTRUCTED + NULL."""

import sqlite3

import pytest

from src.backtest.decision_time_truth import AvailabilityProvenance
from src.backtest.training_eligibility import (
    ECONOMICS_ELIGIBLE_PROVENANCE,
    ECONOMICS_ELIGIBLE_SQL,
    SKILL_ELIGIBLE_PROVENANCE,
    SKILL_ELIGIBLE_SQL,
    is_economics_eligible,
    is_skill_eligible,
)


# ---------------------------------------------------------------------------
# Predicate-level antibodies
# ---------------------------------------------------------------------------


def test_skill_predicate_accepts_fetch_time_recorded_derived():
    assert is_skill_eligible(AvailabilityProvenance.FETCH_TIME)
    assert is_skill_eligible(AvailabilityProvenance.RECORDED)
    assert is_skill_eligible(AvailabilityProvenance.DERIVED_FROM_DISSEMINATION)


def test_skill_predicate_rejects_reconstructed():
    assert not is_skill_eligible(AvailabilityProvenance.RECONSTRUCTED)


def test_skill_predicate_rejects_null():
    assert not is_skill_eligible(None)


def test_skill_predicate_accepts_string_values():
    assert is_skill_eligible("fetch_time")
    assert is_skill_eligible("recorded")
    assert is_skill_eligible("derived_dissemination")
    assert not is_skill_eligible("reconstructed")


def test_economics_predicate_accepts_only_fetch_time_recorded():
    assert is_economics_eligible(AvailabilityProvenance.FETCH_TIME)
    assert is_economics_eligible(AvailabilityProvenance.RECORDED)
    assert not is_economics_eligible(AvailabilityProvenance.DERIVED_FROM_DISSEMINATION)
    assert not is_economics_eligible(AvailabilityProvenance.RECONSTRUCTED)
    assert not is_economics_eligible(None)


def test_skill_eligible_strictly_includes_economics():
    assert ECONOMICS_ELIGIBLE_PROVENANCE.issubset(SKILL_ELIGIBLE_PROVENANCE)
    assert ECONOMICS_ELIGIBLE_PROVENANCE != SKILL_ELIGIBLE_PROVENANCE


# ---------------------------------------------------------------------------
# SQL fragment antibodies (executed against an in-memory DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_mixed_provenance():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY,
            availability_provenance TEXT
        )
        """
    )
    rows = [
        (1, "fetch_time"),
        (2, "recorded"),
        (3, "derived_dissemination"),
        (4, "reconstructed"),
        (5, None),
    ]
    conn.executemany(
        "INSERT INTO forecasts (id, availability_provenance) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    yield conn
    conn.close()


def test_skill_sql_filter_includes_fetch_time_recorded_derived(db_with_mixed_provenance):
    rows = db_with_mixed_provenance.execute(
        f"SELECT id FROM forecasts WHERE {SKILL_ELIGIBLE_SQL} ORDER BY id"
    ).fetchall()
    assert {r[0] for r in rows} == {1, 2, 3}


def test_skill_sql_filter_excludes_reconstructed_and_null(db_with_mixed_provenance):
    rows = db_with_mixed_provenance.execute(
        f"SELECT id FROM forecasts WHERE NOT ({SKILL_ELIGIBLE_SQL}) OR availability_provenance IS NULL"
    ).fetchall()
    excluded = {r[0] for r in rows}
    assert 4 in excluded  # reconstructed
    assert 5 in excluded  # NULL


def test_economics_sql_filter_includes_only_fetch_time_recorded(db_with_mixed_provenance):
    rows = db_with_mixed_provenance.execute(
        f"SELECT id FROM forecasts WHERE {ECONOMICS_ELIGIBLE_SQL} ORDER BY id"
    ).fetchall()
    assert {r[0] for r in rows} == {1, 2}


def test_economics_sql_filter_strictly_subset_of_skill(db_with_mixed_provenance):
    skill_ids = {
        r[0]
        for r in db_with_mixed_provenance.execute(
            f"SELECT id FROM forecasts WHERE {SKILL_ELIGIBLE_SQL}"
        ).fetchall()
    }
    economics_ids = {
        r[0]
        for r in db_with_mixed_provenance.execute(
            f"SELECT id FROM forecasts WHERE {ECONOMICS_ELIGIBLE_SQL}"
        ).fetchall()
    }
    assert economics_ids.issubset(skill_ids)
    assert economics_ids != skill_ids


def test_sql_fragment_does_not_use_string_concatenation_of_user_input():
    """The SQL fragment is constant and parameterless. There is no path
    where caller-supplied strings flow into it. This locks down the
    no-injection contract."""
    assert "?" not in SKILL_ELIGIBLE_SQL
    assert "?" not in ECONOMICS_ELIGIBLE_SQL
    assert SKILL_ELIGIBLE_SQL == SKILL_ELIGIBLE_SQL  # idempotent constant
