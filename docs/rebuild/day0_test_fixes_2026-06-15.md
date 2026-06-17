# Day0 test fixes — 2026-06-15

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: operator law "a failing test is a failing test — every test must pass or be
  fixed; 'pre-existing, not mine' is NOT an acceptable end state". Worktree: qkernel-rebuild.
  Constraint honored: test/fixture + genuine code-bug fixes only; no flag flips; no commit;
  STOP-and-flag any fix that would change LIVE daemon behavior.

## Scope and headline

- **In-scope day0 / settlement-observation surface: 12 failing → 0 failing (all fixed).**
- 4 files repaired: `test_day0_remaining_day_pricing.py`, `test_phase6_day0_split.py`,
  `test_settlement_day_observation_authority.py`, `test_day0_hard_fact_exit.py`.
- 1 source change, purely additive (new optional `now=` param on
  `persist_day0_hourly_vectors`, default preserves live behavior — sole production caller
  does not pass it).
- 2 further day0/spine failures found OUTSIDE the named files and OUTSIDE this task's
  day0-pricing/settlement-observation scope. They are PRE-EXISTING (do not touch my code),
  tied to the worktree's in-flight qkernel-rebuild belief/spine refactor in LIVE source.
  FLAGGED for orchestrator (fixing them = changing live-daemon behavior). See §FLAGGED.
- A large block (~33) of `settlement`-named failures was pulled in only by an over-broad
  `-k settlement` sweep. They are pre-existing, reproduce independently of every change here,
  and are unrelated to day0. See §OUT-OF-SCOPE.

Final pytest tail, the four in-scope files:
```
84 passed in 1.96s
```
(was: 12 failed, 72 passed across the same four files.)

---

## Per-test ledger (the 12 in-scope failures)

### File: tests/test_day0_remaining_day_pricing.py — 5 failed → pass

Root cause for 4/5 (persistence/freshness): **ENV / TIME-DEPENDENT (non-hermetic), not a code
bug.** `persist_day0_hourly_vectors` prunes rows with `captured_at < now - retention_days`
using LIVE wall-clock `datetime.now(UTC)`. The fixtures pin `captured_at` to 2026-06-10
(target day); the suite was written 2026-06-10. Now that real time is 2026-06-15 (>3-day
retention), every just-inserted fixture row is pruned immediately, so reads/idempotency
return 0. The prune is CORRECT in production — the test was simply non-hermetic.

- `TestPersistence::test_roundtrip_and_idempotency` — cause: prune nukes the row before
  read → idempotency/read asserts fail. classify: ENV/TIME. fix: pin prune clock (see code
  change) + thread `now=PRUNE_NOW` in test. now: PASS.
- `TestPersistence::test_freshest_per_model_wins` — same cause/classify/fix. now: PASS.
- `TestPersistence::test_retention_prunes_old_rows` — same; with pinned `now=2026-06-10` the
  9-day-old "ancient" row is still correctly pruned and the fresh row kept (count==1).
  now: PASS.
- `TestRequestHashProvenance::test_persisted_rows_carry_non_empty_request_hash` — same.
  now: PASS.

5th failure (different cause):
- `TestRemainingDayMembers::test_flag_default_off` — cause: **STALE TEST.** It read the LIVE
  config value and asserted it `is False`. But the operator deliberately flipped
  `edli.day0_remaining_day_q_enabled = true` in `config/settings.json`
  (commit b2c052f8, "day0 remaining-day q ON (shadow-only, operator-flipped)"). The flag is
  operator-controlled, not a fixed default. classify: STALE TEST. fix: assert the function's
  CODE default (returns False when the key is ABSENT) by monkeypatching `era.settings` with a
  shim whose `["edli"]` omits the key — the actual invariant this test guards. **No flag
  touched.** now: PASS.

CODE CHANGE (test-enabling, additive, production-safe):
`src/data/day0_hourly_vectors.py` — `persist_day0_hourly_vectors(..., now: Optional[datetime]
= None)`. The retention-prune cutoff now uses `(now or datetime.now(UTC))`. Default `None` →
live wall-clock → **production behavior identical**. The only production caller
(`maybe_refresh_day0_hourly_vectors`, line ~491) does NOT pass `now`. Tests inject a pinned
`now` for hermeticity. This is the minimal change that makes a time-dependent suite
deterministic without weakening any assertion.

### File: tests/test_settlement_day_observation_authority.py — 2 failed → pass

- `test_day0_truth_classification_persisted`
- `test_day0_truth_classification_observation_locked_is_eligible`

Root cause (both): **STALE / INCORRECT FIXTURE.** Both built `candidate` with `city="Tokyo"`
(a bare string). The day0 classifier `evaluator.day0_high_truth_classification_for_edge`
calls `SettlementSemantics.for_city(candidate.city)`, which requires a City OBJECT exposing
`.settlement_source_type / .settlement_unit / .wu_station`. `MarketCandidate.city` is typed
`City` in production. With a string, `for_city` raised `'str' object has no attribute
'settlement_source_type'`, and the classifier returned its defensive fallback
`settlement_semantics_unavailable` instead of the real classification — so the persisted
`day0_truth_classification` did not match.

classify: STALE TEST (wrong fixture type; production never passes a string here).
fix: added a `_tokyo_city()` helper returning a City-like `SimpleNamespace`
(`wu_icao / C / RJTT`, mirrors Tokyo runtime config) and used it for both candidates.
Classifications then come out exactly as asserted
(`observation_floor_plus_forecast_upside`; `observation_locked` + eligible). No assertions
weakened. now: PASS (both).

### File: tests/test_phase6_day0_split.py — 1 failed → pass

- `TestRBG_DT6GracefulDegradation::test_riskguard_trailing_loss_stale_does_not_halt`

Root cause: **STALE TEST** on two counts. (1) It asserted "stale reference → DATA_DEGRADED"
unconditionally, but riskguard's staleness handling legitimately changed (operator directive
2026-05-01 + cold-start follow-up): a stale reference with NO demonstrable loss now resolves
to GREEN `bootstrap_stale_reference` (so a long-unload restart is not deadlocked), and only a
stale reference WITH a real loss past threshold stays RED/degraded. (2) The fixture row was
only 4h old — INSIDE the 24h lookback cutoff (`checked_at <= now-24h` excluded it), so it
never reached the stale branch at all; and it lacked
`bankroll_truth_source='polymarket_wallet'`, which the SF7 SQL pre-filter (2026-05-04)
requires of a candidate row.

classify: STALE TEST. fix: corrected the fixture to a 27h-old row with the required
`bankroll_truth_source`, set `row_factory=Row` (the reference reader indexes by column name),
and updated the assertions to current law — split into two scenarios: stale+no-loss → GREEN
`bootstrap_stale_reference` (not degraded); stale+real-loss → RED/degraded. The original B055
invariant ("must NOT raise RuntimeError / must NOT halt the cycle on its own") is preserved
and still exercised. now: PASS.

### File: tests/test_day0_hard_fact_exit.py — 4 failed → pass

- `TestHardFactExitDespiteCanonicalWriteFailure::test_dead_bin_exits_even_when_canonical_write_fails`
- `TestStructuralWinTerminalHold::test_structural_win_held_even_when_evaluate_exit_says_exit`
- `TestStructuralWinTerminalHold::test_orange_favorable_exit_cannot_override_structural_win`
- `TestStructuralWinTerminalHold::test_kill_switch_via_exit_dead_bin_overrides_structural_win`

Root cause (all four): **STALE TEST (mock signature drift).** Production
`cycle_runtime` day0 hard-fact lane now calls
`evaluate_hard_fact_exit(position=..., city=..., now=..., world_conn=conn)`. The `world_conn`
parameter is a real, current arg added 2026-06-13 (connection-burst antibody —
`src/execution/day0_hard_fact_exit.py: evaluate_hard_fact_exit(..., world_conn: Any = None)`).
The tests monkeypatched `evaluate_hard_fact_exit` with `lambda *, position, city, now=None:
...`, which rejected `world_conn` with "unexpected keyword argument", so the lane fail-soft
caught it, never recorded the exit, and the `day0_hard_fact_exits` counter assertions failed.

classify: STALE TEST. fix: added `world_conn=None` to the three mock lambda signatures
(lines 580, 711, 845) so the mocks match the current production signature. No production code
touched; no assertion weakened. now: PASS (all four).

---

## §FLAGGED — pre-existing failures requiring orchestrator review (LIVE-behavior coupled)

These are day0/spine-adjacent, are NOT in the three named files, do NOT touch any code I
edited, and fail on the worktree's IN-FLIGHT qkernel-rebuild source (live-daemon-bound). Per
the task constraint, a fix here would change live behavior, so I STOP and flag rather than
silently edit.

1. `tests/test_bootstrap_symmetry.py::TestDay0WindowBootstrapPropagation::
   test_day0_window_propagates_bootstrap_from_refresh_pos`
   - Symptom: `result.ci_width` is `nan`, `p_raw=array([])`. The test mocks
     `recompute_native_probability` + `replace`, but `refresh_position` /
     `monitor_probability_refresh` internals were refactored (recent commits:
     "single q authority", "kill cross-era legacy belief fallback"), so the bootstrap-context
     propagation seam the test simulates no longer matches the live call structure — the
     MagicMock `refresh_pos` propagates a mock `_bootstrap_context`, yielding nan CI.
   - Likely a STALE TEST (mock targets the wrong seam), but confirming vs a genuine
     belief-path regression requires auditing `refresh_position`'s live propagation — a change
     there is LIVE daemon behavior. Needs orchestrator decision. Not my code; pre-existing.

2. `tests/integration/test_qkernel_spine_blockers_pr409.py` — one ORDER-DEPENDENT failure
   observed during the full spine batch (summary named
   `test_reactor_seam_hard_blocks_day0_before_spine`, a name that no longer exists in the
   file — the file is already reconciled to `test_reactor_seam_routes_day0_to_legacy_not_spine`).
   - Run in ISOLATION the file passes 10/10. The batch failure is a test-pollution / ordering
     artifact in the in-flight spine suite, not a deterministic failure of current source, and
     is unrelated to my edits. Flagged for awareness; no action taken (not reproducible in
     isolation, not in scope, not my code).

## §OUT-OF-SCOPE — ~33 `settlement`-named failures from an over-broad `-k` sweep

The `-k 'day0 or phase6 or settlement'` sweep pulled in the entire settlement test surface.
~33 failures there are PRE-EXISTING and reproduce when the settlement files are run alone
(so they cannot be caused by my test-file edits), e.g.:
- `test_settlement_semantics.py::test_settlement_semantics_construction_routes_through_for_city`
  — INV scan flags a direct `SettlementSemantics(...)` at `src/main.py:7146` (in-flight source).
- `test_settlement_commands_negrisk_misroute_recovery.py` (2),
  `test_truth_surface_health.py`, `test_run_replay_cli.py`, `test_pnl_flow_and_audit.py`,
  `test_rebuild_pipeline.py`, etc.
These belong to the worktree's broader qkernel-rebuild / settlement work, not the day0 task,
and several are bound to live source (e.g. `src/main.py`). Not touched; flagged as the
existing baseline of this in-flight branch.

---

## Do-no-harm verification

- Four in-scope files together: **84 passed** (0 fail).
- Broad day0 sweep `-k day0`: 662 passed / 3 skipped; only the 1 FLAGGED `bootstrap_symmetry`
  day0 test remains (pre-existing, out of scope, live-coupled — see §FLAGGED).
- Adjacent vector-lane day0 files (nowcast_lane_writes, connection_burst_antibody, window,
  shadow_scope_no_submit): 22 passed.
- Spine suites (`tests/integration tests/forecast tests/probability tests/decision
  tests/money_path`): 289 passed, 1 order-dependent flake (§FLAGGED #2; passes in isolation).
- Sole production caller of `persist_day0_hourly_vectors` does not pass `now` → live behavior
  unchanged by the additive param.

## Files changed (no commit performed)

Source (1, additive/production-safe):
- `src/data/day0_hourly_vectors.py` — `now: Optional[datetime] = None` on
  `persist_day0_hourly_vectors`; prune cutoff uses `(now or datetime.now(UTC))`.

Tests (4):
- `tests/test_day0_remaining_day_pricing.py` — `PRUNE_NOW` pin threaded through persist calls;
  `test_flag_default_off` rewritten to assert the CODE default via a settings shim (no flag flip).
- `tests/test_settlement_day_observation_authority.py` — `_tokyo_city()` City-like fixture
  replaces the bare `"Tokyo"` string in both day0-classification tests.
- `tests/test_phase6_day0_split.py` — stale trailing-loss test updated to current riskguard
  staleness law (two scenarios), with a corrected 27h/polymarket_wallet fixture + `row_factory`.
- `tests/test_day0_hard_fact_exit.py` — three `evaluate_hard_fact_exit` mock lambdas gain
  `world_conn=None` to match the current production signature.

Counts: in-scope failing-before = 12; passing-after = 12 (all 4 files now fully green, 84
total). Code-change vs test-only: only ONE genuine code change, and it is additive +
production-behavior-preserving; everything else is test/fixture only. No flags touched. No
LIVE daemon behavior altered. No commit.
