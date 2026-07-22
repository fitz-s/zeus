# src/solve AGENTS — Zone K2 (SOLVE, order-engine rebuild W3)

Module book: none yet (rebuild-in-flight; design lives in docs/rebuild/)
Machine registry: `architecture/module_manifest.yaml`
Design authority: `docs/rebuild/order_engine_first_principles_design_2026-07-02.md` §3.3,
`docs/rebuild/schema_packets/w3_solve_design_packet_2026-07-03.md`

## WHY this zone matters

SOLVE is the order-engine rebuild's decision core: given ONE served belief (joint q + band)
and the executable venue menu, it plans the multi-order action that maximizes robust
expected log terminal wealth against the current portfolio as ENDOWMENT. It replaces the
top-1-candidate picker (`src/decision/family_decision_engine.py`) at exactly one seam —
`src/engine/qkernel_spine_bridge.py:1332` — behind the time-boxed `w3_solve_enabled`
promotion flag. The cross-family global single-order selector is wired through the reactor's
`prepare all -> choose one -> JIT recapture -> submit once` epoch; live authority still requires
current scope, book, wealth, probability, RiskGuard, and venue receipt evidence each cycle.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `types.py` | Typed menu / endowment / scenario / plan I/O. `PlannedOrder.q_version` mandatory (W1.2 stamp law); every plan stamps `correlation_rail`. | MEDIUM — contract surface |
| `scenario_service.py` | `ScenarioService` protocol + `TransitionalIndependentProduct` (index-paired product measure). The ONE seam C4 swaps; solver never re-samples. | MEDIUM — one-belief law |
| `menu_adapter.py` | `NegRiskRouteSet` (+holdings/cash) → `SolveMenu`. PURE reshaping: no re-pricing, no dropping non-executable items, no synthesizing conversion routes. | MEDIUM |
| `solver.py` | `solve()` → `SolutionPlan` (joint log-utility optimizer + κ + discrete repair) and `SolveEngineShim` (FamilyDecision-shaped seam, sub-slice 3). | HIGH — outcome-deciding math |
| `kappa.py` | Fractional-shading policy. κ=1.0 while the downstream kelly_multiplier haircut lives (single-owner law, enforced by construction). | HIGH — double-shading guard |
| `exits.py` | Exits-as-same-solve (C5 marginal rule). Bodies land in a later sub-slice. | MEDIUM |

## Domain rules

- **Joint outcome atom axis (consult REV-2).** Scenarios (`JointOutcomeScenarioSet`) and
  wealth (`WealthStateByAtom`) live on ONE axis of `JointOutcomeAtom` — each atom a full
  joint outcome `{family_key: bin_id}`. Single-family W3 is the degenerate case; per-family
  marginals are DERIVED projections. This is what makes the C4 provider swap a solver no-op.
- **One-belief law.** The solver NEVER rebuilds q or the band. The bridge serves them
  verbatim (`_served_joint_belief_from_proofs`); the ScenarioService integrates over the
  served `q_draws` (band SAMPLES, never the precomputed per-bin q_lcb — W3.MATH brief risk #3).
- **Objective is joint CVaR, not greedy, not a raw quantile.** `solve()` maximizes the
  lower-tail **CVaR** at the band's α of per-draw expected Δlog-wealth of the WHOLE stake
  vector against `WealthStateByAtom`. CVaR (not the α-quantile) is deliberate: it is
  concavity-preserving. The continuous authority is the Rockafellar–Uryasev lower-CVaR
  convex program; the legacy payoff_vector "quantile-of-concave is unimodal" assertion is
  UNSAFE and not inherited.
  Deterministic (no RNG, no wall-clock): coarse-to-fine 1-D grid per coordinate, seeded at
  the best single item so the plan dominates the top-1 picker.
- **Single-family only.** Multi-family joint scenarios FAIL CLOSED until C4
  (`MultiFamilyJointUnavailableError`) — index-pairing is not a certifiable independent
  product, and caps limit loss, they never license the log-utility objective.
- **Cross-family comparison is one-order-only and certificate-bound.** It does not fabricate
  a joint distribution. A candidate carries no self-authored q. One family witness carries the
  complete MECE row-simplex draw matrix and ordered `(bin, condition, YES token, NO token)`
  bindings; YES consumes its bound column and NO the pointwise complement. Independent current
  resolvers must confirm the probability, native ask-depth/fee/tick/book, and reconciled wealth
  identities before scoring. A current venue-universe witness must prove every active family is
  represented; a partial reactor page is not global and fails closed. One `PortfolioWealthWitness` binds ledger generation, positions,
  reservations, spendable cash, and wealth bounds. The candidate's binary log-wealth projection
  uses the same spendable-cash baseline in both branches plus the minimum exact same-family payout
  in each branch. Cross-family holdings enter neither branch until a joint law exists; coupling a
  candidate loss to the global floor and its win to the unrelated portfolio ceiling invents a
  correlation and can make an existing winner freeze every new family. Cross-family exposure stays
  on the correlation-cap rail. Stale, mismatched, maker-contingent, or
  non-positive candidates are unrankable. Before sizing a new BUY, the current owner strategy's
  native entry-price floor removes unlicensed longshots from the feasible set. Inside that set,
  admission is `q_lcb > fee-inclusive executable cost` plus positive robust delta-log wealth and
  EV, never a price-independent `q > 0.5` wall; the terminal median follows the probability branch.
  A reduce-only SELL is scored against HOLD instead: its
  robust incremental log-growth and EV must both be positive for the full order and every possible
  FAK fill prefix, even when the favorable SELL branch itself is below one half.
  A full-size and every-FAK-prefix positive reduce-only SELL remains eligible, but direction does
  not override the capital objective: rank all positive BUY and SELL alternatives by
  current-family lower-CVaR Δlog-wealth per remaining family-resolution hour. The horizon is
  derived from the immutable family city and target local date plus the configured settlement
  timezone, and is bound into the current scope/universe witness identity — never authored by a
  candidate. Numerical ties prefer higher robust Δlog, then robust Δlog per dollar, then lower
  cash. Expected value remains diagnostic and must never be named realized capital gain.
  This is not multi-order portfolio optimality.
- **κ single-owner, typed.** κ=1.0 throughout W3/W4 (the downstream haircut still shades);
  `KappaPolicy.__post_init__` makes a κ<1 with the haircut alive unconstructable. κ is a
  typed `Kappa` Decimal value object. Ownership transfers atomically in the W5 commit that
  deletes `kelly_multiplier`.
- **Executable-cash budget is a hard constraint, separate from `W_end > 0`.** `W_end > 0` does
  NOT imply affordability (a holding inflating the worst atom, or mutually-exclusive claims, can
  keep terminal wealth positive while the upfront outlay exceeds cash). `Σ cost_i·x_i ≤ cash_usd`
  is enforced in `_feasible_hi`, in `_repair`, and proven by `RepairCertificate.budget_after_repair_usd ≥ 0`.
- **Route sizes are capped at the priced depth.** `MenuItem.max_units = min(route.max_shares,
  route.shares)` — `avg_cost` was walked at `route.shares`; never size past it (per-level cost
  curves are phase-2).
- **Phase-1 executable menu = DIRECT NATIVE routes only.** synthetic/pair/basket/conversion routes
  are menu-visible but non-executable (`PHASE1_NON_DIRECT_ROUTE`); their single-instrument payoff
  projection is wrong and multi-leg atomicity would be lost (phase-2 `PlannedRoute`/`PlannedLeg`).
- **The optimizer is a certifying lower-CVaR program.** A deterministic tail-mixture
  cutting-plane master solves the Rockafellar–Uryasev convex program. Coordinate ascent,
  budget-neutral pairwise exchange, diversified multi-start, and radial balanced growth supply
  only a feasible warm start and the best-single-item dominance floor; they are not a globality
  certificate. Exact 2-D exhaustive-grid fixtures independently test YES-best, NO-best, mirror
  invariance, and the former coordinate-ascent counterexample.
- **Discrete repair must PROVE it did not harm — carry a `RepairCertificate`.** κ scales the
  continuous solution first; quantized/capped stakes (each on their OWN per-item tick/min
  grid) are RE-EVALUATED under the worst-price model; a non-empty plan is submit-worthy only
  if its repaired ΔU is still `> 0` AND every safe-prefix bound is `> 0` (`chosen_source` +
  continuous-from-chosen-parent stamped), proven by a `RepairCertificate` (enforced in
  `SolutionPlan.__post_init__`), else no-trade. Phase-1 leaves the executable price to the submit path.
- **Phase-1 evidence grades the projection, not the plan.** The shim emits a
  `LegacyDecisionProjection` re-scoring the primary leg STANDALONE at its post-haircut size;
  if that ΔU ≤ 0 → no-trade in phase 1. Promotion evidence NEVER grades
  `SolutionPlan.expected_delta_log_wealth`.
- **Log-domain safety.** A non-positive endowment atom is refused up front
  (`ZeroWealthOutcomeError`); coordinate feasibility bounds keep `W_end(a) > 0` strictly.
- **Coherence lockstep (§4 decision 1).** The shim emits `coherence_allows=True`; the
  overlay's COHERENCE_BLOCKED guard retires in the flag-ON packet. Do not add a coherence
  veto here.
- **Correlation rail is "caps" until C4.** Cross-family risk stays with the risk_allocator
  correlation caps; every single-family plan stamps `correlation_rail="caps"`. Any future
  degraded multi-family mode stamps `caps_degraded_not_optimal` with promotion evidence BLOCKED.

## Common mistakes

- Reading `JointQBand.q_lcb`/`q_ucb` as the edge basis instead of `samples` → diverges from
  payoff_vector's own robust ΔU (double-counts the tail).
- Synthesizing conversion routes in the menu adapter → `conversion_routes` is `()` by design
  until the builder + NEGRISK_SPLIT dry-run gate land (packet §5).
- Skipping the discrete re-evaluation "because κ=1 and rounding is small" → the min_order_size
  floor can flip a thin edge negative; only the re-evaluation catches it.
- Threading `max_stake_usd` into the LEGACY engine → breaks G3 OFF-path byte-identity
  (packet decision 5). The solver is budget-aware via `WealthByOutcome.cash_usd` instead.
