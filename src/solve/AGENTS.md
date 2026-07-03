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
promotion flag. Nothing wires it in yet; the math core is inert until sub-slice 3.

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

- **One-belief law.** The solver NEVER rebuilds q or the band. The bridge serves them
  verbatim (`_served_joint_belief_from_proofs`), the ScenarioService integrates over the
  served samples, and the objective reads band SAMPLES (never the precomputed per-bin
  q_lcb — W3.MATH brief risk #3).
- **Objective is joint, not greedy.** `solve()` maximizes the robust (band-α-quantile)
  expected Δlog-wealth of the WHOLE stake vector against `WealthByOutcome`, not a per-item
  sum. The optimizer is deterministic (no RNG, no wall-clock): cyclic coordinate ascent
  with payoff_vector's coarse-to-fine 1-D grid per coordinate, seeded at the best single
  item so the plan provably dominates the top-1 picker.
- **κ single-owner.** κ=1.0 throughout W3/W4 (the downstream haircut still shades);
  `KappaPolicy.__post_init__` makes a κ<1 with the haircut alive unconstructable. κ
  ownership transfers atomically in the W5 commit that deletes `kelly_multiplier`.
- **Discrete repair must PROVE it did not harm.** κ scales the continuous solution first;
  quantized/capped stakes are RE-EVALUATED under the same robust objective; a plan is
  submit-worthy only if its re-evaluated ΔU is still `> 0`, else no-trade. The
  re-evaluation IS the proof (design §3.3) — never skip it.
- **Log-domain safety.** A non-positive endowment bin is refused up front
  (`ZeroWealthOutcomeError`); coordinate feasibility bounds keep `W_end(j) > 0` strictly,
  so `log` never sees a non-positive wealth.
- **Coherence lockstep (§4 decision 1).** The shim emits `coherence_allows=True`; the
  overlay's COHERENCE_BLOCKED guard retires in the flag-ON packet. Do not add a coherence
  veto here.
- **Correlation rail is "caps" until C4.** Cross-family risk stays with the risk_allocator
  correlation caps; every plan stamps `correlation_rail="caps"` so settlement grading can
  measure what the C4 scenario service later changes.

## Common mistakes

- Reading `JointQBand.q_lcb`/`q_ucb` as the edge basis instead of `samples` → diverges from
  payoff_vector's own robust ΔU (double-counts the tail).
- Synthesizing conversion routes in the menu adapter → `conversion_routes` is `()` by design
  until the builder + NEGRISK_SPLIT dry-run gate land (packet §5).
- Skipping the discrete re-evaluation "because κ=1 and rounding is small" → the min_order_size
  floor can flip a thin edge negative; only the re-evaluation catches it.
- Threading `max_stake_usd` into the LEGACY engine → breaks G3 OFF-path byte-identity
  (packet decision 5). The solver is budget-aware via `WealthByOutcome.cash_usd` instead.
