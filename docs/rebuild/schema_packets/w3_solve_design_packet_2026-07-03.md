# W3 — SOLVE design packet (2026-07-03, interface skeleton rev 1)

Basis: design doc §3.3 · architecture doc §1 SOLVE/exits rows + §4 decisions 1-2 · locate briefs
W3.{SEAM,MATH,EXIT,C4,FLAG} (scratchpad, verbatim anchors re-verified against worktree @ 8ad6aed7f).
Skeleton delivered under `src/solve/` (interfaces only; math bodies = sub-slice 2).

## ORCHESTRATOR DECISIONS — RESOLVED 2026-07-03

1. **CONFIRMED: two-phase ON-mode split.** Promotion evidence must isolate variables — comparing a
   multi-order executor against a single-order picker confounds solver quality with execution
   shape. Phase-1 (shim, single primary leg, decision-for-decision comparable) carries the
   settlement-graded evidence gate; phase-2 (bridge consumes SolutionPlan → W2.1 batch + conversion
   legs) is its own packet with its own gate.
2. **CONFIRMED: conversion-route builder lands in ON-phase-2**, preconditioned on the W2.4
   NEGRISK_SPLIT selector dry-run gate (deploy-gate already on record). Not a separate packet —
   phase-2 IS the execution-shape change.
3. **CONFIRMED: κ=1.0 until W5 haircut deletion (single-owner law).** Preserves like-for-like
   promotion evidence (today's effective behavior = full-Kelly then downstream haircut); κ
   ownership transfers atomically in the W5 commit that deletes kelly_multiplier.
   Double-shading unconstructable by KappaPolicy.__post_init__ — keep.
4. **CONFIRMED: coherence lockstep** — shim emits coherence_allows=True; the overlay's
   COHERENCE_BLOCKED guard retires in the flag-ON packet; OFF/G3 path untouched (byte-identity
   preserved). Divergence-event + priority-key rewire stays W4 scope per architecture §4.1.
5. **RULED: do NOT thread max_stake_usd into the legacy engine now.** It would change today's
   live-path behavior outside the W3 flag and break G3 OFF-path byte-identity; it is a dead-end
   fix on a component being replaced. The new solver is budget-aware by construction
   (WealthByOutcome.cash_usd first-class bound) and the CAS ledger (W1.1) hard-bounds spendable
   loudly. Legacy unconstrained-argmax characteristic RECORDED as known; interim exposure during
   promotion = downstream haircut + CAS ledger. If the wave branch deploys to live before W3
   promotion, this interim posture is explicitly accepted.

## ORIGINAL DECISION REQUESTS (record)

1. **Two-layer output (packet §3).** The seam contract returns ONE `selected: CandidateEconomics`
   (single native leg; bridge runs `enable_negrisk_routes=False` per the pr409 ROUTE-IDENTITY
   blocker — the submit path executes one leg). The SOLVE's product is a multi-order plan. Skeleton
   implements: `solve()→SolutionPlan` (truth) + `SolveEngineShim.decide()` (legacy shape, primary
   order → `selected`). CONSEQUENCE: in ON-mode-phase-1 the solver still submits only the plan's
   primary leg through the unchanged path; multi-leg execution (batch W2.1 + conversion legs)
   activates in ON-mode-phase-2 by extending the BRIDGE to consume SolutionPlan directly.
   Confirm the two-phase ON-mode split (recommended: yes — it keeps the G3/promotion evidence
   comparable decision-for-decision before changing execution shape).
2. **Conversion-route builder scope.** `negrisk_routes.conversion_routes` is `()` because no
   builder constructs conversion RouteCosts (W2.4 primitives exist, unpriced as routes). Flipping
   conversions executable = writing `_conversion_route()` in negrisk_routes + pricing legs via the
   W2.4 calldata cost model + the NEGRISK_SPLIT dry-run deploy gate. Decide: in W3 ON-phase-2 scope
   (recommended) or deferred to its own packet.
3. **κ single-owner ruling (packet §6) — confirm:** κ=1.0 while the downstream kelly_multiplier
   haircut lives; ownership transfers to κ in the SAME commit that deletes the haircut stack (W5).
   Enforced by construction (`KappaPolicy.__post_init__` forbids double-shading).
4. **Coherence lockstep (packet §7) — confirm:** shim emits `coherence_allows=True`
   unconditionally; the overlay's COHERENCE_BLOCKED guard (qkernel_spine_bridge.py:1684 region,
   checks `qkernel_execution_economics['coherence_allows']`) is retired in the same packet that
   flips the flag ON. OFF-mode untouched (G3 unaffected).
5. **max_stake_usd gap.** The reactor call site omits `max_stake_usd` into
   `decide_family_via_spine` (arrives None → stake ceiling = full book notional; the real cash
   bound is enforced downstream by the CAS ledger at reserve time). The solve consumes
   `WealthByOutcome.cash_usd` (one-snapshot spendable) as a FIRST-CLASS bound instead. Decide:
   also thread max_stake_usd at the reactor call site (one-line, closes a pre-existing gap) or
   accept ledger-only bounding during promotion.

## 1. Seam contract (verbatim, frozen)

- Construction seam: `qkernel_spine_bridge.py:1332` `FamilyDecisionEngine(...)` with kwargs:
  `fresh_model_reader, day0_reader, predictive_builder, enable_negrisk_routes=False,
  family_book_builder, route_set_builder, selection_objective="roi_frontier"`
  (+ `n_band_draws`, `band_alpha`). The shim accepts the same surface.
- Call seam: `qkernel_spine_bridge.py:1379` `engine.decide(case, omega, {}, portfolio=, matrix=,
  captured_at_utc=, sizing_candidates=, max_stake_usd=, shares_for_routing=, served_joint_q=,
  served_band=, served_payoff_q_lcb_by_side=)` → `FamilyDecision`.
- Return shape: `family_decision_engine.py:583-635` — 12 spec fields + 3 provenance fields
  (`candidate_decisions, market_implied_q, portfolio_comparisons`). Consumers:
  proof overlay `qkernel_spine_bridge.py:1684` (reads q_posterior/q_lcb_5pct/trade_score/
  qkernel_execution_economics/selection_authority_applied); facts writer
  `event_reactor_adapter.py:4415` (reads candidate_decisions/selected/decision_id/receipt_hash/
  omega.bins via getattr-with-default — SILENT-degrade class). Guard: shim's
  `_assert_contract_fields` (solver.py) makes the break loud.
- One-belief law: served q/band pass through verbatim (`_served_joint_belief_from_proofs`
  qkernel_spine_bridge.py:426-539 fails closed); the solver NEVER rebuilds σ or q.

## 2. Package layout

`src/solve/{__init__,types,scenario_service,menu_adapter,solver,kappa,exits}.py` — see module
headers. `TransitionalIndependentProduct` has a real body (index-paired product measure,
deterministic hash); everything else is contract-annotated `NotImplementedError`.

## 3. Two-layer output — see decision 1. SolutionPlan fields carry the evidence hooks:
`delta_u_baseline_top1` (solver≥picker property), `correlation_rail` (§4 decision 2 receipt stamp),
`scenario_sample_hash`/`menu_hash`/`q_version` (receipt anchors), `safe_prefix_index` per order
(W2.1 decomposition).

## 4. Property-test anchors (math core acceptance)

solver ≥ top-1 on every fixture (top-1 is feasible ⇒ optimum dominates) · zero-edge → zero-stake ·
monotone in q · κ scales before repair; repair re-verifies rounded plan under worst-price checks
else no-trade · every order stamps q_version · plan-level robust ΔU uses band samples (payoff_vector
precedent: samples @ payoff then quantile — NEVER the precomputed per-bin q_lcb; W3.MATH brief risk
#3) · exits: `marginal_exit_condition` agrees with the objective's marginal direction at the
current holdings point.

## 5. Conversion legs — see decision 2. Until the builder lands + dry-run gate clears, MenuItems of
conversion kinds appear only if negrisk_routes emits them (today: never), and always
executable=False pass-through — the menu adapter must not synthesize routes.

## 6. κ / double-shading — see decision 3. `kappa.py` enforces single-owner by construction.

## 7. Coherence — see decision 4. §4 decision 1 (veto → router) lands in two steps: W3 stops
consuming the veto (shim + overlay lockstep, ON-mode only); W4 re-emits the report as a divergence
event + re-decision priority key.

## 8. G3 byte-identical-OFF harness (newly authored; no existing harness — W3.SEAM brief)

Per P2 gates table (P2_sequence_and_critical_path.md:120-142, G3 at :131): with the promotion flag
OFF, output is byte-identical to current live. Design: a pytest harness that (a) drives
`decide_family_via_spine` over a recorded fixture corpus (reuse
tests/integration/test_qkernel_spine_routing.py fixtures) with the W3 flag absent vs OFF —
asserting the returned SpineDecisionResult AND the facts-writer payloads are byte-identical
(serialize + compare, not spot fields); (b) proves the flag site is the ONLY divergence point by
grepping the flag key's consumers (must be exactly one: the bridge construction branch). The flag:
`feature_flags.w3_solve_enabled` (settings feature_flags block precedent, W3.FLAG brief), default
OFF, lifecycle registered with a deletion deadline at promotion — never joins the 8 standing flags
without one (FLAG-BLEED risk noted in W3.FLAG brief).

## 9. Promotion lifecycle

G0 unit/property suite → G3 (above) → ON-phase-1 (shim path, single-leg, evidence accrues) →
settlement-graded evidence gate (grade_receipt lane; capital-weighted after-cost EV > 0 at n≥30
settled, per P2 G4/G5 shape) → operator ARM flip (existing `require_operator_arm()` token,
main.py:824-855 — NO second gate) → ON-phase-2 (SolutionPlan execution: batch + conversions) →
flag deleted. Registries: module_manifest/source_rationale/test_topology rows + src/solve/AGENTS.md
in the FIRST code-landing commit (not applied with this skeleton — listed to not repeat the
src/decision omission).

## 10. C4 swap path

`ScenarioService` is the only seam: C4 returns the same `ScenarioSet` shape with real cross-family
structure (family_slices already in the type). Reusable math: `correlation_shrinkage.py`'s
regime-agnostic Ledoit-Wolf (strip the WeatherRegimeTag cache/taxonomy — W5 deletions). Backing
data: `settlement_outcomes` (city,date,metric) joins. Granularity mismatch to resolve in the C4
packet: risk_allocator correlation_key is city-cluster-grained; WeatherFamilyKey is
city+date+metric-grained (W3.C4 brief risk #2). Until C4: `correlation_rail="caps"` on every plan.

## APPENDIX — CONSULT REV-2 RULINGS (2026-07-03)

External deep review (ChatGPT Pro consult REQ-20260702-212900-e935c9) ruled **NO-GO** for
building the math core on the rev-1 skeleton types and identified six blockers. The orchestrator
ACCEPTED all six. This appendix records the rulings; they are law for the W3.2 packet and
SUPERSEDE the rev-1 type shapes (`ScenarioSet`, `WealthByOutcome`, the Mapping-payoff `MenuItem`,
the certificate-less `SolutionPlan`) wherever they conflict.

1. **Joint outcome atom axis (BLOCKER).** `ScenarioSet` (concatenated marginal bins) → 
   `JointOutcomeScenarioSet`: `atoms: tuple[JointOutcomeAtom,…]` (each atom maps
   `family_key→bin_id`), `q_draws[n_draws, n_atoms]`, optional `draw_weights`, derived
   `family_projections`, and ambiguity metadata (`alpha`, `band_hashes_by_family`,
   `provider`+`provider_version`, `semantics` ∈ {POSTERIOR_Q_DRAWS, PRODUCT_MEASURE,
   MEASURED_JOINT}). `WealthByOutcome` → `WealthStateByAtom` on the SAME atom axis, including
   reservations / resting orders / unsettled proceeds / `ledger_snapshot_id` (CAS ledger W1.1 is
   the source of truth). Single-family W3 is the degenerate projection (one entry per atom). This
   is what makes the C4 provider swap a no-op for the solver.

2. **Transitional rail scope + correlation tightening (BLOCKER).** Index-pairing is INVALID for
   sorted/quantile-ordered draws (comonotone, not independent). Ruling: the transitional service
   serves **single-family only**; multi-family joint construction **FAILS CLOSED** until C4.
   Numeric proof (two identical q=0.60, f=0.20 even-money bets): independent expected log **+0.039**
   vs comonotone **−0.0024** (both-lose prob 0.16→0.40). Caps limit loss, they are NEVER a
   log-utility correctness license. Any future degraded multi-family mode stamps
   `correlation_rail="caps_degraded_not_optimal"` with **promotion evidence BLOCKED**.

3. **RepairCertificate (BLOCKER).** `SolutionPlan` gains `RepairCertificate`: continuous objective,
   repaired objective under the worst-price model, tick/min-size deltas, promoted/dropped items,
   ≤15 batch partition, per-safe-prefix objective bounds, budget after repair. A non-empty plan
   REQUIRES a certificate with repaired ΔU > 0 (enforced in `SolutionPlan.__post_init__`).

4. **LegacyDecisionProjection + phase-1 grading invariant (BLOCKER).** Shim-side artifact:
   `primary_order_id`, `projected_selected`, the STANDALONE re-scored ΔU of the primary leg alone
   at post-downstream-haircut size, `projection_reason`, `downstream_haircut_alive`. **Phase-1
   invariant:** promotion evidence grades the projection, NEVER
   `SolutionPlan.expected_delta_log_wealth`; if the standalone primary-leg ΔU ≤ 0 (the leg is only
   good because of unexecuted hedges) → **NO-TRADE in phase 1**.

5. **Validators (BLOCKER).** `JointOutcomeScenarioSet` constructor validates finite values, simplex
   rows (POSTERIOR_Q_DRAWS), nonnegative weights, shape coherence, canonical float64 dtype before
   hashing; the scenario hash covers provider+version+atom axis+draw weights+semantics.
   `_assert_contract_fields` → `validate_family_decision_contract` (presence + non-null semantics +
   candidate_decisions tuple + exactly-one selected/no_trade_reason), backed by a sentinel
   facts-writer test proving no getattr default fires.

6. **Robust objective + smaller rulings.** Objective is lower-tail **CVaR** (concavity-preserving),
   NOT the raw α-quantile — the legacy payoff_vector unimodality assertion is unsafe and is not
   inherited (tests include a non-unimodal counterexample). Dominance baseline = top-1 candidate in
   the SAME feasible set (menu/budget/repair/worst-price), not the legacy raw score. `max_stake_usd`
   removed from core `solve()` (shim-only → cash constraint). Per-LEG tick/min-size on MenuItem.
   κ is a typed `Kappa` value object (Decimal, canonical serialization). MenuItem payoff is a typed
   `AtomPayoffProjector` over atoms. Maker lane disabled (W3 taker-only). `exits.py` stays
   interface-only but gains `ZeroWealthOutcomeError` + `ExitPrecheckResult` (tripwire precedence
   consumed BEFORE economics). `PlannedOrder` gains `plan_generation` + `ledger_snapshot_id` +
   `invalidation_snapshot_id` (phase-2 INV-28/29 envelope metadata; fields now, wiring later).
