# Continuity fix — future-not-listed Gamma warm-backoff (#122, 2026-06-15)

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: live log diagnosis (logs/zeus-live.{log,err}) of the post-un-hang
  `fresh_executable_city_count` oscillation; code at `src/main.py
  _refresh_pending_family_snapshots`. Deploy target: `live/iteration-2026-06-13`
  (the branch the live daemon runs).

## Where this sits in the chain (corrected ground truth)
The pre-compaction summary's "DB-lock storm starves all captures → no fills" was a
MISDIAGNOSIS. The real no-fills root cause was the spine **sizing hang**
(`optimize_vector_stake` 396×4000 ≈ 1.6M calls = ~71s/candidate → 660s cycles →
every snapshot staled), FIXED in `93999d9d9b` (~1400× speedup), which produced the
**first on-chain fill in 3 days** this morning (buy_no 9.81 sh @ 0.74, chain-confirmed
09:43, settles 06-17). The validated edge (NO-on-modal harvest, +0.125 WU after-cost,
~130 admits/day) + σ fix (k=1.30) + harvest relaxation (`3c4aeecc75`) are all DEPLOYED
and the running daemon (started 17:38:58, after all commits) HAS them.

So the edge is live. The remaining gap to the operator's **continuous** goal is
capture continuity: the morning produced 2 orders, not the ~130/day the grade implies.

## The continuity defect (#122)
`fresh_executable_city_count` oscillates `19→23→0→0→0→0` — roughly a THIRD of warm
cycles have fresh executable cities, two-thirds are `executable_substrate_coverage_status:
NONE` (inserted=0). The harvest gate can only fire in the FULL windows → sporadic orders.

### Root cause (decisive)
At `src/main.py:~3570`, a family with **no market topology** is re-added to
`gamma_refresh_families` EVERY cycle ("Gamma may discover bins not yet in topology")
and probed via the Gamma slug API. The evidence:
- The Gamma counters show `raw_events=0 discovered_events=0` on **every** cycle — the
  gamma phase discovers NOTHING; probed families come back `empty` (attempted=8 → empty=8).
- The "not harvested before time-box" families are **200/200 future-dated `2026-06-16`**
  (today 06-15) — tomorrow's markets NOT YET LISTED on Polymarket (forecast side emits
  06-16 opportunity events; market listing lags ~1 day).
- These ~200 dead families recur every rotation, exhaust the bounded Gamma time-box
  (concurrency 8, /events p95 2.5s, slice ≈ a few seconds), and starve CLOB capture of
  the families that DO have cached topology → coverage `NONE` two-thirds of cycles.

There is a `_family_venue_closed` warm-skip for PAST/closed families but **no symmetric
backoff for FUTURE/not-yet-listed** families — that is the gap.

## The fix (evidence-keyed retry backoff — NOT a cap/throttle)
A no-topology family whose Gamma lookup returned EMPTY is parked in a module-global
`_GAMMA_EMPTY_BACKOFF_UNTIL` (family_key → monotonic deadline) for
`ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS` (default 300s) and NOT re-probed until the
cooldown expires. It STAYS a pending event (never terminally dropped) and is captured
the moment the market lists (≤cooldown latency). Symmetric twin of the existing
`_family_venue_closed` past-skip — a focus/efficiency skip.

- SET from `gamma_empty_family_keys` (probed AND returned empty) only — `timebox_unattempted`
  (never probed) families stay immediately retryable.
- CHECK in the `if not topology_rows:` branch, before re-adding to the gamma probe set.
- Both sides key by `_refresh_family_key(...)` (same normalized `tuple[str,str,str]`).
- Convergence: ~8 min post-restart (8 empties parked/cycle × 25 cycles for ~200) then
  steady FULL coverage; dead families thereafter re-probed only ~once/cooldown,
  rotation-staggered (no thundering herd). Reversible: env=0 disables; revert + kickstart.

## Validation
- AST + import OK; `tests/test_time_semantics_relations.py` 19/19 (governs the gamma
  time-box relations).
- Warm-cycle regression suite: 63 passed incl. `tests/test_funnel_starvation_substrate_sweep.py`,
  `tests/test_market_substrate_warm_lock_contention.py`,
  `tests/engine/test_decision_refresh_topology_identity.py`,
  `tests/money_path/test_edli_market_substrate_warm_cycle.py`.
- 4 FAILURES are PRE-EXISTING (confirmed by stashing the change and re-running the clean
  tree — identical 4 fail): `test_cached_topology_limits_gamma_lookup_window` (stale test
  for the refactored `_gamma_lookup_deadline_for_snapshot_refresh`, which now correctly
  returns `refresh_deadline − snapshot_reserve_s` and ignores `cached_topology_count` per
  the 2026-06-09 FUNNEL-STARVATION FIX) and 3 fixture-dependent gamma-parse tests. None
  touched by this change. (Separate cleanup, not part of #122.)

## Success bar (unchanged)
This fix removes a CONTINUITY limiter; it does not by itself prove alpha. DONE remains:
continuous settlement-graded POSITIVE-after-cost fills, the 06-17 settlement of the
filled position grading positive, and calibration coherence — not one order.
