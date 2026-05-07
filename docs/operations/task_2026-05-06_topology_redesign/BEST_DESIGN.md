# Zeus Topology — Integrated Best Design

Status: DESIGN PROPOSAL, NOT CURRENT LAW
Date: 2026-05-06
Companion to: `PLAN.md`, `PLAN_AMENDMENT.md`, `ULTRA_PLAN_FINAL_PREP.md`
Route: `operation planning packet`

This document is the integrated end-state design specification. It synthesizes
the three parallel analyses (architect critique, external research, forensic
baseline) into one coherent system. It is concrete: schemas, formats, file
inventory, enforcement layers, acceptance criteria.

It is built to be durable across 1-month, 6-month, and 1-year horizons without
redesign — by grounding the design in physical/economic invariants of
quantitative trading (which do not change) rather than operational shapes
(which constantly change).

This document does not authorize implementation, schema migration, live
trading unlock, or any topology runtime change.

---

## §1 The Design in One Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                       STABLE LAYER (finite, append-only)             │
│                                                                      │
│  invariants.yaml         capabilities.yaml      reversibility.yaml   │
│  ~44 INV-XX entries      ~15 capability tags    4 classes            │
│  Physical/economic       Bounded by Zeus's      ON_CHAIN /           │
│  truths of the           physical actions       TRUTH_REWRITE /      │
│  market and chain        on canonical state     ARCHIVE / WORKING    │
│                                                                      │
│  These do not change unless market mechanics change.                 │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              │ (decorators in src/ tag functions)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  SOURCE TAGGING (in code)                            │
│                                                                      │
│  @capability("canonical_db_write")                                   │
│  @protects([INV-12, INV-17])                                         │
│  def write_settlement_row(...): ...                                  │
│                                                                      │
│  No central registry. Source is canonical.                           │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              │ (computed on demand from diff)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│              GENERATIVE LAYER (no profile catalog)                   │
│                                                                      │
│  route_card(diff) = {                                                │
│    capabilities_exercised:  diff → @capability decorators            │
│    invariants_in_scope:     capabilities → @protects                 │
│    relationship_tests:      invariants → tests/test_inv_*.py         │
│    reversibility_class:     max(capabilities.reversibility)          │
│    hard_kernel_hits:        capabilities.kernel_class == HARD        │
│    active_leases:           cohort lock service                      │
│  }                                                                   │
│                                                                      │
│  Route card output ≤500 tokens (T0). No profile matching.            │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│            ENFORCEMENT LAYER (where safety is real)                  │
│                                                                      │
│  Edit-time:    Write tool gated by capability set                    │
│  Type-time:    LiveAuthToken phantom type at order boundary          │
│  Commit-time:  diff verifier — capabilities used must match claim    │
│  Pre-merge:    relationship-test runner (only those in scope)        │
│  Pre-merge:    replay-correctness gate (Chronicler events)           │
│  Runtime:      pre-trade kill switch (FIA pattern, non-bypassable)   │
│                                                                      │
│  Hard-kernel violations block. Everything else is advisory.          │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│           TELEMETRY LAYER (M1-M5 from FINAL_PREP)                    │
│                                                                      │
│  Every invocation logs: tokens, verdict, ritual_signal, sunset       │
│  Monthly critic agent runs sunset review                             │
│  INV-HELP-NOT-GATE relationship test in CI                           │
│                                                                      │
│  Drift catches itself before the ratchet engages.                    │
└──────────────────────────────────────────────────────────────────────┘
```

This is the whole system. It is roughly 800 lines of YAML + 500 lines of
Python + ~50 source-code decorators + ~12 hard-kernel paths. Total topology
infrastructure ≤1500 lines. Current: 39,800. Reduction: ~26×.

---

## §2 The Stable Layer — Why It Is Future-Proof

### Why the stable layer cannot grow unboundedly

Topology grows when its primitives are operational shapes (tasks, profiles,
phases, sprints). Operational shapes are infinite — every new sprint adds
profiles, every near-miss adds rules.

The stable layer's primitives are **physical and economic facts of the system
Zeus participates in**:

- **Invariants** are properties of the market mechanism (Polymarket integer
  settlement on WU-displayed values), the contract semantics (point /
  finite_range / open_shoulder bins), the chain (CLOB fills are
  irreversible), and the trading mathematics (Platt calibration is
  monotonic). These do not multiply. They are the laws of the playing field.
- **Capabilities** are bounded by **what physical actions Zeus can take that
  affect canonical state**. Zeus cannot do more than: submit/cancel/redeem
  orders, write/migrate canonical DB, rebuild calibration/settlement,
  publish reports, mutate authority docs/configs, promote archive,
  flip source validity, send on-chain transactions. ~15 items. Every new
  capability requires a new physical action capability — bounded by the
  market and chain interface, not by sprint count.
- **Reversibility classes** are 4. ON_CHAIN (cannot undo, ever),
  TRUTH_REWRITE (can recover from backup but compromises audit), ARCHIVE
  (history rewrite, recoverable from git), WORKING (trivially reversible).
  These are physical properties of the storage medium.

If Polymarket changes settlement mechanics or Zeus expands to new contract
classes, **append** to the stable layer. The structure does not change.

### `architecture/invariants.yaml` shape (preserved + tightened)

Already exists at ~8,400 tokens. Tighten with:

```yaml
- id: INV-12
  name: settlement_value_via_settlement_semantics
  description: |
    Every persisted settlement_value must pass
    SettlementSemantics.assert_settlement_value() with the binding
    bin_type. WU-displayed integers are the only canonical values.
  protects_capabilities: [canonical_db_write, settlement_rebuild]
  relationship_tests:
    - tests/test_settlement_semantics.py
    - tests/test_instrument_invariants.py
  category: contract_mechanics
  created: 2026-04-12
  sunset: 2027-04-12              # M3
  last_revalidated: 2026-05-01
  authority_basis: docs/authority/settlement_semantics_law.md
```

### `architecture/capabilities.yaml` shape (new file, the hinge of the design)

```yaml
- id: live_venue_order
  description: Submit, cancel, or redeem an order on Polymarket CLOB.
  reversibility: ON_CHAIN
  kernel_class: HARD                # blocks unless explicitly authorized
  
  protects_invariants: [INV-08, INV-15, INV-22]
  guarded_writers:
    - src/execution/executor.py::submit_order
    - src/execution/executor.py::cancel_order
    - src/execution/harvester.py::redeem
  required_relationship_tests:
    - tests/test_live_executor_phantom_token.py
    - tests/test_pretrade_killswitch.py
  
  type_discipline:
    enforcement: phantom_token
    token_class: LiveAuthToken
    acquisition: scripts/live_unlock.py    # operator-signed only
  
  lease_required: true              # multi-agent contention
  
  original_intent:                  # M4
    designed_for: [actual order submission, cancel, redeem]
    not_designed_for: [paper trading, replay, backtest, shadow]
    intent_test: |
      task references a live CLOB action AND ZEUS_MODE is live
      AND no shadow/paper/backtest token in description
    drift_signal: invocation rate >5% on tasks that don't match
  
  created: 2026-05-06
  sunset: 2026-08-06                # M3, 90-day default
  
  # Required telemetry fields per M1
  telemetry: capability_log/live_venue_order.jsonl
```

15 such entries. Each is the unit of decision-making.

### `architecture/reversibility.yaml` shape (new file, ≤30 lines)

```yaml
classes:
  - class: ON_CHAIN
    description: Settlement on chain, fills, redeems. Cannot be undone.
    escalation: operator_signed_dry_run + kill_switch_armed
    block_default: true
    
  - class: TRUTH_REWRITE
    description: Mutates canonical DB rows representing live truth.
    escalation: operator_signed + tested_rollback_path
    block_default: true
    
  - class: ARCHIVE
    description: Rewrites historical evidence in docs/archives/.
    escalation: operator_signed
    block_default: false
    
  - class: WORKING
    description: Working-tree edits, easily reversed via git.
    escalation: none
    block_default: false
```

That's the whole stable layer. ~3,000 lines of YAML total when fully populated.
Bootstrap cost ≤30,000 tokens. Compare to current 220,000.

---

## §3 The Generative Layer — No Profile Catalog

### The function

```python
# scripts/topology_doctor/route.py — replaces ~12,000 lines of current scripts

def route_card(diff: Diff, task_description: str) -> RouteCard:
    files_touched = diff.files()
    
    # 1. Files → capabilities (via decorators in source)
    capabilities = set()
    for f, hunk in diff.hunks_by_file().items():
        for fn in hunks_to_functions(hunk):
            capabilities.update(decorators_of(fn, "@capability"))
    
    # 2. Capabilities → invariants
    invariants = set()
    for cap in capabilities:
        invariants.update(CAPABILITIES[cap].protects_invariants)
    
    # 3. Invariants → relationship tests
    relationship_tests = []
    for inv in invariants:
        relationship_tests.extend(INVARIANTS[inv].relationship_tests)
    
    # 4. Reversibility class
    reversibility = max(
        REVERSIBILITY[CAPABILITIES[c].reversibility] for c in capabilities
    )
    
    # 5. Hard-kernel hits
    hard_kernel = [c for c in capabilities if CAPABILITIES[c].kernel_class == "HARD"]
    
    # 6. Active leases (multi-agent)
    leases = lease_service.active_in_capability_set(capabilities)
    
    # 7. Original intent check (M4) for each capability
    intent_violations = [
        c for c in capabilities
        if not CAPABILITIES[c].intent_test_matches(task_description)
    ]
    
    return RouteCard(
        capabilities=capabilities,
        invariants=invariants,
        relationship_tests=relationship_tests,
        reversibility=reversibility,
        hard_kernel_hits=hard_kernel,
        active_leases=leases,
        intent_violations=intent_violations,  # advisory only
    )
```

That is the entire route function. ≤200 lines of Python including helpers.
There is **no profile catalog**. There is no profile authoring. There is no
profile maintenance.

### Route card output format (≤500 tokens for T0)

```
ZEUS ROUTE CARD                                               2026-05-06T14:21
Task: "Adjust calibration alpha for low-temp NYC family"
Diff: 3 files, 47 lines

CAPABILITIES EXERCISED
  - calibration_rebuild   [TRUTH_REWRITE]   guards: INV-04, INV-19
  - canonical_db_write    [TRUTH_REWRITE]   guards: INV-12, INV-17

INVARIANTS IN SCOPE
  - INV-04  calibration family separation (high/low)
  - INV-12  settlement_value via SettlementSemantics
  - INV-17  DB before derived JSON
  - INV-19  Platt monotonicity

RELATIONSHIP TESTS REQUIRED
  - tests/test_calibration_family_separation.py
  - tests/test_settlement_semantics.py
  - tests/test_db_before_json.py
  - tests/test_platt_monotonic.py

HARD-KERNEL HITS: none
REVERSIBILITY: TRUTH_REWRITE  →  requires operator-signed rollback path
ACTIVE LEASES: none
INTENT-MATCH: clean (all capabilities match task profile)

Mode: capability-gated edit + diff verifier + relationship-test runner
```

About 180 tokens. Within budget.

If hard-kernel hits are present, that is the only blocking output:

```
ZEUS ROUTE CARD                                               2026-05-06T14:21
Task: "Manual fix to settlement bin for May 5 NYC"
Diff: 1 file, 4 lines

⚠ HARD-KERNEL HIT
  - settlement_rebuild [ON_CHAIN]
    Direct mutation of canonical settlement value. Blocked.
    Requires: operator-signed rebuild packet + dry-run evidence + rollback.
    See: docs/authority/settlement_semantics_law.md
```

About 70 tokens. Block message is unambiguous.

---

## §4 The Enforcement Layer — Where Safety Becomes Real

### Five enforcement points, each with a specific failure mode it prevents

| Layer | Mechanism | Failure mode prevented | External pattern |
|---|---|---|---|
| Edit-time | Write tool refuses on hard-kernel paths unless capability is in active set | Agent with stale boot profile types into a live writer | Anthropic capability-autonomy inverse |
| Type-time | `LiveAuthToken` phantom type required at `executor.submit_order` boundary | Paper-mode code path accidentally submits live | Jane Street phantom types; QuantConnect class isolation |
| Commit-time | Diff verifier asserts capabilities used == capabilities claimed in route card | Agent claimed read-only, diff touched canonical writer | AgentSpec runtime DSL |
| Pre-merge | Relationship-test runner runs only the tests in `route_card.relationship_tests` | Calibration rebuild ships without the family-separation antibody | Standard relationship-testing discipline |
| Pre-merge | Replay-correctness gate replays last 7d Chronicler events through evaluator | Calibration change silently breaks last week's projections | Event-sourced replay (Two Sigma / MiFID II practice) |
| Runtime | Pre-trade kill switch in execution path; cancels all on trip | Runaway algo, fat-finger | FIA 2024 / Optiver three-pillar |

Of the six, **only the kernel hits at edit-time and runtime kill switch are
blocking by default**. Everything else fails CI but does not gate human-in-
the-loop work mid-stream.

### What is removed

- The 61 hand-authored profiles in `digest_profiles.py` — deleted.
- The 35 fossil milestone profiles — deleted (subset of above).
- The 39% prose-as-block forbidden_files — deleted (replaced by capability tagging at source).
- The `ZEUS_MODE` env-flag-as-safety — replaced by phantom token + class isolation.
- The "ambiguous → hard stop" admission — replaced by capability decoration ("if no @capability decorators are touched, route card is empty advisory; agent proceeds").
- ~12,000 lines of `topology_doctor*.py` policy/admission scripts — collapsed to ~500 lines of `route.py`.

### What is preserved

- `architecture/invariants.yaml` (refactored shape, same content scope).
- `architecture/source_rationale.yaml` (becomes input to capability tagging spike).
- The relationship tests under `tests/test_*invariant*.py`.
- The Chronicler event log (powers replay gate).
- Map maintenance / py_compile / git diff --check (housekeeping).

---

## §5 Integration Table — Every Three-Agent Finding to a Design Element

### Architect findings → Design element

| Architect finding | Where it lives in this design |
|---|---|
| Capability primitive, not file paths | §2 capabilities.yaml + §3 generative layer |
| Files = projection | §3 step 1 (diff → @capability decorators in source) |
| Diff-based post-hoc verification > intent-based ex-ante | §4 commit-time diff verifier |
| Generative routing, not catalog | §3 (no profile catalog) |
| Hybrid router + verifier + tool-gate | §4 five enforcement points |
| Multi-agent lease model | §2 capabilities `lease_required` + §3 step 6 active_leases |
| Relationship tests as first-class | §2 invariants `relationship_tests` + §4 pre-merge runner |
| Shadow-mode validation | Phase 0.F (PLAN_AMENDMENT.md) |
| Token budget enforced as test | §3 ≤500 tokens for T0 + Phase A test |
| Delete digest_profiles.py | §4 "what is removed" |
| Reversibility classification | §2 reversibility.yaml + §3 step 4 |
| §3.2 prose laws → typed wires | §2 invariants `protects_capabilities` |

### Researcher findings → Design element

| Researcher finding | Where it lives in this design |
|---|---|
| Phantom types (Jane Street) | §2 capability `type_discipline.phantom_token` + §4 type-time |
| Pre-trade risk + kill switch (FIA / Optiver) | §4 runtime kill switch |
| Event-sourced + replay-as-test (Two Sigma / MiFID) | §4 pre-merge replay-correctness gate |
| Paper/live as type discipline (QuantConnect) | §2 LiveAuthToken phantom + §4 §4 type-time isolation |
| On-chain reconciliation as truth anchor | reversibility class ON_CHAIN + §6 chain-divergence void |
| Capability-autonomy inverse (Anthropic) | §4 edit-time gate (autonomy↑ → scope↓) |
| AgentSpec trigger/predicate/action DSL | §3 generative function is the DSL evaluator |
| Position-concentration safety widening (Citadel) | §2 capabilities can carry size_threshold per kernel_class |
| Settlement-window freeze | §4 runtime gate (lifted from evaluator) |

### Scientist findings → Design element

| Scientist finding | Where it lives in this design |
|---|---|
| 220k token bootstrap → must collapse | §2 stable layer ≤30k tokens total |
| 61 profiles → 35 fossil | §4 "what is removed" — all profiles deleted |
| 39% prose-as-block forbidden_files | replaced by source decorator capability tags |
| 50 [skip-invariant]/60d | sunset clock + telemetry catches stale rules; M5 INV-HELP-NOT-GATE forbids advisory-as-block |
| 11 relationship-test files for 44 invariants | each invariant `relationship_tests` field is required; gap becomes a CI failure |
| 13% infra-to-impl ratio (1:8) | target ≤2% (1:50) |

Every finding from every agent has a home. None are dropped.

---

## §6 Quant-Trading-System承接 — What This Design Specifically Carries Forward

A quant trading system is judged by its handling of a small set of categories
of failure. For each, this design has a structural answer:

### 1. Wrong order to wrong market at wrong time
- Type-time: `LiveAuthToken` required at submit boundary.
- Edit-time: Write tool blocked on `live_venue_order` paths without capability.
- Runtime: pre-trade kill switch reads risk level (RED → cancel all).
- Settlement-window freeze: `live_venue_order` capability includes `time_window_gate: settlement_window_freeze` field; gate refuses entry within K hours of settlement.

### 2. Wrong data feeding probability chain
- Source validity: `source_validity_flip` capability is HARD kernel.
- Calibration: `calibration_rebuild` capability protects INV-04 (family separation).
- Replay gate: last 7d Chronicler events must replay deterministically; calibration change breaking past projections fails CI.

### 3. Contract semantic violation (settlement value, bin type)
- INV-12 (`settlement_value_via_settlement_semantics`) protected by `canonical_db_write` and `settlement_rebuild`.
- Relationship tests required on every diff that touches those capabilities.
- `SettlementSemantics.assert_settlement_value()` is the runtime gate; the topology layer ensures every writer goes through it.

### 4. State / DB corruption
- `canonical_db_write` is HARD kernel for live truth.
- Reversibility = TRUTH_REWRITE → operator-signed rollback path required.
- Chronicler event log is append-only; canonical state is a projection; replay verifies determinism.

### 5. Calibration / math regression
- INV-19 (Platt monotonicity), INV-04 (family separation), and others.
- Replay-correctness gate is the structural defense.
- Relationship tests run automatically per route card.

### 6. On-chain irreversibility / ghost positions
- Reversibility class ON_CHAIN escalation: operator-signed dry-run + kill switch armed.
- Chain reconciliation: void-on-divergence — local positions diverging from chain for >N cycles are auto-voided, not retained.
- `on_chain_mutation` capability is HARD kernel.

### 7. Risk-level / fail-closed bypass
- INV-21 (`RED_fail_closed_no_advisory_only_risk`) protected by every executor capability.
- Runtime kill switch reads risk level; RED forces cancel-all.
- Position-concentration: per-capability `size_threshold_widens` — at 33%/66% of bankroll-in-family, capabilities require additional escalation.

### 8. Authority confusion / archive-as-truth
- `authority_doc_rewrite` and `archive_promotion` are separate HARD-kernel capabilities.
- Reversibility ARCHIVE for archive promotion → operator-signed.
- Generated artifacts (the deleted `digest_profiles.py` and any replacement) carry equivalence proofs or do not exist.

### 9. Multi-agent contention on shared state
- `lease_required: true` on capabilities like `calibration_rebuild`.
- Lease service in route card step 6 — overlapping leases produce a route-card warning + harness-level coordination.

### 10. Drift of the safety system itself
- M1-M5 anti-drift mechanisms (FINAL_PREP).
- INV-HELP-NOT-GATE forbids any topology output from blocking unrelated work.
- 90-day sunset on every capability and invariant; auto-demote without re-justification.

These ten categories cover what a leading quant trading system must defend.
Every one has a named structural answer in this design. No category is "we'll
trust the docs."

---

## §7 Why This Design Will Not Need Redoing in 1 Month or 1 Year

A design needs redoing when its primitives stop matching the world. This
design's primitives are:

1. **Invariants** — properties of the market mechanism, contract semantics,
   chain irreversibility, and trading mathematics. These do not change in 1
   month or 1 year unless Polymarket changes settlement mechanics, in which
   case **append** to invariants.yaml. The design is unchanged.

2. **Capabilities** — bounded by the physical actions Zeus can take that
   affect canonical state. New venue, new contract type, new settlement path
   → **append** new capabilities. The design is unchanged.

3. **Reversibility classes** — physical properties of the storage medium (on-
   chain, DB, archive, working tree). These do not change unless storage
   architecture changes. **Append** if a new class arises (very unlikely).

4. **Source decorators** — `@capability` and `@protects` annotate functions
   in `src/`. Files change constantly; decorators move with them. There is
   no central registry to maintain. The (file → capability) mapping is a
   query, not a state.

5. **Generative route function** — ~200 lines of Python with no catalog. New
   tasks do not require changes; new capabilities are picked up by name.

What would force redesign:

- Zeus stops being event-sourced (Chronicler retired) → replay gate breaks. **Probability in 1 year: very low.** Chronicler is load-bearing for audit.
- Zeus stops being statically typeable (e.g., dynamic agent harness) → phantom tokens break. **Probability in 1 year: low.** Even with agent harnesses, Python type checkers run.
- New agent paradigm makes ex-ante intent reliable → verifier becomes overkill. **This is a relaxation, not a redesign.** Drop verifier; keep edit-time gate.
- Polymarket disappears → Zeus pivots; both invariants and capabilities are re-derived. **This is the only true redesign trigger.** It is a market-structure event, not a topology problem.

In summary: **no foreseeable change in 1 month or 1 year forces redesign.** All accommodatable changes are appends to the stable layer.

The current topology system needs redoing because its primitive (profile per
task shape) does not match the world (tasks are infinite). This design's
primitive matches reality (invariants are physical, capabilities are
finite). The reality cannot grow operational profiles into the design.

---

## §8 Acceptance Criteria — How We Know the Design Is Done

End of Phase 0 (15 days; per FINAL_PREP §7):

| Criterion | Target | Measure |
|---|---|---|
| Stable layer total tokens | ≤30,000 | sum of invariants.yaml + capabilities.yaml + reversibility.yaml |
| Route card output (T0) | ≤500 tokens | test fixture run |
| Route card output (T2) | ≤2,000 tokens | test fixture run |
| Route function code | ≤500 lines Python | `wc -l scripts/topology_doctor/route.py` |
| Profile catalog entries | 0 | `digest_profiles.py` deleted |
| Capability tags on guarded writers | 100% | source-decorator audit |
| Hard-kernel paths blocked at Write tool | 100% | synthetic edit attempt fails |
| `LiveAuthToken` enforced at submit boundary | yes | type-check passes only with token |
| Replay-correctness gate live in CI | yes | seed regression caught |
| 20-hour autonomous replay friction | ≤2h (from ~10h) | offline replay measurement |
| `[skip-invariant]` rate over 30d post-cutover | <1/week | git log audit |
| INV-HELP-NOT-GATE relationship test passes | yes | CI green |
| All 5 anti-drift mechanisms wired | yes | per-mechanism test |
| Operator signs ADR-1 through ADR-6 | yes | signed file in `adr/` |

Operator review at Phase 0.H against this table is GO/NO-GO. No partial GO.

---

## §9 What Operator Must Decide Before Phase 0 Starts

Beyond the 5 decisions in FINAL_PREP §9:

6. **Accept invariants + capabilities as the dual primitive** (versus alternatives: file-path, object-cohort, relationship-only)? Recommended: accept. Rationale: §7 future-proofness depends on this.

7. **Accept structural deletion of profile catalog** (`digest_profiles.py` and the 61 profiles)? Recommended: accept after Phase 0.D fossil retirement and Phase 0.E spike both succeed. Rationale: §4 "what is removed" is core to token-cost reduction.

8. **Accept type-discipline migration for executor** (LiveExecutor / ShadowExecutor as separate ABCs; LiveAuthToken phantom type)? This is the largest API surface change. Recommended: accept; demonstrate on `live_venue_order` capability spike (Phase 0.E) before committing.

9. **Confirm Chronicler event log is sufficient for replay-correctness gate**, or define the additional event types needed. Replay gate is one of the highest-leverage protections; gating on its feasibility is appropriate.

10. **Approve the 26× topology-infrastructure reduction target** (39,800 → ≤1,500 lines)? This is the headline number; it commits the project to scope.

---

## §10 The Single Sentence

This design is **not a refinement of topology; it is a replacement of its
primitive**. Profiles describe operational shapes (infinite, volatile);
invariants and capabilities describe physical truths (finite, stable). When
the primitive matches reality, the system stops needing redesign — and stops
needing to defend itself with 禁书.

---

## §11 References

- `PLAN.md` — original problem statement (kept).
- `PLAN_AMENDMENT.md` — structural critique + Phase 0 prep.
- `ULTRA_PLAN_FINAL_PREP.md` — anti-drift mechanisms M1–M5 (this design wires them in §2 capability fields and §4 enforcement).
- `architecture/invariants.yaml` — current 44 invariants; refactored shape per §2.
- `architecture/source_rationale.yaml:19-73` — `write_routes` already encodes capability ownership; lift to canonical capability tags.
- Architect output (in-session 2026-05-06) — 12 findings + 3 deepest questions.
- Researcher output (in-session 2026-05-06) — 7 patterns + 5 prescriptions.
- Scientist output (in-session 2026-05-06) — baseline metrics table.
- Jane Street, *Static Access Control via Phantom Types* — §2 type_discipline.
- FIA, *Automated Trading Risk Controls 2024* — §4 runtime kill switch.
- AgentSpec (arxiv:2503.18666) — §3 generative DSL.
- Anthropic, *Building Effective Agents* — §4 edit-time minimal-footprint.
- QuantConnect *Paper Trading docs* — §2 LiveAuthToken precedent.

---

## §12 Non-Goals

This document does not authorize live trading unlock, lock removal, production
DB writes, schema migration, settlement rebuild, redemption, report
publication, archive rewrite, replacement of any current authority surface,
or implementation edits.
