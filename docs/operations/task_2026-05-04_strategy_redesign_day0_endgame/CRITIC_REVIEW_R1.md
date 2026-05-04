# Critic Review R1 — Day0-as-Endgame strategy redesign plan

**Verdict**: REJECT-AND-RESPLIT
**Evidence-grounded**: yes (file:line cited per finding)
**HEAD**: d0259327e3fd46c3c2e2fc351676a2f887a38d03
**Date**: 2026-05-04
**Reviewer**: critic-opus

---

## Bottom line (read this first)

The plan is conceptually correct in direction (Day0 *is* a position-lifecycle phase, not a peer entry mode) but **rests on a premise that is already false on disk**. Zeus *already has* a `LifecyclePhase` enum that includes `DAY0_WINDOW` as a per-position phase, *already has* `lead_hours_to_settlement_close()` computed in city-local timezone, *already has* a `phase` column on `position_current` populated through canonical-DB lifecycle events, and *already has* `fold_lifecycle_phase()` enforcing legal phase transitions including the active→day0_window fold.

The plan reads as if none of this exists. P0 ("phase-typing infrastructure") proposes building a parallel `LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` namespace that **collides with the live `LifecyclePhase.{PENDING_ENTRY, ACTIVE, DAY0_WINDOW, PENDING_EXIT, ECONOMICALLY_CLOSED, SETTLED, VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN}`** at `src/state/lifecycle_manager.py:9-19`. P0 must either (a) re-use the existing enum and add only what is missing, or (b) explicitly justify why a parallel taxonomy is needed and rename to avoid collision. The current draft does neither and will produce a P0 PR that breaks `tests/test_p1_save_order.py::test_terminal_states_constant_covers_all_terminal_phases` and the `LEGAL_LIFECYCLE_FOLDS` invariant.

REJECT-AND-RESPLIT requested. Plan must be rewritten as an *augmentation* of the existing phase machinery (renaming the proposed `PRE_DAY0/DAY0/POST_RESOLUTION` to a different concept name like `MarketPhase` or `LifecycleStage` to disambiguate, and explicitly mapping the new concept onto the existing per-position `LifecyclePhase`) before P0 starts. The §7 open decisions also need to be answered, not just listed.

---

## Per-attack findings

### A1 Premise drift [VERDICT: FAIL]

**Claim under review** (PLAN §0): "Current code treats `DAY0_CAPTURE` as a peer entry mode alongside `OPENING_HUNT` and `UPDATE_REACTION`."

**Verification**:
- `src/engine/cycle_runner.py:335-339` — confirms `MODE_PARAMS` enumerates the three modes as peer entries:
  ```
  DiscoveryMode.OPENING_HUNT:   max_hours_since_open=24, min_hours_to_resolution=24
  DiscoveryMode.UPDATE_REACTION: min_hours_since_open=24, min_hours_to_resolution=6
  DiscoveryMode.DAY0_CAPTURE:    max_hours_to_resolution=6
  ```
- `src/main.py:749` schedules `_run_mode(DiscoveryMode.DAY0_CAPTURE)` as one of three peer mode invocations.
- `src/engine/discovery_mode.py:9` defines `DAY0_CAPTURE = "day0_capture"`.

The premise is **partially correct** — DAY0_CAPTURE *is* a peer DiscoveryMode for *candidate-discovery filtering*. **But the plan over-states the premise** by ignoring that:
- `src/state/lifecycle_manager.py:12` already has `LifecyclePhase.DAY0_WINDOW` as a *per-position phase* (distinct from DiscoveryMode).
- `src/state/lifecycle_manager.py:216-237` already has `enter_day0_window_runtime_state()` enforcing the active→day0_window transition.
- `src/state/projection.py:8` shows `position_current.phase` is already a column populated from `canonical_phase_for_position()`.

Translation: the conceptual reframing the plan calls for is *partially already present* in the codebase. The plan author appears to have surveyed `cycle_runner.py` / `evaluator.py` (which do conflate DAY0_CAPTURE-mode with strategy_key) but missed `lifecycle_manager.py` / `projection.py` / `lifecycle_events.py` (which already model phases per-position).

**Required fix in plan**: §0 must distinguish (a) DiscoveryMode (cycle-level candidate filter, peer-mode is correct) from (b) per-position LifecyclePhase (already exists, already has DAY0_WINDOW). The reframing applies to (a) but not (b). The plan as written treats them as undifferentiated.

### A2 Math soundness — per-phase calibration cohort feasibility [VERDICT: FAIL]

**Claim under review** (PLAN §5 L2): "Per-phase calibration cohorts: Platt fits today are per (source × metric). The reframing implies a two-level cohort: (source × metric × phase)."

**Verification**:
- `src/state/schema/v2_schema.py:267-298` shows `calibration_pairs_v2` columns: `city, target_date, temperature_metric, observation_field, range_label, p_raw, outcome, lead_days, season, cluster, ...` — **no `phase` column**.
- `src/state/schema/v2_schema.py:300-301`: index is `(temperature_metric, cluster, season, lead_days)` — `lead_days` already implicitly captures phase information (lead<1 day ≈ Day0).
- `src/calibration/platt.py:1-3`: `P_cal = sigmoid(A * logit(P_raw) + B * lead_days + C)` — **lead_days is already a Platt input feature**, so phase information is already in the calibration model.

This is a critical math soundness issue: **the plan proposes splitting cohorts by a dimension (phase) that the existing Platt fit already captures continuously via `lead_days` as a regressor.** Splitting by phase converts a continuous regressor into a categorical bucket, which:
1. Discards information (phase boundary at exactly 24h is artificial; a 23h-to-settle pair and a 25h-to-settle pair are physically nearly identical but would land in different cohorts).
2. Reduces sample size per fit (the existing Extended Platt design *deliberately avoids bucketing* to triple per-bucket sample counts — see `src/calibration/platt.py:1-3` "lead_days is NOT a bucket dimension — it's a Platt input. This triples positive samples per bucket (45→135) vs the 72-bucket approach.").

The plan would *re-introduce the small-sample bucket bias the current design explicitly fixed*. Plan §5 L2 mentions this risk obliquely ("does this re-introduce a small-sample bias problem the current single-cohort fit avoids?") but does not show the math for why splitting wins.

**Required fix in plan**: §5 L2 must demonstrate (with sample-count math per (city × metric × Phase-B) cohort, using current `calibration_pairs_v2` row counts) that the split improves out-of-sample Brier vs the current `lead_days`-as-input design. Without that evidence, P4 should be dropped, not deferred.

### A3 Strategy explosion — 4→10 cardinality reach [VERDICT: FAIL]

**Claim under review** (PLAN §4): "Total: 5 entry strategies + 3 terminal-posture strategies + 2 continuous = 10 strategy keys (vs current 4)."

**Verification of downstream surfaces that hardcode the 4-key set**:
- `src/state/db.py:663-669`: CHECK constraint on `decision_chain.strategy_key IN ('settlement_capture', 'shoulder_sell', 'center_buy', 'opening_inertia')`.
- `src/state/db.py:759-764`: CHECK constraint on `strategy_health.strategy_key` — same 4 values.
- `src/state/edge_observation.py:42`: `STRATEGY_KEYS: list[str] = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]`.
- `src/engine/cycle_runner.py:67`: `KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}`.
- `src/engine/cycle_runtime.py:40-46`: `CANONICAL_STRATEGY_KEYS` + `STRATEGY_KEYS_BY_DISCOVERY_MODE`.
- `src/state/portfolio.py:50`: `CANONICAL_STRATEGY_KEYS` (separate constant).
- `src/strategy/kelly.py:67-75`: `STRATEGY_KELLY_MULTIPLIERS` enumerates 4 + 2 dormant (`shoulder_buy`, `center_sell` already present at 0.0 — undocumented in plan §2.3).
- `src/control/control_plane.py:316`: `_LIVE_ALLOWED_STRATEGIES = {"settlement_capture", "center_buy", "opening_inertia"}`.

**Total: 58 source files reference at least one of the 4 keys** (verified via `grep -rn -l ... | wc -l`). Test files: 46.

The plan's §4 §8 A3 acknowledges this risk but proposes no concrete mitigation. The CHECK constraints are particularly load-bearing because they will *fail every existing test that inserts decision_chain or strategy_health rows* if the key set changes. SQLite cannot ALTER an existing CHECK constraint — the table must be dropped and re-created. Plan §6 P2-P3 do not call out this CHECK-constraint migration as a blocker.

**Required fix in plan**: P0 must include a "CHECK-constraint migration plan" subsection enumerating every CHECK constraint, every CANONICAL/KNOWN constant, and every test fixture that references the 4-key set. Until that catalog exists, P0 risk is not "HIGH" but **"BLOCKING"** (touches schema migration on a populated DB).

### A4 Phase-transition atomicity [VERDICT: FAIL]

**Claim under review** (PLAN §8 A4): "Race between cycle and phase-transition observer? What if the cycle reads `phase=PRE_DAY0` but writes apply with `phase=DAY0` because midnight passed mid-cycle?"

**Verification**:
- `src/engine/time_context.py:58-72`: `lead_hours_to_settlement_close()` returns a fractional hour computed against `datetime.now(UTC)` at call time. **Each call recomputes against current wall clock.**
- `src/engine/cycle_runtime.py:1495-1505`, `:1715-1721`, `:2190-2197`: each call site invokes the helper independently with `deps._utcnow()` — no cycle-scoped phase snapshot.

This means within a single cycle, the phase computation **is not stable**: if the cycle straddles midnight in a city's timezone (Sydney, Tokyo, Hong Kong all do), the candidate-filter pass may see `phase=PRE_DAY0` and the position-update pass may see `phase=DAY0`. The plan flags this as a question but does not solve it.

The fix is mechanical (snapshot `decision_time` once per cycle and pass it through, which the codebase already does for some sites — see `src/engine/cycle_runtime.py:2195` `decision_time` parameter) but the plan does not commit to "phase is always derived from `decision_time`, never from `deps._utcnow()` at point of use." Without that invariant, P0 ships a race condition.

**Required fix in plan**: §6 P0 must explicitly state "phase is computed once per cycle from cycle's `decision_time`, threaded through to every call site, never re-derived from wall clock at point of use" + add a relationship test that asserts phase is constant within a cycle even when `freeze_time` straddles midnight.

### A5 Backwards compatibility — existing position phase column [VERDICT: FAIL — biggest miss]

**Claim under review** (PLAN §6 P0 + §7 q5): "every `position_current` row needs a `phase` column. Backfill rule for existing rows?"

**Verification**:
- `src/state/projection.py:6-38`: `CANONICAL_POSITION_CURRENT_COLUMNS` *already includes* `"phase"` at index 1 (second column after `position_id`).
- `src/state/projection.py:87-125`: `upsert_position_current()` writes `phase=excluded.phase` on every upsert.
- `src/engine/lifecycle_events.py:73`: `build_position_current_projection()` populates `"phase": canonical_phase_for_position(position)`.
- `src/state/lifecycle_manager.py:143-182`: `phase_for_runtime_position()` *already maps every runtime state to a `LifecyclePhase`* including DAY0_WINDOW, ACTIVE, PENDING_EXIT, etc.

**The `phase` column already exists. The backfill is already done. The mapping rule is already defined.**

The plan's P0 "Tag every candidate / decision / position with its phase" is *almost entirely complete on the position side*. What's actually missing is *candidate-side* phase tagging (candidates are tagged with `discovery_mode`, not phase) and *decision-side* phase tagging (decisions inherit from candidate). Plan §6 P0 should be re-scoped to "extend phase-tagging to candidates and decisions, reuse existing `phase` for positions" — a much smaller change than the plan implies.

**This is the single biggest premise-mismatch in the plan.** The plan author treats P0 as greenfield infrastructure. P0 is *augmentation* of existing infrastructure. Unless the plan re-scopes P0, the implementation PR will produce duplicate `phase`-derivation code paths and the inevitable divergence bug class.

### A6 Phase-naming collision (LifecyclePhase reuse) [VERDICT: FAIL]

**Claim under review** (PLAN §6 P0 + §7 q2): "Define LifecyclePhase enum (PRE_DAY0 / DAY0 / POST_RESOLUTION)" / "Phase enum naming: `LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` vs `LifecyclePhase.{ENTRY_PHASE, TERMINAL_PHASE, RESOLVED}`?"

**Verification**:
- `src/state/lifecycle_manager.py:9-19`: `LifecyclePhase` enum already exists with values `{PENDING_ENTRY, ACTIVE, DAY0_WINDOW, PENDING_EXIT, ECONOMICALLY_CLOSED, SETTLED, VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN}`.
- `src/state/lifecycle_manager.py:32`: `LIFECYCLE_PHASE_VOCABULARY = tuple(phase.value for phase in LifecyclePhase)` — referenced as a vocabulary token elsewhere.
- `src/state/lifecycle_manager.py:34-90`: `LEGAL_LIFECYCLE_FOLDS` codifies legal transitions per phase.
- `src/state/lifecycle_manager.py:105-109`: `TERMINAL_STATES` is *programmatically derived* from `LEGAL_LIFECYCLE_FOLDS` — adding a new enum will silently change the terminal-state set.

**The plan's proposed `LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` enum is a hard naming collision.** Either it overwrites the existing enum (breaking ~50 imports, hundreds of tests) or it's a separate enum with the same name (Python will allow the import to shadow but the static-analyzer tooling and the developer model will both be broken).

The conceptual distinction the plan wants to draw is real and useful: the position lifecycle's `DAY0_WINDOW` is per-position, while the proposed `PRE_DAY0/DAY0/POST_RESOLUTION` is *market-time* phase (function of `target_date` + city.tz, independent of any specific position). These are different concepts and should have *different names*.

**Required fix in plan**: rename the proposed enum to disambiguate. Suggested: `MarketPhase` or `SettlementPhase` (since it indexes by target_date settlement), with values `PRE_SETTLEMENT_DAY / SETTLEMENT_DAY / POST_RESOLUTION`. Document the relationship: a position's `LifecyclePhase` is partially determined by the market's `MarketPhase`, but they are not the same axis.

### A7 Day0Router move from runtime branch — refactor risk [VERDICT: PARTIAL FAIL]

**Claim under review** (PLAN §5 L3): "Day0Router's weight shift logic must be moved from a runtime `if mode == DAY0_CAPTURE` branch to an explicit phase parameter on every posterior-fusion call."

**Verification**:
- `src/signal/day0_router.py:49-86`: `Day0Router.route()` is a metric-dispatched signal router (high vs low temperature); it does **not** apply a "Day0-phase weight shift" in the L3 (posterior-fusion) sense. It *constructs* a `Day0HighSignal` or `Day0LowNowcastSignal` based on `temperature_metric.is_low()`.
- `src/engine/cycle_runtime.py:2083`: `if mode == deps.DiscoveryMode.DAY0_CAPTURE` *gates whether to fetch_day0_observation* — i.e., it controls whether observation is fetched at all, not how posterior is fused.
- `src/strategy/market_fusion.py`: posterior fusion is *not* weight-shifted by Day0Router; the fusion is alpha-based (`alpha * p_cal + (1-alpha) * p_market`) and alpha comes from spread/liquidity/level, not Day0 mode.

**The plan mischaracterizes Day0Router.** It is not a "Day0-phase weight shift" applied during posterior fusion — it is a *metric-dispatch signal constructor* that runs **only when the cycle is DAY0_CAPTURE mode**, fetching observation-aware signals that get fed into a *different* posterior path (the day0-specific p_vector path in `evaluator.py:1962`).

This means the proposed refactor in §5 L3 ("explicit phase parameter on every posterior-fusion call") is rooted in a misunderstanding. The actual refactor needed is "decouple the *decision to fetch day0 observation* from `mode == DAY0_CAPTURE`, instead from `position.phase == DAY0_WINDOW or candidate.market_phase == SETTLEMENT_DAY`". That's a different and smaller refactor than the plan describes.

Plan §8 A7 partially flags this ("does any existing site rely on that coupling for non-phase-related reasons?") but does not investigate. Verifying: only **5 source sites** branch on `DAY0_CAPTURE` (`grep -c` confirms: cycle_runner.py:318, cycle_runner.py:428, evaluator.py:931+943+955). The refactor is mechanically small. But the plan's framing is wrong.

**Required fix in plan**: §5 L3 must be rewritten to describe what Day0Router actually does (metric dispatch, observation injection) and what the actual coupling-to-mode is (5 specific call sites that gate on mode). Otherwise P3 will land a refactor that doesn't match the runtime model.

### A8 Test inflation floor [VERDICT: UNRESOLVED]

**Claim under review** (PLAN §8 A8): "What's the floor of must-have invariants vs nice-to-have coverage?"

**Verification**:
- 46 test files reference one of the 4 strategy_keys.
- Existing phase-related tests: `tests/test_day0_window.py`, `tests/test_day0_exit_gate.py`, `tests/test_lifecycle_terminal_predicate.py`, `tests/test_p1_save_order.py::test_terminal_states_constant_covers_all_terminal_phases`, `tests/test_cross_module_relationships.py:203` (`TERMINAL_PHASES = {"voided", "settled", "admin_closed", "quarantined"}`).

The plan does not specify a floor. Per Fitz's "test relationships, not just functions" rule (CLAUDE.md), the *minimum* relationship tests for P0 should be:
1. `decision_time` straddling midnight in city tz produces stable phase across cycle (A4 race).
2. Phase transitions on `position_current` follow `LEGAL_LIFECYCLE_FOLDS` (already exists; must not regress).
3. `TERMINAL_STATES` derived set is unchanged (already exists at `tests/test_p1_save_order.py:142`; must not regress).
4. Every CHECK constraint update has a corresponding test that demonstrates the new keys are accepted and old removed keys are rejected.

These four minima are not in the plan.

**Required fix in plan**: §6 P0 must enumerate a "minimum relationship-test floor" of at least the 4 above, plus any new ones generated by §4's strategy-key changes.

### A9 Rollback of in-flight positions [VERDICT: PARTIAL FAIL]

**Claim under review** (PLAN §8 A9): "if P2 changes exit semantics and a live position was opened under old semantics, what's the migration policy?"

**Verification**:
- `src/state/portfolio.py:439-630`: `evaluate_exit()` uses `ExitDecision(trigger=str)` — a free-form string, not an enum. Adding new triggers (T-B2 `HOLD_VS_REDEEM_YIELD_INVERSION`, T-B3 `SETTLEMENT_DRIFT_DECAY`) is mechanically additive.
- But `src/state/portfolio.py:1832-1833` lists trigger strings used in CHECK-style filters: `"EDGE_REVERSAL", "BUY_NO_EDGE_EXIT", "ENSEMBLE_CONFLICT", "DAY0_OBSERVATION_REVERSAL"`. New triggers must be added here.
- The plan §6 P2 says "Risk: HIGH (changes exit semantics for existing positions)" but does not address the operator-facing "if a position opened on day N under old semantics is still live on day N+1 under new semantics, which rule applies?"

The answer is consequential: a position opened under "settlement_capture entries with 1.0× Kelly" should not be re-evaluated as "Phase-B 0.3× sized, exit on `HOLD_VS_REDEEM_YIELD_INVERSION`" mid-flight — that would *force-exit positions whose entry justified larger sizing*. The plan needs to commit to either (a) feature-flag P2 by position-creation-time so existing positions retain old exit logic, or (b) accept that all positions will be re-evaluated under new logic and document the operator-visible behavior change.

**Required fix in plan**: §6 P2 must specify the position-creation-time gate.

### A10 Calibration_pairs_v2 cohort-split sample sufficiency [VERDICT: FAIL]

**Claim under review** (PLAN §8 A10): "Do we have enough per-phase samples per city × metric?"

**Verification**:
- `src/calibration/store.py:213-235` shows `calibration_pairs_v2` is keyed on `(city, target_date, temperature_metric, range_label, lead_days, ...)`.
- The plan does not cite *current row counts* per (city × metric × Phase-B) bucket. The operator's recent rebuild was on the unified corpus (n_mc=10000); it is unknown without a SQL query whether per-phase fits would have ≥30 positives per bucket (the threshold to avoid Platt overfitting).
- Combined with A2 (lead_days is already a Platt input), splitting cohorts is *strictly worse for sample efficiency*.

The plan defers this to "P4 (Risk: HIGH) requires Platt rebuild evidence" but should *gate the entire P4 packet on an upfront sample-count audit*. Until that audit shows per-phase per-bucket positives ≥30, P4 should not be planned at all.

**Required fix in plan**: §6 P4 must include a Phase 0 sub-step "run a SELECT COUNT(*) per (city × metric × phase_bucket_at_lead_days_24h) on `calibration_pairs_v2` and gate P4 on result". If the result is "insufficient samples for >50% of buckets", P4 is deleted.

---

## Additional vectors found (beyond §8)

### A11 Plan provenance header missing [VERDICT: FAIL]

**Verification**: `head -5 PLAN.md` shows no `Created: YYYY-MM-DD / Last reused/audited: YYYY-MM-DD / Authority basis: ...` header per CLAUDE.md "File-header provenance rule (mandatory)". The plan calls itself the file but the file itself doesn't have the provenance fields.

The first 4 lines do include "Created: 2026-05-04" + "Authority basis: operator directive" inline — partially complies in spirit. But the plan-file header rule is mandatory per CLAUDE.md and inline prose does not survive a future agent's grep for `Last reused/audited:`.

**Required fix**: add explicit `Last reused/audited: 2026-05-04` line. Trivially fixable.

### A12 §7 open decisions are blockers but not gated [VERDICT: FAIL]

**Verification**: Plan §7 enumerates 7 open decisions, item 7 of which is "critic scope". Items 1, 2, 3, 4, 5, 6 are not pre-decisions for P0 — they are open questions. The plan §0 says "implementation BLOCKED on critic adversarial review + operator approval of decisions §7", but the §7 questions are interleaved with critic-decidable items (e.g., q2 about enum naming is a critic-decidable point — see A6 above).

**Required fix**: §7 must split into (a) operator-decision-required (q1, q4, q5, q6) and (b) critic-or-implementation-decision (q2, q3, q7). Items in (a) need operator written confirmation in PR body before P0 merges.

### A13 No mention of `position_lots` table [VERDICT: PARTIAL FAIL]

**Verification**: `src/state/db.py:258` references `position_lots` table. The plan addresses `position_current` but not `position_lots`. If P0 adds a phase column to position_current (which it doesn't need to — A5), should `position_lots` also carry phase? The plan is silent.

**Required fix**: §6 P0 must enumerate every position-related table (position_events, position_current, position_lots) and state phase-handling for each.

### A14 §3.5 dormant pair (E4 inverse_relative_entry) — premise incompatibility [VERDICT: PARTIAL FAIL]

**Verification**: `src/strategy/kelly.py:67-75` already lists `shoulder_buy: 0.0` and `center_sell: 0.0` as dormant entries. Plan §4 lists "E4 inverse_relative_entry" as new but the underlying `shoulder_buy`/`center_sell` keys exist. Plan §6 P6 says "Activate shoulder_buy + center_sell after E1-E3 are stable" — coherent with current state, but the plan §2.3 table doesn't list these existing dormants as part of the "current taxonomy." Confusing.

**Required fix**: §2.3 table must include the two dormant entries (shoulder_buy, center_sell) with their current 0.0× Kelly so the "4 → 10" framing accurately reflects "6 currently registered (4 active + 2 dormant) → 10".

---

## Caveats requiring plan amendment before P0 starts

1. **REWRITE §0 + §6 P0**: existing `LifecyclePhase` enum + `phase` column on `position_current` + `lead_hours_to_settlement_close()` already exist. P0 is *augmentation*, not greenfield. (A1, A5, A6)
2. **RENAME proposed enum**: `LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` collides with the live `LifecyclePhase`. Use `MarketPhase` or `SettlementPhase` and document the relationship to the existing `LifecyclePhase`. (A6)
3. **DROP or REWRITE §5 L2 + §6 P4**: per-phase Platt cohort proposal contradicts the existing `lead_days`-as-input design that explicitly avoided bucketing. Need sample-count audit and Brier-improvement evidence before P4 is on the roadmap. (A2, A10)
4. **ADD CHECK-constraint migration plan to §6 P0**: 4-key set is hardcoded in 2 SQLite CHECK constraints + ~6 Python constants + 46 test files. Migration is BLOCKING risk, not HIGH risk. (A3)
5. **COMMIT to phase-from-decision_time invariant**: phase computation must be cycle-snapshotted, not wall-clock-derived per call site, with a relationship test for midnight straddle. (A4)
6. **REWRITE §5 L3**: Day0Router is metric dispatch, not posterior-fusion weight shift. Refactor needs to target the 5 specific `mode == DAY0_CAPTURE` branches, not a generalized "phase parameter on every fusion call". (A7)
7. **ADD relationship-test floor to §6 P0**: minimum 4 invariants enumerated in A8.
8. **GATE §6 P2 by position-creation-time**: existing in-flight positions retain old exit logic; new logic applies only to positions opened post-P2-merge. (A9)
9. **SPLIT §7 into operator-decisions vs critic-decisions** with explicit blocker labels. (A12)
10. **ENUMERATE every position-related table in P0** (position_events, position_current, position_lots) with per-table phase-handling. (A13)
11. **UPDATE §2.3 table** to include shoulder_buy, center_sell dormants so cardinality framing is accurate. (A14)
12. **ADD `Last reused/audited:` header field to PLAN.md** per CLAUDE.md mandatory file-provenance rule. (A11)

---

## Forbidden phrases audit

The plan does NOT contain "pattern proven" / "narrow scope self-validating" / "looks good". The plan §0 says "Status: PLAN ONLY — implementation BLOCKED on critic adversarial review" which is correctly framed, not rubber-stamped. Plan §8 enumerates 10 attack vectors honestly. Plan author's epistemic discipline on flagging A2/A4/A8/A10 risks is appropriate — the failures here are *premise verification* failures (didn't check existing `LifecyclePhase`/`phase`-column/`lead_hours_to_settlement_close`), not rubber-stamp failures.

---

## Verdict reasoning

**REJECT-AND-RESPLIT** rather than APPROVED-WITH-CAVEATS because:
- A1, A5, A6 collectively show the plan was written without verifying the existing phase machinery on disk. This is not a fixable caveat — it is a fundamental rewrite of §0, §6 P0, and §7 q2/q5.
- A2 + A10 show the per-phase calibration cohort proposal contradicts the existing Platt design and would *regress* sample efficiency. This is structural: the plan would damage live calibration if shipped as-is.
- A3 shows the CHECK-constraint migration is mis-classified as HIGH risk (it's BLOCKING).
- A7 shows §5 L3 mischaracterizes the runtime model and would lead to a wrong-shaped refactor.

The conceptual direction (Day0 = position lifecycle phase, not entry mode) is **correct and worth pursuing**. The implementation roadmap as drafted will produce broken PRs. Plan author should re-read `src/state/lifecycle_manager.py`, `src/state/projection.py`, `src/engine/lifecycle_events.py`, `src/engine/time_context.py`, `src/calibration/platt.py` *before* drafting v2 of this plan, and rewrite §6 P0 as augmentation of the existing phase machinery rather than greenfield infrastructure.

---

## Citation table (file:line evidence used)

| Claim | Evidence file:line |
|---|---|
| LifecyclePhase already exists with DAY0_WINDOW | src/state/lifecycle_manager.py:9-19 |
| LEGAL_LIFECYCLE_FOLDS enforces transitions | src/state/lifecycle_manager.py:34-90 |
| TERMINAL_STATES derived programmatically | src/state/lifecycle_manager.py:105-109 |
| phase_for_runtime_position maps states→phase | src/state/lifecycle_manager.py:143-182 |
| position_current has phase column | src/state/projection.py:6-38 |
| upsert_position_current writes phase | src/state/projection.py:87-125 |
| canonical_phase_for_position populates phase | src/engine/lifecycle_events.py:51, :73 |
| lead_hours_to_settlement_close in city.tz | src/engine/time_context.py:58-72 |
| DiscoveryMode peer modes | src/engine/cycle_runner.py:335-339 |
| _LIVE_ALLOWED_STRATEGIES set | src/control/control_plane.py:316 |
| KNOWN_STRATEGIES set | src/engine/cycle_runner.py:67 |
| STRATEGY_KEYS list | src/state/edge_observation.py:42 |
| CANONICAL_STRATEGY_KEYS (engine) | src/engine/cycle_runtime.py:40-46 |
| CANONICAL_STRATEGY_KEYS (state) | src/state/portfolio.py:50 |
| STRATEGY_KELLY_MULTIPLIERS | src/strategy/kelly.py:67-75 |
| decision_chain CHECK constraint | src/state/db.py:663-669 |
| strategy_health CHECK constraint | src/state/db.py:759-764 |
| calibration_pairs_v2 columns (no phase) | src/state/schema/v2_schema.py:267-298 |
| ExtendedPlatt: lead_days as input not bucket | src/calibration/platt.py:1-10 |
| Day0Router metric dispatch | src/signal/day0_router.py:49-86 |
| DAY0_CAPTURE branch sites (5 total) | cycle_runner.py:318,428; evaluator.py:931,943,955 |
| cycle_runtime DAY0_CAPTURE coupling | src/engine/cycle_runtime.py:2083 |
| ExitDecision uses trigger:str | src/state/portfolio.py:79-87 |
| Day0 exit triggers existing | src/state/portfolio.py:754,758,914,918,1832-1833 |
| hours_to_resolution computed in UTC | src/data/market_scanner.py:1000 |
| min_hours_to_resolution defaults | src/engine/cycle_runtime.py:1995-2004 |
| HEAD reference | d0259327e3fd46c3c2e2fc351676a2f887a38d03 |
