# Live Redecision And F109 Repair Plan

Created: 2026-06-17
Scope: live-money runtime repair for continuous redecision and canonical position projection.

## Current Evidence

- Live daemon is running at loaded SHA `018ce63131ec11106fbccfad8f0fa2b5e6d51544`.
- Execution capability is currently open for entry and exit, but recent business-plane status shows no final intent.
- Continuous redecision scheduler runs, yet entry screen can return before logging when no family fires.
- Current open positions are NO-side weather exposure, while `enqueue_live_redecisions` only evaluates `buy_yes`.
- `position_current` can still contain multiple open rows for one token; F109 then blocks canonical monitor and fill-bridge writes.
- Existing duplicate consolidator only voids provable overbook rows. It does not repair same-token rows when local shares are less than or equal to chain shares.

## Requirements

1. Continuous redecision must evaluate both `buy_yes` and `buy_no` from the same settlement-preimage belief vector.
2. A no-action redecision screen cycle must leave operator-visible evidence, not only an APScheduler success line.
3. Same-token open rows that are semantically mergeable must converge to one canonical open row with aggregated economics and audit events.
4. Same-token open rows that are not semantically mergeable must remain fail-closed and visible as divergent; the repair must not guess away real chain exposure.
5. EDLI fill bridge must not keep retrying a parallel open row when an equivalent canonical open row already owns the token exposure.
6. Verification must include relationship tests and current live evidence. A single order firing is not completion.

## Planned Changes

- Patch `src/events/continuous_redecision.py` to compute NO-side posterior as `1 - yes_post` and screen it against the existing NO quote.
- Patch `src/main.py` to log empty continuous-redecision screen outcomes with counts.
- Extend `src/state/position_duplicate_consolidator.py` with a conservative merge path for same token, same direction, same strategy, same condition, same target market identity, and same open lifecycle class.
- Patch `src/events/edli_position_bridge.py` only if required after inspecting bridge identity equivalence; prefer reusing the canonical merge helper over adding a parallel write path.
- Add targeted tests covering NO-side redecision, empty-screen observability, and mergeable-vs-divergent duplicate open rows.

## Verification

- `python3 -m pytest tests/events/test_continuous_redecision.py tests/events/test_continuous_redecision_resurrection.py`
- `python3 -m pytest tests/state/test_position_open_idempotency.py tests/state/test_f109_consolidator_boot_wire.py tests/events/test_edli_position_bridge.py`
- `python3 scripts/topology_doctor.py --planning-lock --plan-evidence docs/operations/current/live-redecision-f109-repair/PLAN.md --changed-files ...`
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...`
- Live DB/log check after deployment or reload: no repeated F109 bridge storm for the same token, redecision screen logs action/no-action counts, and order/position facts remain chain-first.
