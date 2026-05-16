# Ultra Plan Prompt — Zeus Topology Final Design + 90-Day Implementation

**Paste this entire document as your prompt to the cloud planning session.**
Self-contained. Cloud session has no access to the local repo; all required
context is inline. No model identifier embedded — let the harness select.

---

## Mission

Produce a single integrated **final design + 90-day implementation plan** for
replacing the Zeus topology / safety routing system. The design must:

1. Eliminate the lived pain — autonomous agents lost ~10 of 20 hours to
   topology re-planning loops in a recent run.
2. Carry forward everything a leading quantitative trading system needs
   (settlement semantics, fail-closed risk, on-chain irreversibility, paper/
   live isolation, calibration regression protection, multi-agent contention).
3. Be durable across 1-month and 1-year horizons without redesign — by
   building on physical/economic primitives rather than operational shapes.
4. Stop "help guidance" from drifting into "禁书" (forbidden literature)
   structurally, not via prose warnings (which have already failed).

The deliverable is **five files** (specified in §10). It is not implementation;
it is the complete blueprint plus the 90-day runway that produces the
implementation safely.

---

## §1 The Lived Pain (anchor of the redesign)

Operator ran a multi-day autonomous worktree on object-meaning invariance
work. In ~20 hours of autonomous agent time, **roughly half was consumed by
topology re-planning loops**: agent declares task → topology denies →
agent re-plans → topology denies again → repeat. The agent did not progress;
it metabolized topology friction.

The redesign succeeds only if **replaying that 20-hour session under the new
system reduces topology-attributable friction to ≤2 hours**. This is the
acceptance test (§9).

A second, related pain: the `zeus-ai-handoff` skill was originally a narrow
helper for "capture details that compaction will lose so the next agent
resumes cleanly." It drifted into a universal ritual invoked on every task.
Critically, the skill's own SKILL.md already contains an inline anti-pattern
warning ("do not use as a universal ritual") at line 71. **It still drifted.**
This is load-bearing evidence that literary anti-drift fails. The structural
anti-drift mechanisms in §6 are non-negotiable.

---

## §2 Current Topology System Brief (forensic baseline)

| Asset | Lines | Notes |
|---|---|---|
| `scripts/topology_doctor*.py` (14 modules) | ~12,290 | entry point + check modules |
| `architecture/topology.yaml` | ~6,891 | coverage roots, zones, roles |
| `architecture/digest_profiles.py` | ~6,001 | auto-generated profile catalog |
| `architecture/invariants.yaml` | (44 INV-XX entries) | the actual safety primitives |
| `architecture/task_boot_profiles.yaml` | (25 leaf profiles) | task-class boot vocabularies |
| `architecture/source_rationale.yaml` | ~1,957 | `write_routes` already encodes capability ownership |
| `architecture/test_topology.yaml` | ~1,276 | test classification |
| **Topology infrastructure total** | **~39,800 lines** | **13% of Zeus Python codebase** (1 routing line per 8 trading lines) |
| `digest_profiles.py` PROFILES | 61 | **35 (57%) are fossil milestone templates** (`r3-*`, `phase-N-*`) |
| `forbidden_files` entries (across all profiles) | 674 | **39% are prose stop-conditions, not file paths** (advisory pretending to be hard) |
| Relationship-test files (`tests/test_*invariant*.py`) | 11 | for 44 declared invariants — antibody coverage thin |
| `[skip-invariant]` commits in 60-day window | ~50 | **~1 bypass/day — gate generates more friction than confidence** |
| `ambiguous` token frequency in topology_doctor*.py | 27 | hard-stop on ambiguity is load-bearing |
| **Bootstrap token cost** (topology + invariants + boot + AGENTS.md + source_rationale) | **~220,000 tokens** | exceeds haiku context entirely; ~44% of sonnet @500k |

Of 674 `forbidden_files` entries:
- 43% (289) are legitimate hard-kernel (live/prod/DB/settlement)
- 18% (122) are housekeeping (archives/worktrees/scratch)
- **39% (263) are broad path paranoia or prose stop-conditions**

---

## §3 Three Parallel Agent Findings (substantive, not citations)

These are summaries of three independent in-session analyses. Use them as
substrate. The cloud session cannot access the originals.

### §3.1 Architect (structural critique, opus tier)

**12 numbered findings:**

1. **Path-based admission is approximate; capability-based is structural.** The current model uses files as the unit. The actual unit Zeus must guard is *capability* (live venue write, canonical DB write, schema migration, settlement rebuild, etc. — ~12-15 items). Files are a derived view of who exercises what capability. Add `@capability("live_venue_write")` decorators on writer functions; topology becomes a query over diff × capabilities.

2. **Intent-based ex-ante routing decays mid-task.** Agent declares "read-only audit," then mid-task reaches for a one-line fix. Structurally honest model: route card is advisory orientation; hard kernel runs on the actual diff before commit. Tool-level capability gating (Write tool literally disabled for non-capability paths) replaces self-attestation.

3. **Profile catalog should not exist.** 61 hand-authored profiles with 35 fossils is the predictable outcome of "one profile per failure shape." Route card should be *generated* from (diff × capability tags × invariant graph), not catalog-matched. `digest_profiles.py` (6,001 lines) deleted; load YAML once, cache.

4. **Relationship tests are the wrong abstraction layer in current PLAN.** Operator's universal methodology says "relationship tests → implementation → function tests, not reversible." Relationship tests are not facets — they are the **central object**. Topology should be a query over (invariant → relationship test → capability tag set → surface cohort).

5. **Shadow-mode validation is absent.** No mechanism to run new and old admission side-by-side on real agent traffic before cutover.

6. **Token budget is unenforced.** Operator's primary complaint is bloat; redesign must lock T0 ≤500, T1 ≤1000, T2 ≤2000, T3 ≤4000 output tokens as a failing test.

7. **`ambiguous` tri-state still relies on agent self-attestation.** "Read-only" is asserted by the agent; real fix is harness-level — Write tool gated by capability tags from topology.

8. **Multi-agent / parallel subagent coordination is not modeled.** When 3 subagents work the same object boundary, no lease/lock model exists.

9. **Live-money laws are prose, not machine-checked relationships.** Each item (SettlementSemantics, dual-track separation, RED fail-closed, chain>event>cache, DB-before-JSON, strategy_key identity) must point to (a) invariant ID, (b) relationship test path, (c) capability tag set.

10. **No reversibility classification.** Anthropic's agent-safety law is "prefer reversible; escalate irreversible." Zeus's irreversibilities are ranked: ON_CHAIN > TRUTH_REWRITE > ARCHIVE > WORKING. Treat them differently.

11. **Settlement-window freeze is in evaluator code, not at topology.** Lift to non-bypassable topology-layer gate.

12. **Replay-correctness as a merge gate is not proposed.** Zeus already has Chronicler (append-only event log). Replay-as-regression is a structural pattern banks use for MiFID/Dodd-Frank. Free protection at no new infrastructure cost.

**3 deepest unasked questions:**

A. **Should topology be a router or a verifier?** Routers answer pre-action; verifiers answer post-action. For LLM agents whose intent rots mid-task, hybrid (capability-gated tools at edit time + diff verifier at commit time + advisory route card at orient time) is structurally sound.

B. **What is the unit of safety — files, capabilities, invariants, or relationships?** Pick one as truth; others are derived. Recommendation: invariant + capability dual primitive; files are projections.

C. **Should agents see profiles at all?** Agent-visible routing creates intent-gaming (agent retries with different wording). If route-card surface is minimal and the rest is compiler-internal, gaming stops.

### §3.2 Researcher (external patterns, sonnet tier)

**7 most important safety patterns from world-class quant systems:**

1. **Phantom-Type Capability Gating (Jane Street).** Phantom types encode access rights as type parameters; mutating wrong type is compile-time error, not runtime crash.

2. **Pre-Trade Risk Layering with Hard Kill Switch (FIA / Optiver / Citadel).** Every order passes pre-trade gate (price corridor, max size, frequency, position limit). Kill switch halts all generation and cancels resting orders on single trigger. CFTC 2024 proposed rules require this as mandatory architectural component.

3. **Event-Sourced Append-Only Truth + Replay-as-Correctness Gate.** All state changes are immutable events; current state is a projection; replay reproduces snapshot deterministically or you have a bug. MiFID II / Dodd-Frank pattern.

4. **Paper/Live Isolation as a Type Boundary.** Not `if PAPER_MODE: skip_submit`. Separate execution paths with different capability grants — `LiveExecutor` and `ShadowExecutor` are different classes that cannot be accidentally swapped. QuantConnect/Lean pattern.

5. **On-Chain / Venue Reconciliation as Canonical Truth Anchor.** CLOB fills are irreversible. Local state is potentially stale until reconciled against chain. World-class pattern: positions diverging from chain for >N cycles are auto-voided, not retained.

6. **Capability-Autonomy Inverse Constraint (Anthropic).** As autonomy increases, capability scope must decrease proportionally. Minimal footprint — acquire only permissions needed for current task. Prefer reversible.

7. **Runtime Enforcement DSL with Trigger/Predicate/Action Rules (AgentSpec arxiv:2503.18666).** Rule language: trigger (event type) + predicate (condition) + enforcement (stop/pause/substitute). Prevents unsafe agent executions in >90% of code-agent cases with ms overhead.

**5 specific prescriptions for Zeus:**

a. Promote settlement-window freeze to topology-layer structural gate (not evaluator logic).
b. Implement replay-correctness gate as required pre-merge check (use existing Chronicler).
c. Replace `ZEUS_MODE` env flag with structurally incompatible executor interfaces + `LiveAuthToken` phantom type.
d. Add position-concentration safety tiers (33% / 66% of bankroll-in-family widens entry confidence requirements).
e. Implement topology-layer capability rules as machine-readable DSL (AgentSpec trigger/predicate/action schema).

**Agent-specific consensus (Anthropic, Microsoft, AgentSpec):**
- Minimal footprint by default
- Reversibility-weighted risk assessment
- Dry-run / sandbox default
- Runtime enforcement rules, not prompt-level safety
- Context-budget-per-task scoping

### §3.3 Scientist (forensic baseline, sonnet tier)

Numbers in §2. Strongest evidence summary:

- **Profile explosion is the load-bearing failure**: 35 of 61 profiles are dead milestone templates that contribute to "ambiguous" hard-stops on current tasks they don't match.
- **39% of forbidden_files are prose, not paths**: advisory warnings encoded as hard blocks — this is the structural source of "ambiguous → block."
- **220k token bootstrap exceeds haiku entirely**: agents must read >220k just to answer "can I touch file X?"
- **155 [skip-invariant] commits in repo history; ~50 in last 60 days**: agents are actively bypassing the gate at ~1/day. This is the clearest single symptom that the gate generates more friction than confidence.
- **Antibody coverage thin**: 11 relationship-test files for 44 declared invariants.

**Highest-value first cut:** retire the 35 fossil profiles. No-risk warm-up. After that, the 39% prose stop-conditions become advisory. Remaining ~40 file-path forbidden patterns covering live DBs, settlement authority, and lifecycle grammar = the genuine hard kernel, small enough to reason about in one context window.

---

## §4 Plan Evolution (what each round added)

**Round 1 — PLAN.md (initial first-cut):**
- Names problem: profile-per-failure-mode growth + ambiguous-as-hard-stop
- Proposes: minimal hard safety kernel (§3), advisory hazard model (§4), tri-state ambiguity (§5), proof obligations (§6), retired-object housekeeping route (§7), object-boundary admission (§8)
- 5-phase migration (Tests First → Compatibility Data Model → First End-to-End Sample → First Live-Money Object Sample → Batch Reclassification)
- **Wrong primitive**: still file-permission-oriented; profile catalog preserved.

**Round 2 — PLAN_AMENDMENT.md (structural critique):**
- 15 numbered findings vs PLAN
- 3 deepest unasked questions
- Recommends: capability + invariant dual primitive; route card *generated*, not catalog-matched; hybrid router + verifier; delete `digest_profiles.py`
- Inserts **Phase 0: 15-day ultra-plan preparation** before any Phase A:
  - 0.A baseline measurement (instrument, log token costs, false-block rate)
  - 0.B capability catalog (~12-15 capabilities with intent + relationships)
  - 0.C 5 ADRs operator-signed (safety primitive, router-vs-verifier, profile-catalog deletion, paper/live type discipline, token budget)
  - 0.D fossil profile retirement (kill 35 dead profiles — no-risk warm-up)
  - 0.E capability tagging spike (one capability end-to-end)
  - 0.F shadow router build (≥7 days side-by-side)
  - 0.G replay-correctness gate scaffold
  - 0.H operator GO/NO-GO gate

**Round 3 — ULTRA_PLAN_FINAL_PREP.md (anti-drift discipline):**
- Names the **Help-Inflation Ratchet** as the meta-failure mode (opt-in helper → near-miss → new gate → mandatory → ritual → 禁书)
- Proves literary anti-drift fails: zeus-ai-handoff already has inline anti-ritual warning, drifted anyway
- **5 structural anti-drift mechanisms (M1-M5):**
  - **M1** Telemetry as first-class output (every invocation logs ritual_signal; >20% over 30d = mandatory deprecation review)
  - **M2** Opt-in by default, escalation by evidence (helpers default to advisory; mandatory invocation requires operator-signed evidence of recent miss)
  - **M3** Sunset clock per rule (90-day default; auto-demote without re-justification)
  - **M4** Original-intent contract per helper (frontmatter `original_intent.intent_test`; `does_not_fit` → helper refuses to run)
  - **M5** `INV-HELP-NOT-GATE` invariant + relationship test (helpers may suggest, advise, escalate, refuse — they may not silently expand the proof tax for unrelated claims)
- Parallel zeus-ai-handoff rescoping work stream (3 days)
- 20-hour autonomous replay as Phase 0.H acceptance test
- Self-sunset on PLAN docs themselves

**Round 4 — BEST_DESIGN.md (integrated end-state spec):**
- Five-layer architecture: STABLE (invariants/capabilities/reversibility) → SOURCE TAGGING (decorators in src/) → GENERATIVE (route function ≤500 lines) → ENFORCEMENT (5 points: edit/type/commit/pre-merge/runtime) → TELEMETRY (M1-M5 wired)
- Concrete schemas: `invariants.yaml` shape, `capabilities.yaml` shape with original_intent + sunset + lease + telemetry, `reversibility.yaml` shape (4 classes)
- Route card output spec: ≤500 tokens with 6 sections (capabilities, invariants, relationship_tests, hard_kernel_hits, reversibility, leases)
- Integration tables tracing every architect/researcher/scientist finding to a design element
- 10 quant-system failure categories each with a named structural answer
- §7 future-proofness argument: primitives are physical/economic (don't change in 1 year); accommodatable changes are appends
- **Targets**: 39,800 → ≤1,500 lines (26× reduction); 220k → ≤30k tokens bootstrap (7× reduction); 61 → 0 profiles

---

## §5 What the Cloud Session Must Produce (the deliverable)

Five markdown files in this directory:
`docs/operations/task_2026-05-06_topology_redesign/`

### 5.1 `ULTIMATE_DESIGN.md` — final integrated design
Replaces `BEST_DESIGN.md`; tightens schemas; adds anything missed.

Required sections:
- §1 One-page architecture diagram (5 layers)
- §2 Stable layer schemas (full YAML examples for invariants.yaml, capabilities.yaml, reversibility.yaml — including all M1-M5 fields)
- §3 Source tagging convention (`@capability`, `@protects` decorator definitions, semantics, examples)
- §4 Generative layer (route function pseudo-code ≤200 lines + token budget tests)
- §5 Enforcement layer (each of 5 points with: mechanism, failure mode prevented, external pattern citation, blocking-vs-advisory specification)
- §6 Telemetry + anti-drift wiring (M1-M5 each with concrete YAML/code form, not just description)
- §7 Multi-agent lease service spec (lease acquisition, contention resolution, release)
- §8 Quant-system failure category coverage (10 categories × structural answer)
- §9 What is removed / what is preserved / what is new (file inventory)
- §10 Future-proofness analysis (1-month / 6-month / 1-year scenarios + accommodation strategy)
- §11 Tradeoffs honestly named (what this design gives up; what risks remain)

### 5.2 `IMPLEMENTATION_PLAN.md` — 90-day phased plan
Phase 0 prep (15 days, 8 sub-phases A-H from PLAN_AMENDMENT) + Phase 1-5 execution (75 days).

For each phase:
- Day range
- Owner (planner / executor / architect / critic / operator-decision)
- Concrete deliverables (file paths, test names, schema entries)
- Dependencies (which prior phase must close)
- Exit criteria (numeric where possible)
- Rollback path (what to do if this phase fails)

Phase 1 candidate breakdown:
- Phase 1: Stable layer authoring (invariants.yaml refactor + capabilities.yaml authoring + reversibility.yaml) — 10 days
- Phase 2: Source decorator rollout (`@capability`, `@protects` on all writer functions) — 15 days
- Phase 3: Generative route function + token budget tests + delete digest_profiles.py — 10 days
- Phase 4: Enforcement layer (Write tool gate + LiveAuthToken phantom + diff verifier + replay-correctness gate + kill switch lift) — 20 days
- Phase 5: Telemetry + anti-drift wiring + INV-HELP-NOT-GATE + cutover + 20-hour replay re-run — 20 days

### 5.3 `RISK_REGISTER.md` — what could go wrong + mitigation
Required risks to address (add others discovered):
- R1 Source-decorator coverage incomplete → category of writers misses a capability tag → silent miss of hard-kernel hit
- R2 Replay-correctness gate produces non-determinism in legacy replay events → false-positive blocks merges
- R3 LiveAuthToken phantom type breaks an existing import → live execution test suite breaks
- R4 Shadow router disagreement rate too high → cutover indefinitely deferred
- R5 Multi-agent lease service deadlocks under contention
- R6 New design ships and within 6 months drifts back toward 禁书 (anti-drift mechanisms insufficient)
- R7 20-hour replay fixture cannot be reconstructed → acceptance test weakened
- R8 Operator decision fatigue on 6 ADRs → some signed without scrutiny

Each risk: probability (low/med/high), impact (low/med/high), structural mitigation (not "we'll be careful"), detection signal, owner.

### 5.4 `ANTI_DRIFT_CHARTER.md` — the binding doc that prevents 6-month re-drift
Standalone — survives independent of the topology redesign.

- The Help-Inflation Ratchet named, mechanism described
- M1-M5 mechanisms restated as binding rules with precise enforcement
- Sunset schedule for every artifact created in the redesign
- Telemetry review cadence (monthly critic agent, quarterly operator review)
- INV-HELP-NOT-GATE relationship test with full pseudo-code
- Mandatory mid-implementation drift checks at Phase 3 and Phase 5
- Operator override protocol (when M-rules can be temporarily relaxed and what evidence/expiry is required)

### 5.5 `CUTOVER_RUNBOOK.md` — the runbook for cutover day
The day-of operational checklist for switching from old topology to new.

- Pre-cutover gates (which CI lanes must be green, what 7-day shadow-router agreement rate is required)
- Cutover sequence (atomic switch vs. gradual; rollback trigger at each step)
- Telemetry to watch in first 24h, 7d, 30d post-cutover
- Rollback plan (full and partial)
- Post-cutover stabilization tasks (dead-code removal, doc updates, telemetry baseline reset)

---

## §6 Hard Constraints (non-negotiable)

1. **No live trading unlock authorization.** This plan does not approve removing the live trading lock.
2. **No production DB writes.** No backfill, no schema migration, no settlement rebuild authorized in this plan.
3. **No report publication.** No external distribution paths touched.
4. **No archive rewrite.** `docs/archives/**` is provenance, not active surface.
5. **No new authority plane.** New design indexes existing authority (invariants.yaml, source_rationale.yaml); does not replace it.
6. **Hard token budgets.** T0 ≤500, T1 ≤1000, T2 ≤2000, T3 ≤4000 output tokens for route card. Bootstrap ≤30,000 tokens. **These are tests; the build fails if violated.**
7. **Sunset on every new artifact.** No new YAML key, no new doc, no new ADR escapes a `sunset_date`. Default 90 days; some 12 months.
8. **Anti-drift M1-M5 binding.** All five or none — partial adoption recreates the ratchet.
9. **20-hour replay acceptance.** Phase 0.H GO requires the replay measurement; without it, no Phase 1 starts.
10. **Documentation cost ≤ documentation gain.** Every new YAML key or route-card field requires a corresponding deletion. Net add ≤ net delete.

---

## §7 Anti-Drift Discipline Applied to This Prompt Itself

The output of this ultra plan is itself subject to anti-drift:

- **ULTIMATE_DESIGN.md** carries `sunset: 2027-05-06`. Annual operator re-affirmation; otherwise auto-demote to historical.
- **IMPLEMENTATION_PLAN.md** carries `sunset: 2026-08-06`. If Phase 0 has not begun by then, plan auto-demotes; operator decides re-justify or close.
- **ANTI_DRIFT_CHARTER.md** is the only document with no sunset (it is the meta-rule). Modification requires operator signature + evidence of failure of current charter.
- **RISK_REGISTER.md** carries `quarterly_review` cadence; risks not actively mitigated within their timeframe escalate.
- **CUTOVER_RUNBOOK.md** carries `revisit_on_cutover` — the runbook is rewritten as part of the cutover deliverable, not preserved as eternal.

The cloud session must include these sunset clauses in the documents it
produces. Documents without sunset clauses do not pass acceptance.

---

## §8 What the Design Must Specifically Carry Forward (quant-system承接)

For each of these 10 failure categories, the design must name a structural
answer (not a procedural one):

1. **Wrong order to wrong market at wrong time** — phantom type at submit boundary, edit-time Write block, runtime kill switch, settlement-window freeze.
2. **Wrong data feeding probability chain** — source_validity_flip capability HARD; calibration_rebuild protects INV-04 (family separation); replay gate.
3. **Contract semantic violation (settlement, bin)** — INV-12 protected by canonical_db_write + settlement_rebuild; relationship tests required per route card.
4. **State / DB corruption** — canonical_db_write HARD; reversibility TRUTH_REWRITE escalation; Chronicler replay verifies determinism.
5. **Calibration / math regression** — replay-correctness gate as merge gate; relationship tests on Platt monotonicity.
6. **On-chain irreversibility / ghost positions** — reversibility ON_CHAIN escalation; chain reconciliation void-on-divergence; on_chain_mutation HARD.
7. **Risk-level / fail-closed bypass** — INV-21 (RED fail-closed) protected by every executor capability; runtime kill switch reads risk level.
8. **Authority confusion / archive-as-truth** — authority_doc_rewrite + archive_promotion separate HARD capabilities; reversibility ARCHIVE escalation.
9. **Multi-agent contention on shared state** — `lease_required: true` on capabilities; lease service in route card; harness-level coordination.
10. **Drift of the safety system itself** — M1-M5 anti-drift mechanisms; INV-HELP-NOT-GATE relationship test; sunset on every capability and invariant.

The design that does not have a structural answer for each of these is
incomplete. The cloud session must name the answer per category, not defer
to "we'll add that later."

---

## §9 Acceptance Criteria (the GO/NO-GO checklist)

End of Phase 0 (15 days post-kickoff) measured against this table:

| Criterion | Target | Measure |
|---|---|---|
| Stable layer total tokens | ≤30,000 | sum of three YAML files |
| Route card output (T0) | ≤500 tokens | test fixture |
| Route card output (T2) | ≤2,000 tokens | test fixture |
| Route function code | ≤500 lines Python | wc -l |
| Profile catalog entries | 0 | digest_profiles.py deleted |
| Capability tags on guarded writers | 100% | source-decorator audit |
| Hard-kernel paths blocked at Write tool | 100% | synthetic edit attempt fails |
| `LiveAuthToken` enforced at submit boundary | yes | type-check fails without token |
| Replay-correctness gate live in CI | yes | seeded regression caught |
| 20-hour replay friction | ≤2h (from ~10h) | offline replay measurement |
| `[skip-invariant]` rate over 30d post-cutover | <1/week | git log audit |
| INV-HELP-NOT-GATE relationship test | passes | CI green |
| All 5 anti-drift mechanisms wired | yes | per-mechanism test |
| Operator signs ADR-1 through ADR-6 | yes | signed file in `adr/` |
| Topology infrastructure total | ≤1,500 lines | wc -l |
| zeus-ai-handoff auto-summon disabled | yes | SKILL.md frontmatter audit |
| Total topology infrastructure reduction | ≥26× | 39,800 → ≤1,500 |
| Total bootstrap token reduction | ≥7× | 220,000 → ≤30,000 |

Operator review at Phase 0.H against this table is GO/NO-GO. **No partial GO.**

---

## §10 Operator Decisions Required Before Phase 0 Starts

Cloud session must surface these for explicit operator confirmation in the
deliverable's preface:

1. Accept invariants + capabilities as the dual primitive (vs file-path / object-cohort / relationship-only)?
2. Accept structural deletion of profile catalog (digest_profiles.py + 61 profiles)?
3. Accept type-discipline migration for executor (LiveExecutor / ShadowExecutor as separate ABCs; LiveAuthToken phantom)?
4. Accept the 26× topology-infrastructure reduction target (39,800 → ≤1,500 lines)?
5. Accept the 5 anti-drift mechanisms M1-M5 as binding (all or none)?
6. Accept the 20-hour-replay acceptance test as Phase 0.H GO gate?
7. Accept self-sunset for PLAN documents and the redesigned system?
8. Confirm Chronicler event log is sufficient for replay-correctness gate, or define additional event types needed?
9. Approve parallel zeus-ai-handoff rescoping work stream?
10. Approve the 90-day total implementation timeline?

---

## §11 Style Guide for the Deliverable

- **Concrete over abstract.** Schemas with full YAML examples, not "we will define a schema." Pseudo-code with ≤200-line route function, not "a function will compute the route card."
- **Numbers over adjectives.** Token counts, line counts, percentages. Not "significantly reduced" or "much smaller."
- **Trace every decision back to evidence.** Every design element cites architect finding #N or researcher pattern #N or scientist baseline metric.
- **Honest tradeoffs.** Section §11 of ULTIMATE_DESIGN.md must name what the design gives up. No design is free.
- **Enforce token budget.** Each deliverable file ≤6,000 words. Use tables and YAML blocks, not prose.
- **No embedded model identifiers.** Output must be model-agnostic.
- **No marketing language.** No "elegant," "beautiful," "world-class." State the property; let it stand.

---

## §12 Final Note on the Real Failure Mode

Two independent Zeus systems already drifted from "guidance" into "禁书":
topology and zeus-ai-handoff. Both had inline anti-ritual warnings. Both
drifted anyway.

The structural reason is that the systems' **primitives matched operational
shapes** (profiles, ritual invocations) rather than **physical/economic
truths** (invariants, capabilities). When primitives match operational shapes,
the system grows with operations. When primitives match physical truths, the
system stays bounded by reality.

The redesign succeeds if and only if its primitives are bounded by reality.
Anti-drift mechanisms catch residual drift. The 20-hour-replay acceptance
test grounds success in the original pain.

Do not produce a design that requires another redesign in 6 months. Do not
produce a design that needs literary anti-drift warnings to "behave."
Produce a design where the primitive cannot grow into 禁书 because the
primitive itself is finite.

The cloud session has all the context it needs. Begin.
