# Zeus Topology Redesign Plan

Status: DESIGN PROPOSAL, NOT CURRENT LAW
Date: 2026-05-06
Route: `operation planning packet`
Admitted file: `docs/operations/task_2026-05-06_topology_redesign/PLAN.md`

This packet captures a first-principles redesign direction for Zeus topology
after the object-meaning invariance audit, retired non-live execution cleanup,
and topology-noise repairs exposed recurrent routing friction.

This plan does not authorize implementation edits, live trading unlock, live
venue calls, production DB mutation, schema migration, backfill, settlement
rebuild, redemption, report publication, or authority-law changes.

## 1. Problem Statement

Topology currently protects important Zeus boundaries, but its operational
shape is too file-permission oriented. In the long object-meaning invariance
session, the same failure recurred across many waves:

- the real invariant boundary crossed producer, persistence, consumer,
  relationship tests, reports, replay, learning, and legacy bypasses;
- the selected profile admitted only a slice of that boundary;
- downstream antibody tests or active consumers were rejected as out of scope;
- `ambiguous` often became a hard stop even for read-only planning or
  housekeeping;
- fixed gate lists created proof tax for claims the task did not make;
- missing archive packet bodies or stale operations registry rows blocked
  unrelated current work;
- retired-object cleanup was routed like a feature or source behavior change,
  even when the task was to remove dead semantics without touching live state.

The core defect is not one missing profile. It is that topology does not yet
model the thing Zeus must preserve: a live-money-relevant object meaning across
module, time, state, persistence, replay, reporting, and learning boundaries.

## 2. First-Principles Purpose

Topology should be a live-money boundary router.

Its job is to answer:

1. What real object or invariant can this task affect?
2. Which surfaces are producers, persistence points, consumers, and antibodies?
3. Which proof obligations are required to make the requested claim?
4. Which side effects are absolutely forbidden without operator authority?
5. Which warnings are advisory and should not block unrelated safe work?

Topology should not be a general paperwork generator, a broad file-permission
system, or a profile-tax mechanism that requires predicting every future task
shape.

## 3. Minimal Hard Safety Kernel

Only the following classes should be absolute hard stops.

### 3.1 Live / Production Side Effects

Block unless there is explicit operator approval plus dry-run evidence,
application guard, and rollback plan:

- live venue order submission, cancel, redeem, settlement harvest, or account
  mutation;
- production or canonical live DB writes;
- destructive migrations;
- backfills or relabeling jobs that rewrite canonical truth;
- settlement rebuilds;
- report publication or external distribution;
- promoting replay, report, backtest, diagnostic, archive, graph, or scratch
  rows into live truth or learning authority.

### 3.2 Live-Money Laws

Block any change that weakens or bypasses:

- `SettlementSemantics` as the settlement-value gate;
- high/low physical-quantity and calibration separation;
- RED and DATA_DEGRADED fail-closed behavior;
- lifecycle enum grammar and terminal-state semantics;
- chain > event log > local cache reconciliation ordering;
- DB commit before derived JSON/report export;
- strategy identity via `strategy_key`;
- source truth and current-fact freshness boundaries;
- replay/report/learning non-promotion constraints.

### 3.3 Authority Non-Promotion

Block any attempt to treat these as current authority:

- `.omx/**`, `.omc/**`, runtime scratch, and local handoff notes;
- `.claude/worktrees/**` and generated worktree evidence;
- `docs/archives/**` and archived packet bodies;
- generated reports and review artifacts;
- Code Review Graph or derived graph DBs;
- generated `architecture/digest_profiles.py` without regeneration and
  equivalence proof.

### 3.4 Kernel Precedence

Typed intent, housekeeping wording, broad route admission, or caller-supplied
claims must never override the hard safety kernel.

## 4. Advisory Hazard Model

Most current `forbidden_files` entries should be reclassified as hazards unless
they hit the hard kernel.

Proposed facets:

- `live_side_effect`
- `prod_data_mutation`
- `schema_or_canonical_truth`
- `lifecycle_or_command_grammar`
- `source_truth_or_current_fact`
- `authority_doc_or_manifest`
- `generated_artifact`
- `historical_evidence`
- `runtime_scratch`
- `relationship_antibody`
- `report_projection_or_export`
- `replay_or_learning_surface`
- `housekeeping_retirement`

Facets are not just labels. They must participate in admission:

1. hard-kernel facets block;
2. generated artifacts require regeneration/equivalence proof;
3. history and scratch facets may be read as context but cannot become
   authority;
4. relationship antibodies must be preserved, replaced, or explicitly retired;
5. advisory facets emit proof obligations and warnings without blocking
   unrelated safe work.

Compatibility fields such as `allowed_files` and `forbidden_files` can remain
during migration, but they should become mirrors of the semantic decision, not
the primary decision engine.

## 5. Ambiguity Rules

`ambiguous` should become a tri-state route outcome.

### 5.1 Read-Only

Read-only exploration is allowed unless it requires credential access, secret
material, external account mutation, or live/prod side effects.

The route card may say the task is ambiguous, but it should not report a T0
read-only planning task as a blocker solely because files are not in a generic
allowlist.

### 5.2 Local Reversible Work

Local, reversible housekeeping may proceed only when no hard-kernel facet is
present and the route can infer a safe operation class.

Allowed examples:

- deleting stale comments about a retired object;
- updating active docs wording that no longer matches executable truth;
- removing obsolete tests only after antibody disposition is proven;
- regenerating derived topology profiles from the canonical source.

### 5.3 Claim / Commit Boundary

A task may not be staged, committed, PR-published, or marked complete until the
semantic boundary is typed or inferred with enough confidence to attach proof
obligations.

Ambiguous live, prod, canonical DB, schema, backfill, settlement, redemption,
or report-publication work remains blocked.

## 6. Proof Obligations

Gates should become claim-scoped proof obligations, but obligations must be
inferred automatically. Caller-supplied `--claim` may add obligations; it must
not be the only way obligations appear.

Inputs:

- hazard facets;
- side effect;
- artifact target;
- touched surfaces;
- operation stage;
- route intent;
- claims;
- active object-boundary cohort;
- generated-artifact status;
- relationship-antibody disposition.

Obligation severities:

- `blocking`: required to prove the task's completion or protect a hard
  invariant;
- `advisory`: useful evidence but not blocking for unrelated claims;
- `not_applicable`: explicitly skipped because the task does not make that
  claim.

Examples:

- Generated digest profile changed -> regeneration/equivalence proof.
- Active relationship test removed -> proof the protected object is retired or
  replaced by a negative/replacement antibody.
- Canonical state writer touched -> schema/no-live-DB-mutation proof and
  relationship test.
- Report/export touched -> proof it cannot promote diagnostic, replay, or
  legacy evidence into current authority.
- Read-only topology plan -> route card only; no implementation gates.

## 7. Retired-Object Housekeeping Route

Add a first-class route: `retired-object housekeeping`.

Purpose: remove or relabel a concept that is no longer a valid Zeus object,
without granting live mutation authority or silently rewriting historical
truth.

Required preconditions:

1. named retired object;
2. current authority or executable truth proving retirement;
3. active-reader scan;
4. downstream contamination sweep;
5. antibody disposition for tests, relationship tests, report/replay guards,
   and generated checks;
6. explicit archive policy;
7. generated artifact regeneration proof where applicable.

Allowed surface classes:

- active docs wording;
- code comments and dead config references;
- tests and fixtures after antibody disposition;
- scripts and script manifest rows;
- topology profiles and generated digest profiles;
- source rationale or registry rows needed to classify the remaining active
  surface;
- cold evidence wording only when explicitly scoped as evidence wording cleanup,
  not history rewrite.

Forbidden surface classes:

- live venue side effects;
- production DB mutation;
- schema migration;
- backfill;
- settlement rebuild;
- redemption;
- report publication;
- authority-law rewrite;
- silent legacy data relabeling.

Verification:

- active-surface semantic sweep;
- focused tests for removed/replaced antibodies;
- generated artifact regeneration/equivalence check;
- map maintenance;
- py_compile for changed Python files;
- `git diff --check`.

## 8. Object-Boundary Admission

For object-meaning invariance work, topology should admit a semantic cohort
instead of a single profile slice.

Cohort roles:

- object identity;
- producers;
- persistence;
- canonical read models;
- protective consumers;
- execution/monitor/exit consumers;
- settlement consumers;
- reports and projections;
- replay and learning consumers;
- relationship antibodies;
- legacy/compatibility bypasses;
- docs and generated companions needed for the repair.

The cohort may be manually declared first. Inference from source rationale,
tests, imports, or graph can be added later, but graph output remains derived
context and cannot decide semantic truth.

Priority object-boundary examples from the long session:

- `venue_trade_facts`
- fill authority / fill finality
- calibration-transfer OOS evidence
- settlement-event environment identity
- status-summary strategy settlement metrics
- RiskGuard bankroll/equity read models
- verified settlement -> strategy health -> report/learning surfaces

Each cohort must preserve hard no-live/no-prod/no-schema/no-backfill constraints
unless separately authorized.

## 9. Semantic Boot Parity

Add semantic boot vocabulary for high-risk task classes that repeatedly appeared
in the long session but were not first-class boot classes:

- `execution_pricing`
- `risk`
- `exchange_reconciliation`
- `settlement`
- `object_boundary`
- `housekeeping_retirement`

These are proof vocabularies, not new bureaucracy surfaces. They should name
required current-fact reads, fatal misreads, and proof questions. They should
not create a large new profile family.

## 10. Maintenance Model

### 10.1 Archive Dependencies

Semantic boot and current-fact profiles should not depend on physically present
archive packet bodies. Prefer current authority surfaces, current-fact docs, or
archive registry pointers.

Archives are history and evidence. They are not default context and must not be
promoted into active truth.

### 10.2 Generated Artifacts

Generated files are allowed only with an explicit source-of-truth and
regeneration/equivalence proof.

For `architecture/digest_profiles.py`, manual edits should be rejected unless
they are generated from `architecture/topology.yaml` and pass the equivalence
check.

### 10.3 Registry Drift

Registry rows should warn for stale or missing surfaces, but a stale packet row
should not block unrelated read-only or low-risk tasks unless the task depends
on that registry claim.

### 10.4 Profile Explosion Control

Avoid adding one bespoke profile per failure. Prefer composable dimensions:

- operation stage;
- side effect;
- hazard facets;
- object-boundary cohort;
- proof obligations;
- authority class;
- persistence and promotion risk.

## 11. Migration Plan

### Phase A: Tests First

Add tests that lock the new behavior before implementation:

1. hard-kernel paths still block;
2. ambiguous read-only planning is advisory, not a hard blocker;
3. ambiguous edit cannot be marked complete or claim-valid without typed or
   inferred boundary admission;
4. proof obligations are inferred from facets and touched surfaces;
5. retired-object housekeeping requires retirement proof and active-reader scan;
6. generated artifacts require regeneration/equivalence proof;
7. object-boundary cohort admits producer + consumer + relationship antibody
   without widening into live/prod/schema mutation.

### Phase B: Compatibility Data Model

Introduce route-card fields while preserving current outputs:

- `hard_kernel_hits`
- `hazard_facets`
- `proof_obligations`
- `object_boundary`
- `advisory_hazards`
- `claimability_status`

Keep `allowed_files`, `forbidden_files`, and `gates` as compatibility mirrors
until downstream tests and scripts are migrated.

### Phase C: First End-to-End Sample

Implement `retired-object housekeeping` as the first end-to-end conversion.

Reason: it exercises comments, tests, scripts, docs, registries, and generated
profiles while explicitly forbidding live/prod/schema/backfill authority.

### Phase D: First Live-Money Object Sample

Implement one object-boundary cohort. Candidate: `venue_trade_facts` or fill
authority.

Reason: these boundaries exercise multi-producer facts, persistence,
RiskGuard/status/report/replay/learning consumers, and relationship antibodies.

### Phase E: Batch Reclassification

Only after Phases C and D pass, reclassify broad `forbidden_files` into advisory
hazards in small batches.

Do not bulk relax:

- `src/**`;
- `docs/authority/**`;
- `src/state/**`;
- schema surfaces;
- live execution surfaces;
- production DB state;
- report publication paths.

## 12. Verification Strategy

Required verification for topology redesign implementation:

- focused topology route-card tests;
- digest-profile matching tests;
- task-boot profile tests;
- generated digest profile equivalence check;
- map-maintenance closeout check;
- static checks for hard-kernel non-bypass;
- sample route dry-runs for retired-object housekeeping and one live-money
  object boundary;
- critic review after the first implementation slice and after the first
  live-money object sample.

Verification claims must distinguish:

- route design correctness;
- hard-kernel preservation;
- advisory behavior reduction;
- object-boundary admission;
- generated artifact synchronization;
- live-money semantic safety.

## 13. Open Decisions

1. Should ambiguous low-risk local edits be allowed before typed boundary
   admission, or only read-only plus plan/test scaffolding?
2. Should retired-object cleanup be allowed to touch archive wording by default,
   or only under explicit operator scope?
3. Should object-boundary cohorts start as manual YAML declarations, or be
   inferred from source rationale plus tests?
4. Which exact files form the initial hard-kernel path list?
5. Should staging/commit hooks enforce `claimability_status`, or should the
   initial implementation keep enforcement inside topology_doctor only?

## 14. Non-Goals

This plan does not:

- approve Zeus live unlock;
- approve any live trading lock removal;
- authorize production DB writes;
- authorize report publication;
- authorize archive rewrites;
- rewrite legacy data as corrected truth;
- replace current authority docs;
- create a new authority plane.

## 15. Proposed Next Slice

The next implementation slice should be tests-only:

1. reproduce the current failure where read-only topology redesign lands in
   `generic + ambiguous + all files out_of_scope`;
2. assert the desired route-card behavior for T0 read-only ambiguity;
3. assert hard-kernel blocks still win;
4. add retired-object housekeeping route tests;
5. add inferred proof-obligation tests.
