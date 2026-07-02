# Order-Engine v2 → Implementation Architecture (2026-07-02)

**Basis:** design spec `docs/rebuild/order_engine_first_principles_design_2026-07-02.md` (v2,
consult-hardened). Mapping produced by 6-surface read-only investigation (workflow
`wf_86aca968-ad0`: probability authority, events, venue, capital, decision core, governance) —
every claim below carries file:line in the workflow journal; this doc keeps only the load-bearing ones.

## 0. Executive verdict

The design is **~50% standing infrastructure, not greenfield**. Zeus already runs: an append-only
event substrate with idempotency (`opportunity_events`, `src/events/`), two live push websockets
(market book + user fills) already bridging book moves into synchronous re-solves for held families,
the Day0 absorbing boundary **already inside served q as the exact conditional CDF** (not a mask;
`src/forecast/day0_conditioner.py` via the materializer), a content-addressed q-version proxy
(`posterior_identity_hash` + `dependency_source_run_ids_json`), a depth-walk executable-cost module
matching the spec verbatim, two independent FC-03 re-fetch layers, complete venue metadata
(tick/min/negRisk/fee) on every snapshot, a near-complete order/command state grammar (INV-29), a
route-menu enumerator (`negrisk_routes.py`) whose menu almost equals §3.3's, and a live
reserve/release collateral ledger wired at a single choke point.

The true BUILDs are five: **(1) the CAS reservation ledger** (the current one has a live TOCTOU
race TODAY — the daemon is already multi-threaded, so this is a bugfix before it is a redesign
prerequisite), **(2) the joint convex SOLVE** (the current "engine" is filter-then-rank pick-top-1;
the one reusable primitive is `payoff_vector.optimize_vector_stake`'s log-utility ΔU shape),
**(3) the C4 joint scenario/correlation service** (nothing cross-family exists; a Ledoit-Wolf
correlation envelope exists but strategy-owned, cap-framed, wrong seam), **(4) CTF convert/split/merge
venue primitives** (adapter has only submit/cancel/redeem; `negrisk_routes` already enumerates the
conversion routes with `executable=False` waiting for them), **(5) the input→q_version latency
metric + SLA** (zero measurement exists; current detection floor is the 60–90s scan).

Two components are **K0 schema packets** (constitution: principal_architect + integration_reviewer):
the order-execution state-machine extension and the reservation ledger. Both are new canonical truth.

## 1. Component map (design → code)

| design element | verdict | seam / evidence |
|---|---|---|
| q_version + input HWMs (A1) | **ADAPT** | `forecast_posteriors.posterior_identity_hash`/`dependency_hash`/`dependency_source_run_ids_json` already content-addressed, consumed at `event_reactor_adapter.py:11336-11492`; BUILD the read-time diff "incorporated HWM ≥ known raw-input HWM" (only checked at materialization-refusal today) |
| input→q latency SLA (A2, "THE metric") | **BUILD** | chain exists (`source_clock_live_replacement_cycle` → `replacement_forecast_production:380` → `cycle_advance_trigger` → materializer) but zero wall-clock measurement anywhere |
| Day0 boundary inside q | **REUSE** | already exact `Y=max(obs,X_rem)` CDF in point q AND q_lcb/q_ucb (`materializer` + `day0_conditioner.py`); two cruder decision-time re-masks (`evaluator.py:2458`, reactor `:21191`) = later consolidation |
| freshness fail-closed | **REUSE** | readiness BLOCKED/TTL + monotone-cycle guard + live-eligibility flags |
| C4 joint scenarios / correlation | **BUILD** (samples) + **ADAPT** (envelope) | zero cross-family samplers; `strategy/correlation.py` + `regime_correlation_store.py` (Ledoit-Wolf) exist but are exposure-CAP framed, strategy-owned, coupled to regime taxonomy slated for deletion — rehome under probability authority |
| event loop (§3.2) | **40-50% REUSE** | `opportunity_events` + EventWriter + idempotency; market-channel WS pushes BOOK/BBA and already triggers `EDLI_REDECISION_PENDING` for held/screened families (`price_channel_ingest.py:2148-2220`); user-channel WS fills live. BUILD: `SOURCE_RUN_ARRIVED` event type (probe is poll+cursor, `source_clock_update_probe.py`), universalize the book bridge beyond held families |
| input calendar (C2 shadow values, REST_ELIGIBLE) | **REUSE** | `release_calendar.py` + `dissemination_schedules.py` are exactly the deterministic schedule |
| order state machine (§3.1) | **ADAPT (K0)** | AMENDED 2026-07-02 (evidence: W1.2 schema packet): `venue_commands` gains a `q_version` stamp at creation (decision truth; snapshot precedent `executor.py:1216-1226`); `STALE_PENDING_CANCEL` and `delayed` are **DERIVED predicates, not stored states** — staleness ≡ order open ∧ stamped q_version ≠ current family q_version; delayed ≡ in-flight dwell > measured submit p99. `venue_order_facts` CHECK untouched (venue truth ≠ decision plane); reserved-cash link already exists (`collateral_reservations` PK = command_id); CommandState NOT expanded (INV-29 amendment); do NOT fold into position LifecyclePhase |
| reservation ledger (§3.1, A4) | **ADAPT + BUILD (K0)** | `collateral_ledger.py` reserve-on-submit/release-on-terminal wired at one choke point (`venue_command_repo.py:1179`) BUT: check-then-insert TOCTOU (no aggregate atomicity) with real thread concurrency live today (reactor pool + 20-worker pool); FILLED released not converted (~180s phantom-cash window); partial fills untracked; no unsettled-proceeds bucket. BUILD CAS + convert-on-fill + identity check |
| A4 identity → RED | **ADAPT** | `exchange_reconcile.py` (findings table, record/resolve API) reconciles orders/positions but never dollars, and findings don't feed `risk_level.py` — add a collateral-identity finding kind routed into the EXISTING RiskGuard RED (constitution forbids a parallel kill-switch) |
| SOLVE (§3.3) | **BUILD** | `family_decision_engine.decide()/_select()` is filter-then-rank top-1 — not a solver. Reusable: `payoff_vector.optimize_vector_stake` (log-utility ΔU vs existing exposure — right shape, 1-D); `negrisk_routes.build_negrisk_route_set` (direct/synthetic/pair/full-basket routes, size-aware on executable ladders) = the §3.3 menu, REUSE-grade. Single instantiation seam: `qkernel_spine_bridge.py:1332` → clean module swap |
| exits = same solve (C5) | **ADAPT** | `ExitContext`/`ExitDecision` plumbing + fail-closed scaffolding reusable; replace rule body (win-rate floor + forward_edge) with marginal condition `b·Σq_j/W_j > q_i/W_i` (needs wealth-by-outcome state); `monitor_refresh.py` (4,043 lines) = the separate lane to dissolve; `exit_lifecycle.py` order mechanics REUSE |
| executable cost / FC-03 / venue metadata | **REUSE** | verbatim matches; FC-03 has two independent fail-closed layers |
| batch submit + safe prefixes | **BUILD (thin)** | SDK `post_orders`/`cancel_orders` exist (`py_clob_client_v2/client.py:840,876`), zero call sites — wrapper + ≤15 chunking + safe-prefix decomposition |
| CTF convert/split/merge | **BUILD (hard)** | adapter wires only submit/cancel/redeem; `negrisk_routes.py` header contains the earlier corroborating audit; needs new adapter methods + `venue_commands` intent kinds + transition rows |
| self-trade guard | **BUILD** | nothing exists |
| rate-limit budget + cancel-priority | **BUILD** | only reactive 429 handling exists |
| REST_ELIGIBLE (§3.4) | **BUILD** | deterministic, from release_calendar + measured cancel/submit p99. NOTE: `maker_fill_calibration.py` (learned Beta-Binomial fill model, 9 reactor call sites) is the anti-pattern §3.4 forbids → DELETE, in lockstep |
| ARM / κ | **REUSE** | route the new controller's live-submit boundary through existing `require_operator_arm()`/`OperatorArm` token (`main.py:824-855`); do not build a second gate |

## 2. Deletion list — blast radius (uneven; order matters)

| target | radius | note |
|---|---|---|
| regret ledger as decision input | tiny (2-5 hits/file) | cleanest first deletion |
| selection_curse_bound + calibrator + loaders | contained (1 main call site in family_decision_engine) | dies with the engine swap |
| market_anchor | mostly done | already superseded by `market_coherence.py` — which is itself a model-vs-market veto lane (**operator decision required**: under the axioms it goes; it is 3 call sites in `decide()`) |
| direction_law | smaller than grep suggests | already a stub; surviving hits are the native-side route-legality check (an A3 menu-completeness test — KEEP) |
| taker quality floors (`LIVE_DIRECTION_WIN_RATE_FLOOR`) | entangled with C5 | wired into `Position.evaluate_exit` — same change as the exit-rule rewrite, not separable |
| Kelly haircut stack → κ | largest file-touch count (evaluator 25, reactor 38 raw hits) | mechanical, low logical risk |
| coverage/licensing lanes (`settlement_backward_coverage`, live_admission gates) | **9+ reactor sites + TRAP** | `arm_gate_coverage_blocks` feeds ArmGateVerification and settings says the ARM gate reads the coverage verdict UNCONDITIONALLY — **rewire ARM first or the deletion silently disarms it** |
| maker_rest_escalation | 17 main.py hits + **TRAP** | it is the ONLY GTC TTL owner today — delete only in the same packet that lands C3 staleness + REST_ELIGIBLE |
| monitor_refresh.py | 4,043 lines | architectural deletion (no separate cadence lane); its belief-re-read/day0 threading content partially survives inside the solve |
| strategy lanes / edge_source | split verdict | delete dispatch-on-strategy-key (`cycle_runtime.py:63,98`); KEEP `edge_source` as receipt provenance field (11 db.py + 12 attribution writers) |
| reactor scan as primary trigger | highest structural risk | this is main.py's whole APScheduler model, not a reactor-local change; scan survives as liveness backstop |
| tests | retire WITH components, same commit | enumerated: tests/decision/test_selection_*{4}, test_market_anchor*{2}, test_direction_law*{2}, test_maker_rest_escalation*, kelly-haircut tests; remove their `test_topology.yaml` rows |

`risk_allocator/governor.py` (correlation-key hard caps) is NOT on the deletion list — it survives
as the outer safety rail alongside the new solve.

## 3. Migration DAG (per the P2_sequence 2026-06-14 precedent: G0 tests → G3 byte-identity-OFF →
evidence gate → ARM flip → flag deleted; self-arming for read-only/additive nodes)

**Governance preconditions (every wave):** planning-lock evidence before src/state/** or cross-zone
packets; K0 schema_packets for Wave-1 objects; new modules registered in `module_manifest.yaml` +
`source_rationale.yaml` + scoped AGENTS.md **in the same packet** (src/decision/ sat unregistered —
do not repeat); `db_table_ownership.yaml` + schema-fingerprint refresh for any new table
(machine-checked, fails closed); no permanent flags.

- **W0 — self-arming instrumentation (additive, read-only):**
  q_version formalization (alias posterior_identity_hash; read-time incorporated-HWM ≥ raw-HWM
  tripwire, A1) · input→q_version latency metric + SLA plumbing (A2) · `SOURCE_RUN_ARRIVED`
  event type emitted by the source-clock probe · registry backfill.
- **W1 — the two K0 truth objects (also a live bugfix):**
  CAS reservation ledger (aggregate-atomic reserve; convert-on-fill; partial-fill accounting;
  unsettled-proceeds bucket; A4 identity finding → existing RiskGuard RED). The TOCTOU race is live
  today under real thread concurrency — this packet is justified independently of the redesign. ·
  Order-state extension (`STALE_PENDING_CANCEL`, `delayed`, q_version/snapshot/reserved-cash stamps)
  extending `venue_order_facts` CHECK + `CommandState` + truth reducer vocabulary.
- **W2 — venue capabilities (inert until consumed):**
  batch submit/cancel wrapper + ≤15 chunking + safe prefixes · self-trade guard · rate-limit budget
  + cancel-priority · CTF convert/split/merge adapter methods + intent kinds (flips
  `negrisk_routes` conversion legs to executable).
- **W3 — the SOLVE (new module, single seam):**
  new registered module (e.g. `src/solve/`) implementing the §3.3 program: menu from
  `negrisk_routes`, math generalizing `payoff_vector`'s ΔU to joint multi-asset, holdings as
  endowment (C5 marginal rule with wealth-by-outcome state), κ, discrete repair pass. Swapped in at
  `qkernel_spine_bridge.py:1332` behind a time-boxed promotion flag (G3 byte-identical OFF), promoted
  on its evidence gate, flag deleted. Until the C4 service exists: single-family solve + existing
  `risk_allocator` caps as the correlation rail (declared simplification). · In parallel: C4 joint
  scenario/correlation service rehomed under the probability authority.
- **W4 — event-driven triggers:**
  universalize the book-move bridge (any family with resting capital or q-coverage) · C3 staleness
  path (`SOURCE_RUN_ARRIVED`/obs-tick → `STALE_PENDING_CANCEL` → cancel-set → reconciled re-solve) ·
  REST_ELIGIBLE from release_calendar + measured p99s · demote the 60s reactor scan to liveness
  backstop · **same packet:** delete `maker_rest_escalation` + `maker_fill_calibration`.
- **W5 — deletions (ARM-rewire first):**
  decouple ARM from the coverage verdict → then coverage/licensing lanes · selection machinery ·
  Kelly haircut stack → κ · `monitor_refresh` dissolution into the solve · strategy dispatch ·
  regret-ledger decision inputs · Day0 decision-time mask consolidation (after trust in
  authority-side conditioning) · each with its tests and registry rows, same commit.

## 4. Decisions — RESOLVED 2026-07-02 (operator axiom applied)

**Operator axiom (2026-07-02):** the decision is a continuous flow, not a one-shot event; the
system wins only by being persistently faster than the market's motive; time and efficiency are
first-class decision inputs. This affirms authority X1 (event-driven re-decision) as core law and
resolves all three open decisions:

1. **market_coherence.py → DELETE the veto, INVERT into a re-decision router (dies with W3, not W5).**
   First principles: alpha IS disagreement with price (win-rate vs settled price, not vs our own
   q_lcb) — a divergence veto refuses to trade exactly where measured edge is largest. The failure
   mode it guards (our inputs are broken and the market knows) is already owned by the principled
   defenses: freshness fail-closed, the A2 latency SLA, q_lcb uncertainty shading, and
   settlement-graded evidence. A binary veto is gate-mass, and under continuous flow it oscillates
   (blocked → book drifts → unblocked → re-enter = churn). Execution: the new SOLVE simply does not
   include `coherence_allows` (the 3 `decide()` filter sites die with the engine swap at W3);
   `assess_market_coherence`/`MarketCoherenceReport` survive REUSED as (a) a typed divergence event
   into `opportunity_events` for monitoring/calibration diagnostics, and (b) the **re-decision
   priority key** — divergence × staleness ranks which family the event loop re-solves first. The
   brake becomes the router: largest disagreement gets the fastest look, never a refusal.

2. **W3 interim correlation rail → ACCEPT transitional (single-family solve + risk_allocator caps);
   do NOT block on C4.** First principles with the time axiom: blocking serializes the critical
   path behind the longest-lead BUILD while the top-1 picker keeps leaving EV on the table every
   cycle. The strongest correlation (bins of one market, same city×metric) is INSIDE the family and
   fully handled by the single-family joint solve; residual cross-family exposure is bounded by
   `risk_allocator/governor.py` hard caps (the surviving outer rail) plus κ shading. Bounded
   overexposure, never sign error. Three binding conditions: (a) every decision receipt stamps
   `correlation_rail=caps` so settlement grading can later measure exactly what C4 changes;
   (b) the solver consumes a `ScenarioService` interface from day one — transitional impl is the
   per-family independent product measure, so C4 is a drop-in service swap, not a solver rewrite;
   (c) C4 runs W3-parallel with its own evidence gate — a dated follow-up, not dessert.

3. **exit_portfolio_execution_authority_2026-06-13.md → ERRATUM WRITTEN (2026-07-02, in the doc).**
   Operator word outranks the authority doc: the flag-gated/shadow-computed delivery mechanism is
   superseded by the time-boxed promotion harness (G3 byte-identical-OFF → evidence gate → ARM flip
   → flag deleted). Scope is mechanism only — E/Q/X/K math stands, X1 is affirmed as core law, and
   K4's paired-replay estimation stays licensed (offline analysis ≠ runtime shadow lane).

### 4b. What the axiom changes in the plan (beyond the three decisions)

- **A2 latency metric is confirmed as W0's first deliverable** — speed cannot be managed unmeasured;
  the SLA is the axiom made executable. Current detection floor: 60–90s scan; websocket-triggered
  sync re-solve exists only for held/screened families until W4.
- **The DAG order stands under the axiom — W1 before W4 is not caution, it is causal:** raising
  decision frequency multiplies exposure to the live reservation-ledger TOCTOU race. Event-driven
  triggers without the CAS ledger = faster wrong decisions. Correctness objects first, then cadence.
- **Restart churn is a speed defect, not just noise:** the daemon's ~20-min auto-restart cycle
  breaks websocket continuity — each reconnect is a blind window in the continuous flow. W0 adds a
  blind-window metric (time not covered by a live book subscription) beside the A2 latency metric;
  the restart root-cause gets triaged on that evidence.
