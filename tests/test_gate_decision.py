"""P3 -- GateDecision provenance.

Tests round-trip serialization, reason_refuted conservatism, integration with
is_strategy_enabled, backward-compat with bare-bool gates, and that
set_strategy_gate stores a GateDecision with full provenance.
"""

import sqlite3

import pytest

import src.control.control_plane as cp
from src.control.gate_decision import GateDecision, ReasonCode, reason_refuted


class TestGateDecision:
    def setup_method(self):
        cp._control_state.clear()

    def teardown_method(self):
        cp._control_state.clear()

    def test_gate_decision_round_trip(self):
        """to_dict / from_dict must be an identity transform."""
        original = GateDecision(
            enabled=False,
            reason_code=ReasonCode.EDGE_COMPRESSION,
            reason_snapshot={"brier": 0.31, "window_days": 30},
            gated_at="2026-04-12T03:00:00+00:00",
            gated_by="operator",
        )
        serialised = original.to_dict()
        restored = GateDecision.from_dict(serialised)
        assert restored == original
        assert restored.reason_code is ReasonCode.EDGE_COMPRESSION
        assert restored.reason_snapshot == {"brier": 0.31, "window_days": 30}

    def test_reason_refuted_default_false(self):
        """All ReasonCodes return False in Phase 1 -- conservative un-gate policy."""
        for code in ReasonCode:
            decision = GateDecision(
                enabled=False,
                reason_code=code,
                reason_snapshot={},
                gated_at="2026-04-12T03:00:00+00:00",
                gated_by="operator",
            )
            assert reason_refuted(decision, current_data={}) is False, (
                f"reason_refuted returned True for {code.value} -- should be False in Phase 1"
            )

    def test_is_strategy_enabled_with_gate_decision(self):
        """is_strategy_enabled returns False when GateDecision.enabled is False."""
        decision = GateDecision(
            enabled=False,
            reason_code=ReasonCode.MANUAL_KILL_FOR_LOSSES,
            reason_snapshot={},
            gated_at="2026-04-12T03:00:00+00:00",
            gated_by="operator",
        )
        cp._control_state["strategy_gates"] = {"center_buy": decision.to_dict()}
        assert cp.is_strategy_enabled("center_buy") is False
        assert cp.is_strategy_enabled("opening_inertia") is True  # unknown = enabled

    def test_backward_compat_bool_gate(self):
        """Bare bool from DB refresh converts to GateDecision(UNSPECIFIED) seamlessly."""
        cp._control_state["strategy_gates"] = {
            "center_buy": False,  # disabled
            "opening_inertia": True,  # enabled
        }
        gates = cp.strategy_gates()
        assert isinstance(gates["center_buy"], GateDecision)
        assert gates["center_buy"].enabled is False
        assert gates["center_buy"].reason_code is ReasonCode.UNSPECIFIED
        assert isinstance(gates["opening_inertia"], GateDecision)
        assert gates["opening_inertia"].enabled is True
        assert gates["opening_inertia"].reason_code is ReasonCode.UNSPECIFIED

    def test_set_strategy_gate_stores_decision(self, monkeypatch):
        """set_strategy_gate command stores GateDecision with provenance fields."""
        monkeypatch.setattr(cp, "get_world_connection", lambda: sqlite3.connect(":memory:"))
        cp._control_state["strategy_gates"] = {}

        cmd = {
            "strategy": "center_buy",
            "enabled": False,
            "reason_code": "edge_compression",
            "reason_snapshot": {"brier": 0.31},
            "issued_by": "operator",
        }
        ok, _reason = cp._apply_command("set_strategy_gate", cmd)
        assert ok is True

        # GateDecision stored in-memory as dict before refresh overwrites with bools
        raw = cp._control_state["strategy_gates"]["center_buy"]
        assert isinstance(raw, dict)
        assert raw["enabled"] is False
        assert raw["reason_code"] == "edge_compression"
        assert raw["reason_snapshot"] == {"brier": 0.31}
        assert raw["gated_by"] == "operator"

        # strategy_gates() converts dict -> GateDecision
        gates = cp.strategy_gates()
        assert isinstance(gates["center_buy"], GateDecision)
        assert gates["center_buy"].reason_code is ReasonCode.EDGE_COMPRESSION
        assert gates["center_buy"].enabled is False
