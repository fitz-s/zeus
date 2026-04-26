# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S2;
#                  architecture/invariants.yaml INV-29.
"""P1.S2 command_bus type-contract tests.

Locks the typed surface so P1.S3+ executor work has stable invariants:
  - VenueCommand is frozen (FrozenInstanceError on mutation)
  - IdempotencyKey.from_inputs is deterministic across processes
  - CommandState / CommandEventType / IntentKind are closed enums
  - Repo's _TRANSITIONS dict uses values aligned with these enums
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Manifest registration
# ---------------------------------------------------------------------------


def test_inv29_command_bus_law_registered():
    import yaml
    manifest = yaml.safe_load((ROOT / "architecture/invariants.yaml").read_text())
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-29" in by_id, "INV-29 missing from invariants.yaml"
    assert by_id["INV-29"].get("enforced_by"), "INV-29 must declare enforced_by"


# ---------------------------------------------------------------------------
# VenueCommand frozen
# ---------------------------------------------------------------------------


class TestVenueCommandFrozen:
    def _make(self):
        from src.execution.command_bus import (
            CommandState, IdempotencyKey, IntentKind, VenueCommand,
        )
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-1", token_id="tok-1", side="BUY",
            price=0.5, size=10.0, intent_kind=IntentKind.ENTRY,
        )
        return VenueCommand(
            command_id="cmd-1",
            position_id="pos-1",
            decision_id="dec-1",
            idempotency_key=idem,
            intent_kind=IntentKind.ENTRY,
            market_id="m1",
            token_id="tok-1",
            side="BUY",
            size=10.0,
            price=0.5,
            state=CommandState.INTENT_CREATED,
        )

    def test_venuecommand_is_frozen(self):
        cmd = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cmd.command_id = "mutated"  # type: ignore[misc]

    def test_venuecommand_state_must_be_enum(self):
        from src.execution.command_bus import (
            IdempotencyKey, IntentKind, VenueCommand,
        )
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-1", token_id="tok-1", side="BUY",
            price=0.5, size=10.0, intent_kind=IntentKind.ENTRY,
        )
        with pytest.raises(TypeError, match="state must be CommandState"):
            VenueCommand(
                command_id="cmd-1", position_id="pos-1", decision_id="dec-1",
                idempotency_key=idem, intent_kind=IntentKind.ENTRY,
                market_id="m1", token_id="tok-1", side="BUY",
                size=10.0, price=0.5,
                state="INTENT_CREATED",  # type: ignore[arg-type]
            )

    def test_venuecommand_intent_kind_must_be_enum(self):
        from src.execution.command_bus import (
            CommandState, IdempotencyKey, VenueCommand,
        )
        idem = IdempotencyKey(value="0" * 32)
        with pytest.raises(TypeError, match="intent_kind must be IntentKind"):
            VenueCommand(
                command_id="cmd-1", position_id="pos-1", decision_id="dec-1",
                idempotency_key=idem, intent_kind="ENTRY",  # type: ignore[arg-type]
                market_id="m1", token_id="tok-1", side="BUY",
                size=10.0, price=0.5,
                state=CommandState.INTENT_CREATED,
            )

    def test_venuecommand_side_must_be_buy_or_sell(self):
        from src.execution.command_bus import (
            CommandState, IdempotencyKey, IntentKind, VenueCommand,
        )
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-1", token_id="tok-1", side="BUY",
            price=0.5, size=10.0, intent_kind=IntentKind.ENTRY,
        )
        with pytest.raises(ValueError, match="must be 'BUY' or 'SELL'"):
            VenueCommand(
                command_id="cmd-1", position_id="pos-1", decision_id="dec-1",
                idempotency_key=idem, intent_kind=IntentKind.ENTRY,
                market_id="m1", token_id="tok-1", side="LONG",
                size=10.0, price=0.5,
                state=CommandState.INTENT_CREATED,
            )


# ---------------------------------------------------------------------------
# IdempotencyKey determinism
# ---------------------------------------------------------------------------


class TestIdempotencyKeyDeterministic:
    def test_same_inputs_produce_same_key(self):
        from src.execution.command_bus import IdempotencyKey, IntentKind
        kwargs = dict(
            decision_id="dec-1", token_id="tok-1", side="BUY",
            price=0.5, size=10.0, intent_kind=IntentKind.ENTRY,
        )
        k1 = IdempotencyKey.from_inputs(**kwargs)
        k2 = IdempotencyKey.from_inputs(**kwargs)
        assert k1.value == k2.value
        assert len(k1.value) == 32

    def test_different_decision_id_produces_different_key(self):
        from src.execution.command_bus import IdempotencyKey, IntentKind
        a = IdempotencyKey.from_inputs(
            decision_id="dec-1", token_id="t", side="BUY", price=0.5, size=1.0,
            intent_kind=IntentKind.ENTRY,
        )
        b = IdempotencyKey.from_inputs(
            decision_id="dec-2", token_id="t", side="BUY", price=0.5, size=1.0,
            intent_kind=IntentKind.ENTRY,
        )
        assert a.value != b.value

    def test_price_precision_absorbs_float_noise(self):
        """0.5 and 0.5 + 1e-9 round to the same 4-decimal price; idempotency
        key must NOT change for that tiny noise. But 0.5 vs 0.51 must differ.
        """
        from src.execution.command_bus import IdempotencyKey, IntentKind
        kwargs = dict(decision_id="dec", token_id="t", side="BUY", size=1.0,
                      intent_kind=IntentKind.ENTRY)
        a = IdempotencyKey.from_inputs(price=0.5, **kwargs)
        b = IdempotencyKey.from_inputs(price=0.5 + 1e-9, **kwargs)
        c = IdempotencyKey.from_inputs(price=0.51, **kwargs)
        assert a.value == b.value, "Sub-0.0001 noise must not change the key"
        assert a.value != c.value, "0.5 vs 0.51 must produce different keys"

    def test_intent_kind_must_be_enum(self):
        from src.execution.command_bus import IdempotencyKey
        with pytest.raises(TypeError, match="intent_kind must be IntentKind"):
            IdempotencyKey.from_inputs(
                decision_id="dec-1", token_id="tok-1", side="BUY",
                price=0.5, size=10.0, intent_kind="ENTRY",  # type: ignore[arg-type]
            )

    def test_side_validated(self):
        from src.execution.command_bus import IdempotencyKey, IntentKind
        with pytest.raises(ValueError, match="must be 'BUY' or 'SELL'"):
            IdempotencyKey.from_inputs(
                decision_id="dec-1", token_id="tok-1", side="LONG",
                price=0.5, size=10.0, intent_kind=IntentKind.ENTRY,
            )

    def test_value_format_validated(self):
        from src.execution.command_bus import IdempotencyKey
        with pytest.raises(TypeError):
            IdempotencyKey(value=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="32 hex chars"):
            IdempotencyKey(value="too-short")

    def test_key_stable_across_separate_processes(self, tmp_path):
        """The strongest determinism test: two separate Python processes
        compute the same key. Catches any nondeterminism from hash randomization,
        dict ordering, etc. (sha256 + explicit canonicalization should be safe.)
        """
        snippet = textwrap.dedent("""
            from src.execution.command_bus import IdempotencyKey, IntentKind
            k = IdempotencyKey.from_inputs(
                decision_id="dec-X", token_id="tok-Y", side="SELL",
                price=0.7321, size=42.0, intent_kind=IntentKind.EXIT,
            )
            print(k.value)
        """)
        a = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                           text=True, cwd=str(ROOT), check=True).stdout.strip()
        b = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                           text=True, cwd=str(ROOT), check=True).stdout.strip()
        assert a == b
        assert len(a) == 32


# ---------------------------------------------------------------------------
# Closed enum grammars
# ---------------------------------------------------------------------------


class TestEnumsAreClosed:
    def test_command_state_grammar_is_closed(self):
        from src.execution.command_bus import CommandState
        with pytest.raises(ValueError):
            CommandState("BANANA")

    def test_command_event_type_grammar_is_closed(self):
        from src.execution.command_bus import CommandEventType
        with pytest.raises(ValueError):
            CommandEventType("BANANA")

    def test_intent_kind_grammar_is_closed(self):
        from src.execution.command_bus import IntentKind
        with pytest.raises(ValueError):
            IntentKind("BANANA")

    def test_command_state_count(self):
        """11 states per implementation_plan.md §P1.S2."""
        from src.execution.command_bus import CommandState
        assert len(list(CommandState)) == 11

    def test_command_event_type_count(self):
        from src.execution.command_bus import CommandEventType
        assert len(list(CommandEventType)) == 11

    def test_intent_kind_count(self):
        from src.execution.command_bus import IntentKind
        assert len(list(IntentKind)) == 4


# ---------------------------------------------------------------------------
# Repo / type alignment — no drift
# ---------------------------------------------------------------------------


class TestRepoTypeAlignment:
    """Ensures `src/state/venue_command_repo.py::_TRANSITIONS` uses string
    values that match the new enum surface. Drift caught here means a future
    edit to one file or the other has lost lockstep with its peer.
    """

    def test_transitions_state_keys_are_valid_command_states(self):
        from src.execution.command_bus import CommandState
        from src.state.venue_command_repo import _TRANSITIONS
        valid = {s.value for s in CommandState}
        for (state, _event), state_after in _TRANSITIONS.items():
            assert state in valid, f"Repo transition uses unknown state key: {state!r}"
            assert state_after in valid, f"Repo transition produces unknown state: {state_after!r}"

    def test_transitions_event_keys_are_valid_command_events(self):
        from src.execution.command_bus import CommandEventType
        from src.state.venue_command_repo import _TRANSITIONS
        valid = {e.value for e in CommandEventType}
        for (_state, event), _state_after in _TRANSITIONS.items():
            assert event in valid, f"Repo transition uses unknown event: {event!r}"

    def test_inflight_states_match_repo_unresolved_filter(self):
        """IN_FLIGHT_STATES must be the exact set the repo's
        find_unresolved_commands SELECT WHERE clause filters on."""
        from pathlib import Path
        from src.execution.command_bus import IN_FLIGHT_STATES
        repo_src = (ROOT / "src/state/venue_command_repo.py").read_text()
        for s in IN_FLIGHT_STATES:
            assert f"'{s.value}'" in repo_src, (
                f"IN_FLIGHT_STATES {s.value} missing from repo's unresolved filter — drift"
            )
