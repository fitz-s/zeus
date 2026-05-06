# Zeus Topology Redesign — PLAN Amendment & Ultra-Plan Preparation

Status: DESIGN PROPOSAL, NOT CURRENT LAW
Date: 2026-05-06
Companion to: `PLAN.md` (same folder)
Route: `operation planning packet`
Audit basis: parallel critique from architect + external research + forensic baseline scientist (in-session 2026-05-06).

This amendment does not authorize implementation, schema migration, profile
edits, live trading unlock, or any topology runtime change. It revises the
direction of `PLAN.md` and inserts a Phase 0 (ultra-plan preparation) before
Phase A.

---

## A. Forensic Baseline (numbers PLAN.md is missing)

| Signal | Number | Implication |
|---|---|---|
| Topology infra lines (scripts + arch yaml + digest_profiles.py) | ~39,800 | 13% of Zeus Python; 1 routing line per 8 trading lines |
| Bootstrap token cost (topology + invariants + boot + AGENTS.md + source_rationale) | ~220,000 tokens | Exceeds haiku context window; ~44% of sonnet @500k |
| `digest_profiles.py` PROFILES count | 61 | 35 (57%) are fossil milestone templates (`r3-*`, `phase-N-*`) |
| `forbidden_files` total entries across profiles | 674 | 39% are prose stop-conditions, not file paths |
| INV-## declared invariants | 44 | only 11 relationship-test files; antibody coverage thin |
| `[skip-invariant]` commits in 60-day window | ~50 | ~1 bypass/day — gate generates more friction than confidence |
| `ambiguous` token frequency in topology_doctor*.py | 27 | hard-stop on ambiguity is a load-bearing primitive |

The PLAN names symptoms correctly. The numbers say the symptoms are not 12
items on a checklist; they are a single mass: profile proliferation that
makes routing too expensive to read and too coarse to trust, so agents bypass
it.

---

## B. Structural Re-diagnosis (what PLAN.md gets wrong)

PLAN.md treats this as "refine the router." The convergent evidence says
**change the primitive**.

1. **Path-based admission is approximate; capability-based is structural.** PLAN keeps "files" as the unit. The actual unit Zeus must guard is *capability* (live venue write, canonical DB write, schema migration, settlement rebuild, calibration rebuild, report publish, authority-doc rewrite, paper→live promotion, archive promotion, source-validity flip, on-chain mutation, generated-artifact bypass — ~12–15 items). Files are a derived view of who exercises what capability. Jane Street's phantom-type pattern makes this compile-time; even without OCaml, Python decorators (`@capability("live_venue_write")`) on writer functions plus a static checker get most of the benefit. **PLAN.md does not name capability as a primitive at all.**

2. **Intent-based ex-ante routing decays mid-task.** PLAN's whole flow is "agent declares route → admission → files." But agent intent rots: declaration says "read-only audit," then mid-task a one-line fix is reached for. **The structurally honest model is diff-based post-hoc verification**: route card is advisory orientation; the hard kernel runs on the actual diff before commit/PR open. Tool-level capability gating (Write tool literally disabled for non-capability paths in T0) replaces self-attestation.

3. **Profile catalog should not exist.** 61 hand-authored profiles, 35 of them fossil, is the predictable outcome of "one profile per failure shape." Route card should be **generated** from (diff × capability tags × invariant graph), not matched against a catalog. Existing 61 profiles become regression fixtures of the generator, not the engine. `architecture/digest_profiles.py` (the 6,001-line generated mirror) should be deleted; load YAML once, cache it.

4. **Relationship tests are the wrong abstraction layer in PLAN.** PLAN treats relationship antibodies as a hazard facet. CLAUDE.md operator constraint says "the order is: relationship tests → implementation → function tests. Not reversible." So relationship tests are not facets — they are the **central object**. Topology should be a query over (invariant → its relationship test → its capability tag set → its surface cohort). Files/profiles are projections. PLAN gets this backward.

5. **Shadow-mode validation is absent.** PLAN jumps Phase A → B → C with no side-by-side comparison of old vs new admission against real agent traffic. For live-money work this is unacceptable; one missed hard-block on canonical DB write is irrecoverable.

6. **Token budget is unenforced.** PLAN adds five new card fields without quantifying their cost. Operator's primary complaint is token bloat; redesign that adds fields without cost limits will repeat the failure. **Budget must be a test that the build fails:** T0 ≤ 500, T1 ≤ 1000, T2 ≤ 2000, T3 ≤ 4000 output tokens per route call.

7. **`ambiguous` tri-state still relies on agent self-attestation.** PLAN §5.1 says "read-only is allowed unless it touches credentials/live/etc." But "read-only" is the agent claiming. Real fix: harness-level — Write tool is gated by capability tags from topology, not by agent's wording.

8. **Multi-agent coordination is not modeled.** Zeus methodology heavily uses parallel subagents on the same object boundary (debate, batch dispatch). PLAN has no lease/lock model. When 3 subagents touch `venue_trade_facts` cohort simultaneously there is no contention discipline.

9. **§3.2 "live-money laws" are prose, not machine-checked relationships.** Each item (SettlementSemantics, dual-track separation, RED fail-closed, chain>event>cache, DB-before-JSON, strategy_key identity) must point to (a) invariant ID in `invariants.yaml`, (b) relationship test path, (c) capability tag set. Items missing one of the three are structurally unenforced — they are advisory pretending to be law.

10. **No reversibility classification.** Anthropic's agent-safety law is "prefer reversible actions; escalate irreversible ones." Zeus's irreversibilities are ranked: irreversible-on-chain (CLOB fill, redeem) > irreversible-truth-rewrite (canonical DB mutation, archive rewrite) > irreversible-in-archive (history rewrite) > reversible (working tree edit). PLAN treats all hard-kernel items uniformly. They're not.

11. **Settlement-window freeze is in evaluator code, not at topology.** Settlement-proximity and risk-level structural gates should live in topology and be non-bypassable by any evaluator path. PLAN does not lift these from evaluator into the kernel.

12. **Replay-correctness as a merge gate is not proposed.** Zeus already has Chronicler (append-only event log per AGENTS.md). Replay-as-regression-test is a structural pattern major banks use for MiFID/Dodd-Frank. PLAN does not use the existing event log to power a replay gate — even though doing so would make calibration changes structurally safe at no new infrastructure cost.

13. **Position-concentration safety widening is absent.** Standard quant practice (Citadel three-pillar): as realized concentration crosses 33%/66% of bankroll-in-family, safety margins widen automatically. PLAN does not encode size-driven gate escalation.

14. **Paper/live isolation is still a runtime flag.** `ZEUS_MODE` is `os.environ`. World-class quant pattern (QuantConnect, Jane Street): structurally incompatible interfaces (`LiveExecutor` vs `ShadowExecutor` implement different ABCs; `LiveAuthToken` is a phantom type required at the order-submission boundary). Type system makes the wrong code unwritable. PLAN preserves the flag.

15. **No measurement-driven exit criteria for migration.** Phases C/D/E in PLAN have no numeric success thresholds (false-block rate, miss-on-irreversible, token cost, agent override count). Migration without measurement is faith-based.

---

## C. The Three Deepest Questions PLAN Never Asks

1. **Should topology be a router or a verifier?** Routers answer "what can I do?" before action. Verifiers answer "what did you do and is it safe?" after. For LLM agents whose intent rots mid-task, the verifier is structurally sound; the router is a polite fiction. Recommendation: **hybrid** — capability-gated tools at edit time + diff verification at commit time + advisory route card at orient time. PLAN never asks.

2. **What is the actual unit of safety — files, capabilities, invariants, or relationships?** PLAN picks "object-boundary cohort" without adjudicating among the four. The right answer for a live-money quant system is **invariant + capability dual primitive**, with files/cohorts as projections. Picking the wrong primitive guarantees another redesign in 6 months.

3. **Should agents see profiles at all, or should profiles be invisible compiler internals?** Agent-visible routing creates intent-gaming: agent retries with different wording to get a different profile match. If route card surface were minimal ("touchable: yes/no/proof-required; here are 3 invariants in scope; here are 2 capabilities you'd touch") and the rest were compiler-internal, agents would stop optimizing against the routing layer. PLAN keeps profiles agent-visible.

---

## D. Specific Section-by-Section PLAN Edits

| PLAN Section | Recommended Action |
|---|---|
| §1 Problem Statement | Add forensic numbers from §A above. Reframe defect as "wrong primitive" not "wrong profile slice." |
| §2 First-Principles Purpose | Add hard token budget (T0 ≤500, T1 ≤1000, T2 ≤2000, T3 ≤4000 output tokens) as constraint #6. |
| §3.1 Live Side Effects | Re-rank by reversibility class: on-chain irreversible > truth-rewrite irreversible > archive irreversible > reversible. Different escalation per class. |
| §3.2 Live-Money Laws | Each item must point to (invariant ID, relationship test path, capability tag). Items missing all three become advisory until antibodied. List the gaps explicitly. |
| §3.3 Authority Non-Promotion | Keep. |
| §4 Advisory Hazard | DROP the bespoke 13-facet enum. Replace with two orthogonal axes: capability touched (~12 items) × reversibility class (4 items). 48-cell matrix; most cells empty; remaining cells are derivable from source_rationale. |
| §5 Ambiguity Rules | Replace "agent-declared read-only" with "harness-level capability gating": Write tool is enabled only when current capability set permits. Self-attestation removed from primary admission. |
| §6 Proof Obligations | Keep concept. Inputs become (diff × capabilities × invariants × reversibility class). Caller-supplied `--claim` is advisory only. |
| §7 Retired-Object Housekeeping | Promote from a route name to a first-class capability `housekeeping_retirement_authority`. Apply same diff-based admission. |
| §8 Object Boundary Cohort | Cohort is a **query** over (`source_rationale.write_routes` + import graph + relationship test discovery), not a manual YAML artifact. Manual override only for inference ambiguity. |
| §9 Semantic Boot Parity | Add capability vocabulary to boot vocabulary. Each boot profile names required capabilities, not just files. |
| §10 Maintenance Model | Add §10.5: delete `architecture/digest_profiles.py` (load YAML at startup, cache). Add §10.6: instrument router for token cost + decision logs. |
| §11 Migration | Insert Phase 0 (this amendment §E). Reframe Phase A tests against the new primitive. |
| §13 Open Decisions | Most resolve once §C question 2 (the safety primitive) is answered. Remaining become ADR-2/3/4 in Phase 0. |
| §15 Proposed Next Slice | Replace with Phase 0.A baseline measurement + 0.D fossil retirement (no-risk warm-up). |

---

## E. Ultra-Plan Preparation: Phase 0 (15 working days)

PLAN.md jumps from problem statement to Phase A (tests) without grounding. The
ultra plan needs a measurement and decision predecessor. **No Phase A starts
before Phase 0 closes.**

### Phase 0.A — Baseline Measurement (Days 1–5)

Owner: executor (sonnet) under operator review.
Output: `docs/operations/task_2026-05-06_topology_redesign/baseline_metrics.md`.

Instrument `scripts/topology_doctor.py` with a session-local decision log
capturing: invocation timestamp, input tokens, output tokens, route admitted,
files declared, agent's eventual diff (sampled at PR-open), post-hoc safety
verdict from a critic agent.

Run instrumented router for 5 days against real agent traffic. Required
metrics in the report:

- median + p95 input/output tokens per route call;
- false-block count (route blocked, post-hoc safe);
- miss-on-irreversible count (route admitted, post-hoc unsafe);
- profile-match distribution (which of 61 profiles are actually hit);
- override frequency (`[skip-invariant]` and equivalent).

Exit criterion: numeric baseline locked; no design proceeds without it.

### Phase 0.B — Capability Catalog (Days 3–5, parallel)

Owner: architect (opus) advise; executor (sonnet) write.
Output: `architecture/capabilities.yaml` (new file, 12–15 entries).

Enumerate every Zeus capability whose unauthorized exercise is irrecoverable
or truth-rewriting. Candidate set:

```
1.  live_venue_order        # CLOB submit/cancel/redeem
2.  live_venue_settlement   # harvest, redeem
3.  canonical_db_write      # state/zeus*.db (live truth)
4.  canonical_truth_rewrite # backfill/relabel of historical canonical rows
5.  schema_migration        # state DB schema changes
6.  settlement_rebuild      # rewriting settlement_value semantics
7.  calibration_rebuild     # rewriting calibration training data
8.  report_publish          # external distribution paths
9.  authority_doc_rewrite   # docs/authority/** semantics changes
10. live_config_mutation    # config/settings.json runtime-read fields
11. archive_promotion       # docs/archives/** → current authority
12. source_validity_flip    # current_*_validity.* changes
13. paper_to_live_promotion # ZEUS_MODE escalation
14. on_chain_mutation       # any ETH RPC tx send
15. generated_artifact_bypass # editing digest_profiles.py without regen
```

For each, record: (a) invariant ID(s) it protects, (b) relationship test
path(s), (c) reversibility class, (d) writer functions/modules in `src/`.

Exit criterion: every §3.2 PLAN.md item maps to ≥1 capability with all
fields present. Items lacking ≥1 field become advisory-only with a
follow-up antibody ticket.

### Phase 0.C — Operator Architectural Decision Records (Days 5–7)

Owner: operator decision; planner (opus) drafts.
Output: 5 single-page ADRs under `docs/operations/task_2026-05-06_topology_redesign/adr/`.

| ADR | Decision | Recommended |
|---|---|---|
| ADR-1 | Safety primitive | invariant + capability dual primitive; files = projection |
| ADR-2 | Router vs verifier | hybrid: capability-gated tools (edit-time) + diff verifier (commit-time) + advisory route card (orient-time) |
| ADR-3 | Profile catalog | delete hand-authored profiles; route card generated from (diff × capabilities × invariants); existing 61 profiles → regression fixtures |
| ADR-4 | Paper/live isolation | structurally incompatible `LiveExecutor`/`ShadowExecutor` ABCs; `LiveAuthToken` phantom type at order boundary; retire `ZEUS_MODE` env flag for live admission |
| ADR-5 | Token budget | T0 ≤500, T1 ≤1000, T2 ≤2000, T3 ≤4000 output tokens; >budget = failing topology test |

Exit criterion: operator signs all 5 ADRs (accept / amend / reject). No
later phase contradicts a signed ADR.

### Phase 0.D — Fossil Profile Retirement (Days 7–9)

Owner: executor (sonnet).
Output: one tests-only PR plus one retirement PR.

Identify the 35 fossil profiles (`r3-*`, `phase-N-*`, frozen sprint
templates). For each, prove non-match against last 30 days of router traffic
(0.A logs). Retire in a single batch. Tests-only PR first locks the
post-retirement route-card behavior; retirement PR follows.

This is no-risk and reduces routing surface by 57% before any new design
ships. Use as warm-up for Phase A discipline.

Exit criterion: 26 first-principles + meta profiles remain; routing token
cost on representative tasks reduced by ≥30%.

### Phase 0.E — Capability Tagging Spike (Days 9–12)

Owner: executor (opus, complex refactor).
Output: end-to-end demo for ONE capability (recommend `live_venue_order`).

Tag every code path that exercises `live_venue_order` with a decorator or
type marker. Implement the tool-level gate (Write disabled on tagged paths
unless capability is in active set). Implement the diff verifier (PR open
fails if diff touches tagged path without explicit capability acquisition
in the route card). Run end-to-end on a synthetic test branch.

Exit criterion: synthetic test branch attempting to edit a
`live_venue_order` path is blocked at three layers (route card warning →
Write tool refusal → diff verifier rejection). No false blocks on unrelated
paths.

### Phase 0.F — Shadow Router Build (Days 7–14, parallel)

Owner: executor (sonnet).
Output: shadow router emitting both old and new route cards; disagreement
log under `docs/operations/task_2026-05-06_topology_redesign/shadow_log/`.

Implement new admission logic (capability-tag-driven, no profile catalog).
Run alongside existing for ≥7 days. Log every disagreement, especially:
old-blocks/new-allows (highest risk; potential miss) and
old-allows/new-blocks (false-block reduction signal).

Exit criterion: ≥7 days of shadow traffic; old-blocks/new-allows count is
zero or every instance has a documented review verdict; old-allows/new-blocks
explains a real safety improvement.

### Phase 0.G — Replay-Correctness Gate Scaffold (Days 12–15)

Owner: executor (sonnet).
Output: `scripts/replay_correctness_gate.py` + CI lane.

Use existing Chronicler event log. Take the last 7 days of events; replay
through the current evaluator on a clean checkout; compare resulting
projections against archived snapshots. Fail-on-mismatch as a new advisory CI
lane. Even before any topology shift ships, this captures category errors
structurally and is independent of the redesign.

Exit criterion: gate passes on `main`; gate fails on a synthetic regression
(seed a known-bad calibration change, prove gate catches it).

### Phase 0.H — Decision Gate (Day 15)

Owner: operator + critic-opus review.
Output: GO / NO-GO verdict on Phase A start.

Operator reviews: 0.A baseline, 0.B catalog, 0.C ADRs (signed),
0.D retirement results, 0.E capability spike, 0.F shadow disagreement
report, 0.G replay gate proof.

GO if: token-cost reduction ≥30%, capability spike demonstrated end-to-end,
shadow disagreement risk-classified, replay gate live, all ADRs signed.
NO-GO returns to ultra-plan refinement. **No Phase A test commit lands
without this gate passing.**

---

## F. Cross-Cutting Constraints (apply through all phases)

1. **No live trading unlock.** No phase authorizes live trading lock removal,
   live venue calls, production DB mutation, schema migration, backfill,
   settlement rebuild, redemption, or report publication. Phase 0 is
   measurement, design, and safety scaffold only.

2. **Generated artifact discipline.** Any edit to `architecture/digest_profiles.py`
   between now and ADR-3 sign-off requires regeneration + equivalence
   proof. After ADR-3 it is deleted.

3. **Antibody-first.** Every new admission rule lands as a failing test
   first, then implementation. No exceptions. PLAN §11 already says this;
   reaffirm.

4. **Budget enforcement is a test.** Phase A test set includes a
   token-budget regression test per tier. Budget violation = build red.

5. **Documentation cost.** Every new YAML key or route-card field added in
   Phases 0.B–0.E must come with a deletion: which existing YAML key or
   field becomes dead and is removed. Net add ≤ net delete.

6. **Multi-agent lease model (deferred to Phase B+ but stub now).** Phase
   0.B catalog records a `lease_required: bool` per capability. Capabilities
   exercised by parallel subagents (e.g., calibration_rebuild) require
   session-scoped lease in the eventual design. Stub the field; defer
   enforcement.

---

## G. Open Operator Questions (must be answered before Phase 0.C ADR sign-off)

1. Is the operator willing to delete `architecture/digest_profiles.py`
   entirely (ADR-3) — accepting that all profile authority lives in YAML
   loaded once at process start?
2. Is the operator willing to retire `ZEUS_MODE` env flag in favor of
   structurally-typed executor classes (ADR-4)? This is the largest API
   surface change.
3. Should Phase 0.E capability spike target `live_venue_order` (highest
   risk, most learning) or `report_publish` (lower risk, faster)?
4. Should the shadow router (Phase 0.F) emit user-visible warnings on
   disagreement, or be silent for the 7-day window?
5. Is the operator willing to sign ADR-2 (hybrid router+verifier) given it
   requires a harness-level Write-tool gate that is unfamiliar to current
   Zeus tooling?

---

## H. Estimated Outcomes

If Phase 0 closes with GO verdict and Phases A–E complete on the new
primitive:

| Metric | Current | Target |
|---|---|---|
| Bootstrap token cost | ~220k | ≤30k (capabilities + invariants + minimal route schema) |
| `digest_profiles.py` lines | 6,001 | 0 (deleted) |
| Hand-authored profiles | 61 | 0 (route card is generated) |
| `forbidden_files` entries | 674 | derived; ≤50 explicit kernel paths |
| `[skip-invariant]` rate | ~50/60d | <10/60d (gate trustworthy) |
| False-block rate | unmeasured | <5% measured |
| Miss-on-irreversible | unmeasured | 0 by structural design (capability gate at tool layer) |
| Route card output (T0/T1) | ~500 tokens | ≤500 / ≤1000 enforced by test |

These are aspirations; Phase 0.A baseline establishes whether they are
achievable.

---

## I. References

- `PLAN.md` — base plan (this amendment companions it).
- `AGENTS.md:1-100` — money-path mental model and probability chain.
- `architecture/topology.yaml:1-160` — coverage roots and authority roles.
- `architecture/digest_profiles.py:1-300` — generated profile artifact (slated for ADR-3 deletion).
- `architecture/invariants.yaml` — INV-01..INV-44 (the actual safety primitives).
- `architecture/source_rationale.yaml:19-73` — `write_routes` already encodes capability ownership; lift to first-class.
- `architecture/task_boot_profiles.yaml:38-86` — proof-question pattern; generalize.
- `scripts/topology_doctor.py:147-210` — `RUNTIME_RISK_GATE_BUDGETS`, `RUNTIME_CLAIM_CONTRACTS` — existing capability/claim hooks.
- Jane Street, *Static Access Control via Phantom Types* — capability primitive precedent.
- FIA, *Automated Trading Risk Controls Best Practices 2024* — pre-trade kill-switch architecture.
- Optiver, *Three Pillars of Trading* — pre/post-trade reconciliation.
- AgentSpec (arxiv:2503.18666) — runtime trigger/predicate/action DSL.
- Anthropic, *Building Effective Agents* — minimal-footprint, reversibility-weighted, capability-autonomy inverse.
- QuantConnect *Paper Trading docs* — paper/live as type discipline.
- Citadel three-pillar risk framework — position-concentration safety widening.

---

## J. Non-Goals (re-asserted from PLAN.md §14)

This amendment does not approve Zeus live unlock, live trading lock removal,
production DB writes, report publication, archive rewrites, legacy data
relabeling, current-authority replacement, or a new authority plane.
