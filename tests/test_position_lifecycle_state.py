# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T1 acceptance criteria
"""Tests for Position.lifecycle_state — Phase 7 T1.

Acceptance criteria:
  - Position() defaults lifecycle_state to UNRESOLVED.
  - asdict → JSON → load → Position round-trip produces SettlementOutcome instance (NOT raw int).
  - Backward-compat: v1-vintage positions.json (no lifecycle_state) loads with UNRESOLVED default.
  - Coercion guard: integer lifecycle_state reconstructs to SettlementOutcome enum.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from src.contracts.settlement_outcome import SettlementOutcome
from src.state.portfolio import Position


# Minimal required fields for Position construction
_BASE_FIELDS = {
    "trade_id": "trade-lc-001",
    "market_id": "mkt-lc-001",
    "city": "Chicago",
    "cluster": "Test",
    "target_date": "2026-07-04",
    "bin_label": "80-90°F",
    "direction": "buy_yes",
    "temperature_metric": "high",
    "env": "test",
    "state": "holding",
}


def _make_position(**overrides) -> Position:
    return Position(**{**_BASE_FIELDS, **overrides})


class TestPositionLifecycleStateDefault:
    def test_default_is_unresolved(self):
        pos = _make_position()
        assert pos.lifecycle_state == SettlementOutcome.UNRESOLVED

    def test_default_is_enum_instance(self):
        pos = _make_position()
        assert isinstance(pos.lifecycle_state, SettlementOutcome)


class TestPositionLifecycleStateRoundTrip:
    def test_asdict_json_reload_is_enum(self):
        """asdict → json.dumps → json.loads → Position must restore SettlementOutcome."""
        pos = _make_position(lifecycle_state=SettlementOutcome.PHYSICALLY_CONFIRMED)
        raw_dict = dataclasses.asdict(pos)
        json_str = json.dumps(raw_dict)
        loaded_dict = json.loads(json_str)

        # Reconstruct via reflection (same path as portfolio load)
        pos_fields = {f.name for f in dataclasses.fields(Position)}
        filtered = {k: v for k, v in loaded_dict.items() if k in pos_fields}
        pos2 = Position(**filtered)

        assert isinstance(pos2.lifecycle_state, SettlementOutcome), (
            f"Expected SettlementOutcome instance after round-trip, got {type(pos2.lifecycle_state)}"
        )
        assert pos2.lifecycle_state == SettlementOutcome.PHYSICALLY_CONFIRMED

    def test_roundtrip_all_members(self):
        """Every SettlementOutcome value survives asdict → json → Position round-trip."""
        for member in SettlementOutcome:
            pos = _make_position(lifecycle_state=member)
            raw = dataclasses.asdict(pos)
            js = json.loads(json.dumps(raw))
            pos_fields = {f.name for f in dataclasses.fields(Position)}
            filtered = {k: v for k, v in js.items() if k in pos_fields}
            reloaded = Position(**filtered)
            assert reloaded.lifecycle_state == member, (
                f"Round-trip failed for {member.name}"
            )
            assert isinstance(reloaded.lifecycle_state, SettlementOutcome)

    def test_integer_coercion_in_post_init(self):
        """Position constructed with raw int lifecycle_state is coerced to SettlementOutcome."""
        pos = _make_position(lifecycle_state=3)  # VENUE_RESOLVED_WIN
        assert isinstance(pos.lifecycle_state, SettlementOutcome)
        assert pos.lifecycle_state == SettlementOutcome.VENUE_RESOLVED_WIN


class TestPositionLifecycleStateBackwardCompat:
    def test_v1_vintage_no_lifecycle_state_field(self):
        """v1-vintage position dict without lifecycle_state loads with UNRESOLVED."""
        pos_fields = {f.name for f in dataclasses.fields(Position)}
        # Simulate v1-vintage JSON payload — no lifecycle_state key
        raw = {k: v for k, v in _BASE_FIELDS.items() if k in pos_fields}
        assert "lifecycle_state" not in raw
        pos = Position(**raw)
        assert pos.lifecycle_state == SettlementOutcome.UNRESOLVED
        assert isinstance(pos.lifecycle_state, SettlementOutcome)
