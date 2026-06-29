# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   A5 PositionPhase projection; consult ruling thread 6a42bc3d (authority-aware reducer).

"""A5 equivalence antibody — authority-aware derive_position_phase vs the live owner.

`phase_for_runtime_position` (lifecycle_manager) is the canonical live projection: a
single-state-string dispatcher giving the primary `state` field authority, with
`chain_state`/`exit_state` only as FALLBACKS that fire when the primary state has not
already reached a stronger economic/exit/terminal disposition.

The redesign's `derive_position_phase` is now authority-aware (consult 6a42bc3d
ruling): it splits quarantine into explicit A5 quarantine vs A7 chain-quarantine
FALLBACK, and pending-exit into explicit A5 pending-exit vs exit-state FALLBACK, with
the fallbacks ranked BELOW economic-close / explicit-pending-exit. That makes it an
EXACT decomposition of the live owner over the whole input domain — the previously
"sanctioned" divergence (economically_closed/pending_exit + chain-quarantine being
re-quarantined) is gone, because A5 authority now wins.

This antibody proves 192/192 exact equivalence, so the authority-aware reducer is a
behavior-preserving basis for a future A5 cutover. (No writer is cut over here.)
"""

from __future__ import annotations

from itertools import product

import pytest

from src.contracts.canonical_lifecycle import PositionPhase
from src.state.canonical_projections import derive_position_phase
from src.state.lifecycle_manager import (
    PENDING_EXIT_RUNTIME_STATES,
    derive_runtime_position_phase,
    phase_for_runtime_position,
)

# Full runtime-state domain of phase_for_runtime_position + the fallback inputs.
_RUNTIME_STATES = [
    "voided", "settled", "economically_closed", "admin_closed", "quarantined",
    "pending_exit", "pending_tracked", "day0_window", "entered", "holding",
    "garbage_x", "",
]
_EXIT_STATES = ["", "exit_intent", "sell_pending", "backoff_exhausted"]
_CHAIN_STATES = ["", "quarantined", "quarantine_expired", "exit_pending_missing"]


def _runtime_state_to_phase_facts(state: str, exit_state: str = "", chain_state: str = "") -> dict:
    """Map the runtime (state, exit_state, chain_state) onto the authority-aware
    derive_position_phase inputs, replicating the live owner's authority model:
    the primary `state` value sets exactly one EXPLICIT A5 fact; chain-quarantine and
    exit-state set FALLBACK facts that project the phase only when no stronger A5
    authority is present."""
    s = str(state or "").strip().lower()
    es = str(exit_state or "").strip().lower()
    cs = str(chain_state or "").strip().lower()
    return dict(
        has_admin_close=(s == "admin_closed"),
        is_voided=(s == "voided"),
        has_settlement=(s == "settled"),
        has_economic_close=(s == "economically_closed"),
        has_explicit_quarantine=(s == "quarantined"),
        has_explicit_pending_exit=(s == "pending_exit"),
        has_chain_quarantine_fallback=(cs in {"quarantined", "quarantine_expired"}),
        has_exit_fallback=(es in PENDING_EXIT_RUNTIME_STATES or cs == "exit_pending_missing"),
        has_positive_exposure=(s in {"entered", "holding", "day0_window"}),
        in_day0_window=(s == "day0_window"),
        has_entry_intent=(s == "pending_tracked"),
    )


# --- single-fact legibility: each runtime state maps to the expected phase ------ #

@pytest.mark.parametrize("state,expected", [
    ("voided", PositionPhase.VOIDED),
    ("settled", PositionPhase.SETTLED),
    ("economically_closed", PositionPhase.ECONOMICALLY_CLOSED),
    ("admin_closed", PositionPhase.ADMIN_CLOSED),
    ("quarantined", PositionPhase.QUARANTINED),
    ("pending_exit", PositionPhase.PENDING_EXIT),
    ("pending_tracked", PositionPhase.PENDING_ENTRY),
    ("day0_window", PositionPhase.DAY0_WINDOW),
    ("entered", PositionPhase.ACTIVE),
    ("holding", PositionPhase.ACTIVE),
    ("garbage_x", PositionPhase.UNKNOWN),
])
def test_single_state_derive_matches_live_owner(state: str, expected: PositionPhase) -> None:
    live = phase_for_runtime_position(state=state)
    derived = derive_position_phase(**_runtime_state_to_phase_facts(state))
    assert live is expected
    assert derived is expected


# --- the ruling's explicit edge fixtures (state authority vs chain fallback) ----- #

@pytest.mark.parametrize("state,chain,expected", [
    ("economically_closed", "quarantined", PositionPhase.ECONOMICALLY_CLOSED),
    ("economically_closed", "quarantine_expired", PositionPhase.ECONOMICALLY_CLOSED),
    ("pending_exit", "quarantined", PositionPhase.PENDING_EXIT),
    ("pending_exit", "quarantine_expired", PositionPhase.PENDING_EXIT),
    ("entered", "quarantined", PositionPhase.QUARANTINED),
    ("day0_window", "quarantined", PositionPhase.QUARANTINED),
    ("pending_tracked", "quarantined", PositionPhase.QUARANTINED),
])
def test_state_authority_vs_chain_quarantine_fallback(state: str, chain: str, expected: PositionPhase) -> None:
    live = phase_for_runtime_position(state=state, chain_state=chain)
    derived = derive_position_phase(**_runtime_state_to_phase_facts(state, chain_state=chain))
    assert live is expected
    assert derived is expected


def test_exit_fallback_with_chain_quarantine_is_quarantined() -> None:
    # An exit-state fallback on a non-terminal state, with chain quarantine, is
    # QUARANTINED (the chain fallback outranks the exit fallback).
    live = phase_for_runtime_position(state="entered", exit_state="exit_intent", chain_state="quarantined")
    derived = derive_position_phase(**_runtime_state_to_phase_facts("entered", "exit_intent", "quarantined"))
    assert live is PositionPhase.QUARANTINED
    assert derived is PositionPhase.QUARANTINED


# --- full-domain antibody: EXACT equivalence (zero divergence) post-ruling ------- #

def test_derive_phase_exactly_equivalent_to_live_owner_full_domain() -> None:
    for s, es, cs in product(_RUNTIME_STATES, _EXIT_STATES, _CHAIN_STATES):
        live = phase_for_runtime_position(state=s, exit_state=es, chain_state=cs)
        derived = derive_position_phase(**_runtime_state_to_phase_facts(s, es, cs))
        assert derived is live, f"divergence at {(s, es, cs)}: live={live.value} derived={derived.value}"


def test_runtime_adapter_byte_identical_to_live_owner_full_domain() -> None:
    # The production runtime adapter (lifecycle_manager.derive_runtime_position_phase)
    # must reproduce phase_for_runtime_position exactly across the whole domain.
    for s, es, cs in product(_RUNTIME_STATES, _EXIT_STATES, _CHAIN_STATES):
        live = phase_for_runtime_position(state=s, exit_state=es, chain_state=cs)
        bridged = derive_runtime_position_phase(state=s, exit_state=es, chain_state=cs)
        assert bridged is live, f"adapter divergence at {(s, es, cs)}: live={live.value} bridged={bridged.value}"
