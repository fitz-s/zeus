# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R2-a/R2-b + §E2
#                  docs/rebuild/whole_system_first_principles_2026-07-07.md §2.4 + §7.1
"""R2-core: the two snapshot contracts + diff engine replacing the 31-pass
recovery mountain (src/execution/command_recovery.py + parts of
src/execution/exchange_reconcile.py).

BUILD-INTO-TARGET (§E2): this package is a clean-namespace target component,
not a patch onto the legacy files. It is SHADOW-FREE but INERT for this
packet (R2-core) -- nothing outside tests/the replay harness imports it yet.
Wiring it into a live cycle (with byte-identical/replay promotion evidence)
is R2-c, the 31-pass migration wave, per the constitution's no-shadow-modes
axiom (§C6).

Public surface:
    local_truth   -- what Zeus itself believes it holds (venue_commands +
                      position_current + collateral_reservations).
    chain_truth   -- what the outside world says happened (on-chain
                      positions + settlement resolutions + venue order/trade
                      fact stream, deduped via src.state.fill_dedup).
    diff_engine   -- classify(local, chain) -> findings -> predicate table ->
                      apply_corrective_event; reconcile() runner entry point.
    replay        -- certificate/event-native replay harness (the R2-c
                      acceptance tool, built now per brief item 6).
"""
from __future__ import annotations
