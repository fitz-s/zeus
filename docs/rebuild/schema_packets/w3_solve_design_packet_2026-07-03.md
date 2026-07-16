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
headers. **SUPERSEDED by the CONSULT REV-2 appendix below:** the scenario/wealth axis is now the
joint outcome ATOM axis (`JointOutcomeScenarioSet` / `WealthStateByAtom`), and
`TransitionalIndependentProduct` serves the SINGLE-FAMILY degenerate case only — multi-family joint
construction FAILS CLOSED until C4 (it is NOT an index-paired product; index-pairing is not a
certifiable independent product). `solver.py` + `menu_adapter.py` + `scenario_service.py` + `kappa.py`
have real bodies; `exits.py` stays interface-only (typed error + precheck).

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

`ScenarioService` is the only seam: C4 returns the same `JointOutcomeScenarioSet` shape (joint
outcome ATOMS + `q_draws` over atoms + `semantics="MEASURED_JOINT"`) — NOT the rev-1 concatenated
`ScenarioSet` with family_slices — so the solver needs no code change at the swap. Reusable math:
`correlation_shrinkage.py`'s
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

## APPENDIX — CONSULT REV-2 FOLLOW-UP RULINGS (2026-07-03)

Second consult round (answer_w3_rev2_followup.txt): interface foundation CLEARED, GO for the shim
sub-slice, NO-GO for phase-1 promotion evidence until the math-core gaps below close. All accepted.

**Blockers.**
1. **Executable-cash budget constraint.** `W_end > 0` does NOT imply affordability — a holding that
   inflates the worst atom's wealth (or mutually-exclusive claims) can leave positive terminal wealth
   in every atom while the upfront outlay `Σ positive_cost·x` exceeds spendable cash. A linear budget
   `Σ cost_i·x_i ≤ cash_usd` (sells free budget) is now enforced in `_feasible_hi`, in `_repair`
   (drop least-valuable positive-cost orders until affordable), and proven by the certificate
   (`budget_after_repair_usd ≥ 0`, enforced in `SolutionPlan.__post_init__`; per-prefix budgets are
   nonnegative as prefixes are cumulative subsets).
2. **Route depth cap.** `MenuItem.max_units = min(route.max_shares, route.shares)` — never size past
   the depth `avg_cost` was actually walked at. Per-level cost curves are phase-2.

**Highs.** (3) Phase-1 executable menu = DIRECT NATIVE routes only; synthetic/pair/basket/conversion
routes are menu-visible but non-executable (`PHASE1_NON_DIRECT_ROUTE`) — their single-instrument
payoff projection is wrong and multi-leg atomicity would be lost; `PlannedRoute`/`PlannedLeg` is the
phase-2 shape. (4) `RepairCertificate.chosen_source ∈ {joint, top1}` and `continuous_objective` is
taken from the CHOSEN parent vector. (5) Safe-prefix positivity: every per-prefix objective bound must
be `> 0` (enforced at construction); an unsafe multi-order plan (best single prefix negative) emits
`UNSAFE_PREFIX_DECOMPOSITION`. (6) CVaR filters zero/negative weights before the sort (0·−inf=NaN
guard) and `JointOutcomeScenarioSet` rejects non-positive weights.

**Mediums.** Row-sum==1 for all three semantics; per-leg NO-ladder quantization (NO buys walk
`no_asks`); phase-1 leaves PRICE to the submit path (`PlannedOrder.price=None`); `PlannedOrder`
`__post_init__` validation; AtomPayoffProjector full atom coverage (`structural_zero` opt-in);
`MIN_TAIL_DRAWS=20` + point-belief STAMPED in diagnostics (`tail_floor_ok`, `effective_tail_draws`,
`point_belief`); AST sentinel tied to the real `_record_qkernel_selection_family_facts` consumer.

**Optimizer ruling — Rockafellar–Uryasev lower-CVaR convex program is authoritative.**
The continuous solve uses a deterministic tail-mixture cutting-plane master. Each discovered
worst-tail mixture adds a concave-superlevel constraint; convergence requires the master's `eta`
upper bound to meet the directly evaluated lower CVaR. Coordinate ascent, budget-neutral pairwise
exchange, diversified multi-start, and radial balanced growth remain only as a feasible warm start
and best-single-item dominance floor. They are not a globality certificate. Globality is attacked
independently by exact 0.01-grid YES-best/NO-best fixtures, label-mirror invariance, 2-D/3-D
exhaustive grids, and the known asymmetric multi-item counterexample that defeats the former warm
start. Any grid-oracle win over the continuous authority is a STOP, not a tolerance widening.

**Cross-family one-order ruling — compare certificates, never fabricate a joint law.**
When an epoch may add exactly one order, native YES and NO candidates from different
families may compete without a cross-family joint distribution only through the dedicated
single-order selector. It integrates the complete fee-adjusted ask curve at 0.01-share
granularity. A candidate carries no caller-authored q: a family witness supplies one complete
MECE row-simplex matrix with ordered `(bin_id, condition_id, YES token, NO token)` membership;
YES takes the bound column and NO its pointwise complement. Independent current resolvers confirm
the probability certificate and exact native book/curve. A current venue-universe witness proves
that every active family is present; a bounded reactor page is never renamed "global". The wealth witness binds ledger
generation, reconciled positions, reservations, spendable cash, and wealth bounds. Unknown
coupling is lower-bounded by evaluating wins against the portfolio ceiling and losses against
the floor. Before that score is admitted, lower-tail-CVaR payoff-side win probability must be
strictly above one half, so the exact binary payoff vector has positive median payoff; otherwise
the typed result is `ROBUST_MAJORITY_LOSS`. A full-size and every-FAK-prefix positive
reduce-only SELL is a current-endowment dominance action: it consumes no new capital and releases
cash immediately, so it is selected before adding new BUY risk; multiple such SELLs rank by
lower-CVaR Δlog-wealth. When no positive SELL exists, BUYs rank by lower-CVaR Δlog-wealth per
remaining family-resolution hour. That horizon is not caller-authored: the current immutable
family city and target local date plus configured settlement timezone derive it, and both current
scope and venue-universe witness identities bind it. Ties use raw robust Δlog, robust Δlog per
dollar, then lower cash. Expected value is diagnostic only, not realized capital gain. This is a
current certified resolution-horizon rate, not a claim about future opportunity arrivals or a
learned continuation value. Maker-contingent assets and any stale/mismatched certificate fail
closed. The event reactor now owns
`prepare all -> choose one -> JIT recapture -> submit once`; implementation alone is not a
live-optimality claim, which additionally requires a current complete-scope receipt, exact
one-submit evidence, and fresh post-submit capital/venue reconciliation.

**Time-sensitive current-truth acquisition.** Gamma tradeability/token metadata and CLOB native
order books are independent external facts joined by condition/token identity. When the current
book cache proves a fresh capture is required, the live adapter may fetch the complete bound CLOB
token universe concurrently with the current Gamma bind, then join both results inside one
authority-bounded capture window. The optimization must not shrink the family/token universe,
reuse an expired book, infer tradeability from the CLOB response, or bypass the existing token,
metadata, completeness, quote-TTL, and JIT winner recapture checks. A token identity change makes
the speculative book batch unusable and falls back to the ordinary current capture/fail-closed
path. This changes latency only; q, Fractional Kelly, BUY/SELL/HOLD/CASH ranking, operator pause,
and venue actuation law are unchanged.

**α-sensitivity replay (promotion-evidence-gate item, NOT a solver change).** Before promotion, replay
the W3 fixture corpus at α ∈ {0.01, 0.05, 0.10} and require decision-stability bands (diff the
selected/no-trade transitions) so CVaR conservatism is not an artifact of one tail level.

**Re-scoped deferral.** `build_wealth_by_atom` minimal ENTRY-SIDE body is now IN SCOPE for sub-slice 3
(not deferrable — evidence on a wealth object the live shim cannot derive is not evidence): atom wealth
from current family holdings + spendable cash net of reservations (CAS ledger read) + `ledger_snapshot_id`.
Implementation stays sub-slice 3; the full C5 exit ledger builder remains deferred.

## APPENDIX — SUB-SLICE 3 (W3.3): SHIM BODY + ENTRY-SIDE WEALTH BUILDER (2026-07-03)

Scope (phase-1 evidence chain; NO production wiring — bridge untouched, nothing imports the shim
outside tests; the seam swap + G3 harness are the next packet).

1. **`build_wealth_by_atom` (exits.py, entry-side).** Pure core with INJECTED inputs (W3.3 ruling):
   `W_a = spendable_cash (net of reservations) + per-atom holdings payout`; strict positivity →
   `ZeroWealthOutcomeError`; stamps `ledger_snapshot_id`. Reaches NO ledger connection itself — the
   seam swap threads the real CAS-ledger read at the bridge; the shim/tests inject the values.

2. **`SolveEngineShim.decide()`.** COMPOSES an inner `FamilyDecisionEngine` (same ctor surface) for
   the FamilyDecision scaffolding (predictive, served joint_q/band pass-through, family_book,
   market_coherence, market_implied_q, enumerated candidate economics) and REPLACES the selection
   with the joint solver over a `SolveMenu` built from the same route surface. Endowment wealth
   VECTOR = the legacy `portfolio` A_y (`portfolio.a(bin)`) — LIKE-FOR-LIKE with the picker; the
   separate spendable-cash BUDGET is injected. The primary leg (safe_prefix_index 0) is re-scored
   STANDALONE at its post-downstream-haircut size; `coherence_allows=True` (lockstep); `receipt_hash`
   re-stamped; `validate_family_decision_contract` before return.

3. **`LegacyDecisionProjection` + phase-1 grading.** The projection's standalone ΔU and post-haircut
   size are STAMPED into `selected.optimal_delta_u` / `selected.optimal_stake_usd`, so the existing
   proof overlay / facts writer grade the PROJECTION, never the joint plan's ΔU. Phase-1 gate:
   primary leg must be direct-executable AND its standalone post-haircut ΔU `> 0`, else typed
   no-trade (`PHASE1_PRIMARY_LEG_NOT_TRADEABLE`). Note: the solver's safe-prefix positivity already
   refuses a hedge whose primary leg is negative alone (`UNSAFE_PREFIX_DECOMPOSITION`) BEFORE the
   shim, so a "primary leg good only because of an unexecuted hedge" surfaces as a shim no-trade; the
   shim gate is the defensive second layer.

JUDGMENT CALLS (flagged for review):
- **Downstream haircut re-scoring is CONFIG-FACTOR ONLY at decide() time.** The full
  variance-adjusted haircut (`SizingContext`/`evaluate_kelly`, event_reactor_adapter.py:5657) needs
  bankroll + portfolio-state provider + lead_days that are NOT in the frozen :1379 kwargs. The shim
  reproduces only the config `kelly_multiplier` base factor (`post_size = pre_size × multiplier`) and
  stamps both pre- and post-haircut sizes; the promotion-evidence gate grades the ACTUAL submitted
  size from the settlement receipt (where the full haircut is known). No bankroll/portfolio side
  channel was invented (per the STOP rule).
- **Composition applies the inner engine's q_lcb / selection-calibrator guards** to the legacy
  candidate economics the shim reuses; the solver selects on its own (unguarded) menu. Coherence veto
  is bypassed per the lockstep ruling; the other guards (W5-deletion targets) still run in the inner
  engine. Flagged as a phase-1 like-for-like nuance for the seam-swap review.
- **Wealth vector = `portfolio.a(bin)`** (legacy A_y) with injected spendable as the budget; when no
  spendable provider is injected (pre-seam-swap default) the shim floors spendable at the minimum
  endowment so it never fabricates cash the ledger has not confirmed.

### PHASE-1 EVIDENCE-GRADING CONTRACT (approved 2026-07-03)

Binding rule for how phase-1 promotion evidence treats the primary-leg size and ΔU:

- The `LegacyDecisionProjection` stamps BOTH the pre-haircut size and the **config-multiplier**
  post-haircut size (`post = pre × settings.sizing.kelly_multiplier`), and its standalone
  primary-leg ΔU (re-scored at that post-haircut size) flows into `selected.optimal_delta_u` /
  `selected.optimal_stake_usd` — the overlay/facts-writer path.
- The config-multiplier post-haircut size is a **reproducible APPROXIMATION** computed at decide()
  time. It MAY DIFFER from the full variance-adjusted downstream haircut
  (`SizingContext`/`evaluate_kelly`, event_reactor_adapter.py:5657), which depends on
  bankroll + portfolio-state provider + lead_days that are NOT available in the frozen :1379 kwargs.
- **The settlement-graded ACTUAL submitted size is AUTHORITATIVE.** The promotion-evidence gate
  grades the actual submitted size read from the settlement receipts, not the projection's
  approximation. Receipt = truth; projection = reproducible approximation; they reconcile at
  grading time. No bankroll/portfolio side channel is threaded into the shim to close the gap
  (STOP-rule: never invent a side channel the frozen contract does not carry).

## APPENDIX — SEAM SWAP + G3 HARNESS (W3.4, 2026-07-03)

The final W3 packet: the time-boxed promotion flag, the single-point seam edit, and the G3 gate.

**Flag: `feature_flags.w3_solve_enabled`** — TIME-BOXED, DELETED AT PROMOTION (no-permanent-flags
law). Default ABSENT = OFF; a config-read fault is OFF. Accessor `qkernel_spine_bridge.w3_solve_enabled()`
mirrors `qkernel_spine_enabled()` and reads per call (no import-time cache, so G3 absent-vs-OFF sees
the same read each call). Operators enable by adding `feature_flags.w3_solve_enabled: true` to
config/settings.json (uncommitted); the flag is registered here + in the accessor docstring (the
committed record). DELETION DEADLINE: removed in the promotion commit that ARMs the solver (P2
sequence: G0 → G3 → evidence gate → ARM flip → flag deleted).

**Seam edit (qkernel_spine_bridge.py, MINIMAL — reviewed line-by-line).** Two additive helpers next
to the existing flag accessor (`w3_solve_enabled()`, `_wrap_engine_with_solve_shim(engine)` — the
latter LAZY-imports `src.solve` inside the ON branch) and a 4-line guard immediately before the
existing `engine.decide(...)` call:

    if w3_solve_enabled():
        engine = _wrap_engine_with_solve_shim(engine)

OFF/absent → the guard is skipped and `engine` stays the `FamilyDecisionEngine`; the `engine.decide(...)`
call is UNCHANGED (byte-identical legacy path). ON → `engine` is wrapped in `SolveEngineShim` (which
composes the same engine via `engine=`), and the same `engine.decide(...)` dispatches polymorphically
to the shim. NOTHING else in the bridge changes.

**Ledger handle (STOP-reported).** `decide_family_via_spine` carries no CAS-ledger / spendable-cash
handle in its signature; threading `available_pusd` would require adding a parameter — touching the
caller beyond the construction site. Per the ruling, the sanctioned fallback landed:
`_wrap_engine_with_solve_shim` constructs the shim with NO `spendable_cash_provider`, so the shim's
CONSERVATIVE ENDOWMENT FLOOR applies (spendable = min per-atom endowment; never fabricates cash the
ledger has not confirmed). The real CAS-ledger `available_pusd` read is threaded by the seam-swap
operator by hand (add a `spendable_usd_provider` param to `decide_family_via_spine` + its one caller).

**G3 gate (tests/integration/test_w3_solve_seam_g3.py).** (a) absent-vs-OFF byte-identity over a
realistic fixture corpus (serialized SpineDecisionResults identical); (b) single-divergence-point
(AST: `w3_solve_enabled` consumed at exactly one call site, `_wrap_engine_with_solve_shim` called
once); (c) ON-mode: the shim physically runs (`src.solve.solver` imported, decisions pass
`validate_family_decision_contract`, and the ON no-trade reason is the solver's — DIVERGING from the
legacy picker's on the same inputs); (d) OFF-path import-isolation (a subprocess drives a full decide
with the flag OFF and asserts `src.solve` was never imported). The trade + projection-stamped path is
covered by the shim unit tests (test_shim_decide); the realistic spine fixtures no-trade in-test
(wide robust band / cold bankroll), exactly as the existing spine-routing suite handles with skips.

**Promotion sequence pointer:** G0 unit/property suite (tests/solve) → G3 (this harness) → ON-phase-1
settlement-graded evidence gate (grades the receipt actual submitted size) → operator ARM flip
(existing require_operator_arm) → ON-phase-2 (batch + conversions) → flag deleted.
