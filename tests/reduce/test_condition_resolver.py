# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-b -- position (condition_id, outcome_index) resolution for the
#   materializer. Convention validated by prior analysis (see
#   src/reduce/condition_resolver.py module docstring): 411/411 clean
#   matches against independent world settlement joins.
"""Tests for src.reduce.condition_resolver: convention + refusal matrix."""
from __future__ import annotations

import pytest

from src.reduce.condition_resolver import (
    ConditionResolutionRefusal,
    MissingConditionIdError,
    PositionNotFoundError,
    UnrecognizedDirectionError,
    resolve_condition_outcome,
)
from tests.reduce.conftest import insert_position_current

CONDITION = "0xabc123"


class TestConvention:
    def test_buy_yes_resolves_to_outcome_index_0(self, conn):
        insert_position_current(
            conn, position_id="p1", condition_id=CONDITION, direction="buy_yes"
        )

        resolution = resolve_condition_outcome(conn, "p1")

        assert resolution.position_id == "p1"
        assert resolution.condition_id == CONDITION
        assert resolution.outcome_index == 0
        assert resolution.direction == "buy_yes"

    def test_buy_no_resolves_to_outcome_index_1(self, conn):
        insert_position_current(
            conn, position_id="p1", condition_id=CONDITION, direction="buy_no"
        )

        resolution = resolve_condition_outcome(conn, "p1")

        assert resolution.condition_id == CONDITION
        assert resolution.outcome_index == 1
        assert resolution.direction == "buy_no"


class TestRefusalMatrix:
    """Each case names the exact missing/unusable input -- fail-closed is a
    feature (mirrors src.reduce.position_economics.ReducerRefusal style):
    never guess a (condition_id, outcome_index) pair."""

    def test_position_not_found_refuses(self, conn):
        with pytest.raises(PositionNotFoundError, match="p-missing"):
            resolve_condition_outcome(conn, "p-missing")

    def test_missing_condition_id_refuses(self, conn):
        insert_position_current(
            conn, position_id="p1", condition_id=None, direction="buy_yes"
        )

        with pytest.raises(MissingConditionIdError, match="p1"):
            resolve_condition_outcome(conn, "p1")

    def test_blank_condition_id_refuses(self, conn):
        """An empty/whitespace condition_id is exactly as unusable as NULL --
        never treated as a legitimate value."""
        insert_position_current(
            conn, position_id="p1", condition_id="   ", direction="buy_yes"
        )

        with pytest.raises(MissingConditionIdError):
            resolve_condition_outcome(conn, "p1")

    def test_null_direction_refuses(self, conn):
        insert_position_current(
            conn, position_id="p1", condition_id=CONDITION, direction=None
        )

        with pytest.raises(UnrecognizedDirectionError, match="p1"):
            resolve_condition_outcome(conn, "p1")

    def test_unknown_direction_literal_refuses(self, conn):
        """direction='unknown' is a legal position_current CHECK value but
        carries no (condition_id, outcome_index) mapping -- refuse, don't
        default to either side."""
        insert_position_current(
            conn, position_id="p1", condition_id=CONDITION, direction="unknown"
        )

        with pytest.raises(UnrecognizedDirectionError, match="unknown"):
            resolve_condition_outcome(conn, "p1")

    def test_all_refusals_are_condition_resolution_refusal_subclasses(self, conn):
        assert issubclass(PositionNotFoundError, ConditionResolutionRefusal)
        assert issubclass(MissingConditionIdError, ConditionResolutionRefusal)
        assert issubclass(UnrecognizedDirectionError, ConditionResolutionRefusal)
