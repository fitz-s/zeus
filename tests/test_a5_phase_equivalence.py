# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   A5 PositionPhase projection; consult ruling thread 6a42bc3d (authority-aware reducer).

"""A5 equivalence antibody — authority-aware derive_position_phase vs the live owner.

`phase_for_runtime_position` (lifecycle_manager) is the canonical live projection: a
single-state-string dispatcher giving the primary `state` field authority, with
`chain_state`/`exit_state` only as FALLBACKS that fire when the primary state has not
already reached a stronger economic/exit/terminal disposition.

T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW): the
former "explicit A5 quarantine vs A7 chain-quarantine FALLBACK" split is retired —
there is no quarantine phase target. 'quarantined' / 'quarantine_expired' are no
longer recognized state/chain_state values; both `derive_position_phase` and the
live owner now fall through to UNKNOWN for them, exactly like any other
unrecognized/retired vocabulary word (no special-casing). Real exposure disputes
keep their TRUE phase and the dispute lives in a ReviewWorkItem, never here.

This antibody proves the authority-aware reducer stays an EXACT decomposition of the
live owner over the whole input domain (including the now-retired 'quarantined' /
'quarantine_expired' inputs, which both sides must agree fall through identically).
"""

from __future__ import annotations

from itertools import product

import pytest

from src.contracts.canonical_lifecycle import PositionPhase
from src.state.canonical_projections import derive_position_phase
from src.state.lifecycle_manager import (
    PENDING_EXIT_RUNTIME_STATES,
    _legacy_runtime_phase_dispatch,
    derive_runtime_position_phase,
    phase_for_runtime_position,
)

# Full runtime-state domain of phase_for_runtime_position + the fallback inputs.
# 'quarantined' / 'quarantine_expired' are RETAINED here deliberately (not as
# recognized states — T5 retired them) so the antibody proves both sides agree
# they now fall through to UNKNOWN like any other unrecognized string.
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
    the primary `state` value sets EXPLICIT A5 facts; exit-state sets the sole
    remaining FALLBACK fact that projects the phase only when no stronger A5
    authority is present. chain_state no longer feeds any phase fact (T5) — it
    only still contributes to has_exit_fallback via exit_pending_missing."""
    s = str(state or "").strip().lower()
    es = str(exit_state or "").strip().lower()
    cs = str(chain_state or "").strip().lower()
    return dict(
        has_admin_close=(s == "admin_closed"),
        is_voided=(s == "voided"),
        has_settlement=(s == "settled"),
        has_economic_close=(s == "economically_closed"),
        has_explicit_pending_exit=(s == "pending_exit"),
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
    ("quarantined", PositionPhase.UNKNOWN),  # T5: retired, no longer recognized
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


# --- T5: retired chain_state values no longer influence the phase at all -------- #

@pytest.mark.parametrize("state,chain,expected", [
    ("economically_closed", "quarantined", PositionPhase.ECONOMICALLY_CLOSED),
    ("economically_closed", "quarantine_expired", PositionPhase.ECONOMICALLY_CLOSED),
    ("pending_exit", "quarantined", PositionPhase.PENDING_EXIT),
    ("pending_exit", "quarantine_expired", PositionPhase.PENDING_EXIT),
    # T5: a retired chain_state no longer forces QUARANTINED — the position
    # keeps its TRUE phase (exposure/entry-intent facts win outright now).
    ("entered", "quarantined", PositionPhase.ACTIVE),
    ("day0_window", "quarantined", PositionPhase.DAY0_WINDOW),
    ("pending_tracked", "quarantined", PositionPhase.PENDING_ENTRY),
])
def test_state_authority_vs_retired_chain_state(state: str, chain: str, expected: PositionPhase) -> None:
    live = phase_for_runtime_position(state=state, chain_state=chain)
    derived = derive_position_phase(**_runtime_state_to_phase_facts(state, chain_state=chain))
    assert live is expected
    assert derived is expected


def test_exit_fallback_with_retired_chain_state_is_pending_exit() -> None:
    # T5: an exit-state fallback on a non-terminal state, with a retired
    # chain_state value present, is PENDING_EXIT — the retired chain value no
    # longer outranks (or even participates in) the exit fallback.
    live = phase_for_runtime_position(state="entered", exit_state="exit_intent", chain_state="quarantined")
    derived = derive_position_phase(**_runtime_state_to_phase_facts("entered", "exit_intent", "quarantined"))
    assert live is PositionPhase.PENDING_EXIT
    assert derived is PositionPhase.PENDING_EXIT


# --- full-domain antibody: EXACT equivalence (zero divergence) post-ruling ------- #

def test_reducer_byte_identical_to_legacy_oracle_full_domain() -> None:
    # The authority-aware reducer must reproduce the FROZEN legacy dispatcher (the
    # battle-tested live phase semantics) exactly across the whole input domain.
    for s, es, cs in product(_RUNTIME_STATES, _EXIT_STATES, _CHAIN_STATES):
        oracle = _legacy_runtime_phase_dispatch(state=s, exit_state=es, chain_state=cs)
        derived = derive_position_phase(**_runtime_state_to_phase_facts(s, es, cs))
        assert derived is oracle, f"divergence at {(s, es, cs)}: oracle={oracle.value} derived={derived.value}"


def test_a5_cutover_public_and_adapter_match_legacy_oracle_full_domain() -> None:
    # A5 CUTOVER proof: the runtime adapter AND the now-delegating public
    # phase_for_runtime_position both reproduce the frozen oracle exactly across the
    # whole domain — the cutover is behavior-preserving (no live row changes phase).
    for s, es, cs in product(_RUNTIME_STATES, _EXIT_STATES, _CHAIN_STATES):
        oracle = _legacy_runtime_phase_dispatch(state=s, exit_state=es, chain_state=cs)
        bridged = derive_runtime_position_phase(state=s, exit_state=es, chain_state=cs)
        public = phase_for_runtime_position(state=s, exit_state=es, chain_state=cs)
        assert bridged is oracle, f"adapter divergence at {(s, es, cs)}: oracle={oracle.value} bridged={bridged.value}"
        assert public is oracle, f"cutover divergence at {(s, es, cs)}: oracle={oracle.value} public={public.value}"
