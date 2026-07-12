# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   §1 canonical vocabulary; consult review thread 6a42bc3d step-1 read-only gate cutover.

"""Behavior-lock for the family re-entry blocking predicate + its canonical sourcing.

The family-exclusive dedup gate blocks a same-family re-entry when an existing
position / order / trade / command state says the family is already live. This pins
the block/allow decision per the review's fixture matrix (consult 6a42bc3d) and locks
the three cleanly-mappable blocking value-sets to the typed lifecycle enums
(byte-identical), so the enum-sourcing refactor cannot silently change which live
rows the SQL IN-clauses and the Python gate select.
"""

from __future__ import annotations

import pytest

from src.contracts.canonical_lifecycle import PositionPhase, VenueOrderStatus, VenueTradeStatus
from src.state.canonical_projections import OPEN_ORDER_FACT_STATES
from src.strategy.family_exclusive_dedup import (
    _TRADE_FACT_BLOCKING_STATES,
    _TRADE_ORDER_BLOCKING_STATES,
    _TRADE_POSITION_BLOCKING_PHASES,
    _state_text_blocks_reentry,
)


# --- review fixture matrix (consult 6a42bc3d): block/allow per state text ------ #

@pytest.mark.parametrize("state", [
    "LIVE", "RESTING", "PARTIALLY_MATCHED", "MATCHED", "ACKED",
    "UNKNOWN", "REVIEW_REQUIRED", "active", "pending_entry", "day0_window",
    "pending_exit", "quarantined", "partially_filled", "filled", "submitting",
])
def test_live_family_states_block_reentry(state: str) -> None:
    # An entry that is live / partially-matched / filled / unknown / under review
    # blocks a same-family re-entry.
    assert _state_text_blocks_reentry(state) is True


@pytest.mark.parametrize("state", [
    "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED",  # terminal venue order outcomes
    "settled", "voided", "economically_closed", "admin_closed",  # terminal position phases
    "CANCELLED", "CANCELED",  # raw cancel synonyms
])
def test_terminal_states_do_not_block_reentry(state: str) -> None:
    # A terminal/cancelled/expired/settled disposition frees the family for re-entry.
    assert _state_text_blocks_reentry(state) is False


def test_empty_phase_blocks_defensively() -> None:
    # An empty phase string is in the defensive blocking superset (byte-identical to
    # the prior gate); the real call site coerces empty -> "pending_entry" upstream,
    # so an unknown-phase exposure still blocks a same-family re-entry.
    assert _state_text_blocks_reentry("") is True


# --- value-lock: the 3 sourced blocking sets are byte-identical to golden ------ #

def test_trade_position_blocking_phases_sourced_from_position_phase() -> None:
    assert set(_TRADE_POSITION_BLOCKING_PHASES) == {
        "pending_entry", "active", "day0_window", "pending_exit", "quarantined",
    }
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' is
    # retired from PositionPhase — no writer mints it going forward — but it
    # stays in _TRADE_POSITION_BLOCKING_PHASES as a mixed-epoch bridge literal
    # for the raw-SQL `phase IN (...)` family-dedup query (a LEGACY row still
    # carrying it, until the T5 schema migration, docs/rebuild item 5,
    # rewrites history, must keep blocking a same-family re-entry). Every
    # OTHER member is still a real PositionPhase value (enum-sourced).
    assert set(_TRADE_POSITION_BLOCKING_PHASES) - {"quarantined"} <= {p.value for p in PositionPhase}


def test_trade_fact_blocking_states_sourced_from_venue_trade_status() -> None:
    assert set(_TRADE_FACT_BLOCKING_STATES) == {"MATCHED", "MINED", "CONFIRMED", "PARTIAL"}
    assert {
        VenueTradeStatus.MATCHED.value,
        VenueTradeStatus.MINED.value,
        VenueTradeStatus.CONFIRMED.value,
    } <= set(_TRADE_FACT_BLOCKING_STATES)


def test_trade_order_blocking_states_sourced_from_open_order_and_venue_order() -> None:
    assert set(_TRADE_ORDER_BLOCKING_STATES) == {
        "LIVE", "RESTING", "PARTIALLY_MATCHED", "MATCHED", "ACKED", "UNKNOWN", "REVIEW_REQUIRED",
    }
    # open-order core is exactly OPEN_ORDER_FACT_STATES; terminal MATCHED from VenueOrderStatus
    assert set(OPEN_ORDER_FACT_STATES) <= set(_TRADE_ORDER_BLOCKING_STATES)
    assert VenueOrderStatus.MATCHED.value in _TRADE_ORDER_BLOCKING_STATES
