# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   A5 PositionPhase projection; consult review thread 6a42bc3d (Step 3 audit finding).

"""A5 equivalence antibody — derive_position_phase vs the live runtime phase owner.

`phase_for_runtime_position` (lifecycle_manager) is the MATURE live projection: a
single-state-string dispatcher giving the primary `state` field authority, with
`chain_state`/`exit_state` only as fallbacks when the state is non-terminal. The
redesign's `derive_position_phase` is a DIFFERENT input model — nine independent
truth booleans with a flat monotonic precedence.

For every single-fact runtime input the two AGREE, so derive is a faithful
decomposition of the live owner. They diverge on exactly one edge class: when the
primary state is `economically_closed`/`pending_exit` AND a chain-quarantine
fallback is also set, the live owner keeps the economic/exit phase (state-field
authority) while derive's flat precedence ranks QUARANTINED higher.

This antibody PINS that relationship so:
  - derive_position_phase is proven equivalent to the live owner on all non-edge
    inputs (the cutover would be behavior-preserving there), and
  - the four documented divergence combos cannot silently change on either side
    without breaking this test — derive is therefore NOT a drop-in replacement for
    phase_for_runtime_position until the state-authority-vs-quarantine-override
    semantics are ruled on (open design question, consult 6a42bc3d follow-up).
"""

from __future__ import annotations

from itertools import product

import pytest

from src.contracts.canonical_lifecycle import PositionPhase
from src.state.canonical_projections import derive_position_phase
from src.state.lifecycle_manager import (
    PENDING_EXIT_RUNTIME_STATES,
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

# The ONLY sanctioned derive-vs-live divergences (state-field authority vs flat
# quarantine precedence). Pinned until the semantics are ruled on.
_SANCTIONED_DIVERGENCE = {
    ("economically_closed", "quarantined"),
    ("economically_closed", "quarantine_expired"),
    ("pending_exit", "quarantined"),
    ("pending_exit", "quarantine_expired"),
}


def _runtime_state_to_phase_facts(state: str, exit_state: str = "", chain_state: str = "") -> dict:
    """Map the runtime (state, exit_state, chain_state) onto derive_position_phase's
    independent truth booleans, replicating the live owner's fallback structure
    (chain-quarantine / exit-pending fill in only as facts, not state-authority)."""
    s = str(state or "").strip().lower()
    es = str(exit_state or "").strip().lower()
    cs = str(chain_state or "").strip().lower()
    return dict(
        has_admin_close=(s == "admin_closed"),
        has_settlement=(s == "settled"),
        is_voided=(s == "voided"),
        is_quarantined=(s == "quarantined") or (cs in {"quarantined", "quarantine_expired"}),
        has_economic_close=(s == "economically_closed"),
        has_open_exit=(
            (s == "pending_exit")
            or (es in PENDING_EXIT_RUNTIME_STATES)
            or (cs == "exit_pending_missing")
        ),
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


# --- full-domain antibody: equivalent except the four pinned divergence combos -- #

def test_derive_phase_equivalent_to_live_owner_except_pinned_divergence() -> None:
    observed_divergence: set[tuple[str, str]] = set()
    for s, es, cs in product(_RUNTIME_STATES, _EXIT_STATES, _CHAIN_STATES):
        live = phase_for_runtime_position(state=s, exit_state=es, chain_state=cs)
        derived = derive_position_phase(**_runtime_state_to_phase_facts(s, es, cs))
        if live is derived:
            continue
        # Any divergence MUST be the sanctioned state-authority-vs-quarantine edge.
        assert (s, cs) in _SANCTIONED_DIVERGENCE, f"unexpected divergence: {(s, es, cs, live.value, derived.value)}"
        assert live.value in {"economically_closed", "pending_exit"}
        assert derived is PositionPhase.QUARANTINED
        observed_divergence.add((s, cs))
    # The pinned set is exhaustive — every sanctioned combo actually occurs.
    assert observed_divergence == _SANCTIONED_DIVERGENCE
