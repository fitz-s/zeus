# IMPLEMENTATION_PLAN

## Sunset: 2026-08-06

If Phase 0 has not begun by 2026-08-06, this plan auto-demotes to
`docs/operations/historical/`. Operator decides re-justify or close.
Charter rule M3 (`ANTI_DRIFT_CHARTER.md §5`) governs.

## §0 Preconditions before Phase 0

All ten operator decisions in `ULTIMATE_DESIGN.md §0` must be signed.
Six ADRs (ADR-1..ADR-6) collapse those decisions; ADRs are
Phase 0.C deliverables.

No code change in `src/`, `scripts/`, or `architecture/` happens before
Phase 0.H GO. The 5 deliverable markdowns in this directory are the only
artifacts produced before that gate.

## §1 Phase summary

| Phase | Days | Owner | Headline |
|---|---|---|---|
| 0.A | 1-3 | planner + critic | Baseline measurement |
| 0.B | 1-5 | architect | Capability catalog (~16) authored |
| 0.C | 1-7 | operator | 6 ADRs signed |
| 0.D | 3-7 | implementer | Fossil profile retirement (35 profiles) |
| 0.E | 5-10 | implementer | Capability tagging spike (one capability) |
| 0.F | 7-15 | implementer | Shadow router build, 7-day side-by-side |
| 0.G | 7-15 | implementer | Replay-correctness gate scaffold |
| 0.H | 15 | operator | GO / NO-GO against acceptance table |
| 1 | 16-25 | implementer | Stable layer authoring |
| 2 | 26-40 | implementer | Source decorator rollout |
| 3 | 41-50 | implementer + critic | Generative route function + delete digest_profiles + **mid-drift check** |
| 4 | 51-70 | implementer | Enforcement layer (5 gates) |
| 5 | 71-90 | implementer + critic + operator | Telemetry + INV-HELP-NOT-GATE + cutover + 20h replay re-run + **mid-drift check** |

Days are sequential. Sub-phases within a phase may overlap; phase
boundaries are exit gates.

## §2 Phase 0 — Preparation (15 days)

### 0.A Baseline measurement (days 1-3)

**Owner:** planner + critic.
**Deliverables:**
- `evidence/baseline/topology_token_cost.md` — measured bootstrap token
  cost on 5 representative tasks (target row in briefing §9: ≤30,000
  post-cutover; baseline today is ~220,000).
- `evidence/baseline/false_block_rate.md` — last-30d audit of
  topology_doctor blocks where the agent ultimately bypassed via
  `[skip-invariant]`. Baseline: **159 [skip-invariant] commits / 60d**
  (verified) → ~2.6/day.
- `evidence/baseline/20h_replay_friction.md` — offline replay of the
  ~20-hour autonomous session referenced in briefing §1. Measures
  topology-attributable friction in hours. **Acceptance target ≤2h
  post-cutover** (briefing §9 row "20-hour replay friction").

**Dependencies:** none.
**Exit criteria:** all three measurement files exist; numbers are
machine-readable (front-matter or JSON sidecar) so Phase 0.H can compare.
**Rollback:** measurement-only; no rollback needed.

### 0.B Capability catalog authoring (days 1-5)

**Owner:** architect.
**Deliverables:** draft `architecture/capabilities.yaml` with ~16 entries.

Seed 9 from existing `source_rationale.yaml::write_routes`:
canonical_position_write, control_write, settlement_write,
backtest_diagnostic_write, calibration_persistence_write,
calibration_decision_group_write, decision_artifact_write,
venue_command_write, script_repair_write.

Add 7 net-new: live_venue_submit, on_chain_mutation,
authority_doc_rewrite, archive_promotion, source_validity_flip,
calibration_rebuild, settlement_rebuild.

Each entry must conform to ULTIMATE_DESIGN §2.2 schema (intent,
relationships, hard_kernel_paths, original_intent, sunset_date,
lease_required, telemetry, reversibility_class).

**Dependencies:** 0.A baseline.
**Exit criteria:** YAML schema validates; each entry has
`sunset_date: 2027-05-06`; each entry has `original_intent.intent_test`
that is a single line; review checklist signed.
**Rollback:** delete the draft file; redo in Phase 1.

### 0.C ADRs (days 1-7)

**Owner:** operator (signs); architect (drafts).
**Deliverables:** `docs/operations/task_2026-05-06_topology_redesign/adr/`:
- `ADR-1_primitive_choice.md` — invariants + capabilities as dual primitive
- `ADR-2_profile_catalog_deletion.md` — delete digest_profiles + 26× / 19×
  reduction target
- `ADR-3_paper_live_type_discipline.md` — LiveAuthToken phantom +
  separate ABCs
- `ADR-4_anti_drift_binding.md` — M1-M5 binding (CHARTER); self-sunset
  on PLAN docs
- `ADR-5_acceptance_gate_replay_event_scope.md` — 20-hour replay as
  Phase 0.H GO; Chronicler event scope confirmation
- `ADR-6_scope_timeline.md` — zeus-ai-handoff parallel work stream;
  90-day timeline

**Exit criteria:** all 6 signed (operator hash in frontmatter); each
sunset_date set to 2027-05-06.
**Rollback:** unsigned ADR blocks Phase 0.H GO.

### 0.D Fossil profile retirement (days 3-7)

**Owner:** implementer.
**Deliverables:** delete 35+ fossil profiles from
`architecture/topology.yaml :: digest_profiles:` (the `r3-*`,
`phase-N-*`, `batch h *`, `observability *` entries) and regenerate
`architecture/digest_profiles.py`.

**This is the no-risk warm-up** (briefing §3.3). It tests the
operator-cycle on a low-stakes change, demonstrates the deletion can be
made without breaking active flows, and removes dead state that
contributes to false-block ambiguity.

**Dependencies:** 0.B (catalog must exist so we can confirm no fossil
profile is the sole owner of a capability hard-kernel path).
**Exit criteria:** 35+ profiles deleted; CI green; spot check of 3
recent task types still produces sensible route output from the legacy
system.
**Rollback:** `git revert` of the deletion commit. Trivial.

### 0.E Capability tagging spike (days 5-10)

**Owner:** implementer.
**Deliverables:** end-to-end on **one capability only** —
`settlement_write` is recommended (highest-risk; tightest spec).

- `src/architecture/decorators.py` (≤120 LOC) with `@capability` and
  `@protects`.
- `@capability("settlement_write")` and `@protects("INV-02", "INV-14")`
  applied to `src/execution/harvester.py::write_canonical_settlement`.
- `tests/test_capability_decorator_coverage.py` passes for that one
  capability.

**Dependencies:** 0.B (capability entry exists).
**Exit criteria:** decorator fires on import; CI lint asserts the
guarded path carries the tag; route function (placeholder, returns
fixed RouteCard) renders a card that mentions the capability.
**Rollback:** revert the decorator commit; remove the test.

### 0.F Shadow router build (days 7-15)

**Owner:** implementer.
**Deliverables:** parallel `route_function` runs side-by-side with the
existing `topology_doctor` for ≥7 days on real agent traffic. Output
agreement matrix (`evidence/shadow_router/agreement_<date>.md`).

**Dependencies:** 0.B, 0.E (one capability tagged so a real round-trip
is possible).
**Exit criteria:** ≥7 calendar days of side-by-side; agreement rate
≥90% (target 98% pre-cutover; this is a Phase 0 floor); disagreements
classified into "shadow correct" vs "legacy correct" (R4 mitigation).
**Rollback:** disable shadow runner; legacy continues unchanged.

### 0.G Replay-correctness gate scaffold (days 7-15)

**Owner:** implementer.
**Deliverables:** wrapper around `src/state/chronicler.py` and
`scripts/replay_parity.py` that:
- Picks a fixed seed window (e.g., last 7 days of canonical events).
- Re-runs the projection deterministically.
- Compares to a baseline snapshot.
- Returns 0 / non-zero on match / mismatch.
- Emits `ritual_signal` (CHARTER §3 M1).

**Dependencies:** ADR-5 confirmed Chronicler event scope (briefing §10
decision #8).
**Exit criteria:** scaffold runs in <60s on a 7-day window; seeded
regression test catches a deliberately introduced mismatch; CI lane
exists but is not yet a merge gate.
**Rollback:** scaffold is opt-in; disable the CI lane.

### 0.H Operator GO / NO-GO (day 15)

**Owner:** operator.
**Deliverables:** signed `evidence/phase0_h_decision.md` against the
briefing §9 acceptance table:

| Criterion | Phase-0 floor | Phase-5 target |
|---|---|---|
| Capability catalog drafted | yes | yes |
| Fossil profile retirement complete | yes | yes |
| Capability tagging spike (1 capability) | yes | 100% |
| Shadow router agreement ≥7d | ≥90% | ≥98% |
| Replay-correctness scaffold runs | yes | merge gate live |
| 20-hour replay friction baseline measured | yes | ≤2h |
| 6 ADRs signed | yes | yes |

**No partial GO** (briefing §6 hard constraint). If any row is missing,
operator either signs a §9 charter override (≤14d expiry) or halts.
**Rollback:** the four prior phases are individually reversible (revert
commits); §9 lists individual rollback paths.

## §3 Phase 1 — Stable layer authoring (days 16-25, 10d)

**Owner:** implementer.
**Deliverables:**

| Day | Work |
|---|---|
| 16-18 | Migrate `architecture/capabilities.yaml` from Phase 0.B draft to canonical state; ensure 16 entries finalized |
| 17-19 | Author `architecture/reversibility.yaml` with 4 classes (ULTIMATE_DESIGN §2.3) |
| 18-22 | Extend `architecture/invariants.yaml`: append `capability_tags`, `relationship_tests`, `sunset_date` to all 34 entries; INV-11/INV-12 gap audit (R9) and ID-policy decision recorded in §2.1 |
| 22-25 | Decide and execute disposition of `architecture/topology_schema.yaml` (537 LOC) and `architecture/inv_prototype.py` (348 LOC) (R12) — subsume into capabilities.yaml + decorators OR retain with explicit mapping |

**Dependencies:** Phase 0.H GO.
**Exit criteria** (numeric, briefing §9):
- Stable layer total tokens ≤30,000.
- Schema validators all green:
  `tests/test_charter_sunset_required.py`,
  `tests/test_charter_mandatory_evidence.py`.
- INV gap policy documented; R9 closed.
- Net-add ≤ net-delete invariant (briefing §6 #10) verified for the
  phase.
**Rollback:** all changes are YAML; revert commit.

## §4 Phase 2 — Source decorator rollout (days 26-40, 15d)

**Owner:** implementer.
**Deliverables:**

| Day | Work |
|---|---|
| 26-28 | Promote `src/architecture/decorators.py` from spike to production module |
| 28-35 | Apply `@capability` to all writer functions whose paths appear in capabilities.yaml :: hard_kernel_paths (~50-80 functions across `src/execution/`, `src/state/`, `src/calibration/`, `src/control/`) |
| 32-38 | Apply `@protects` to invariant-anchor functions (one per invariant relationship test target) |
| 36-40 | Run full `tests/test_capability_decorator_coverage.py` and fix coverage gaps |

**Dependencies:** Phase 1.
**Exit criteria** (briefing §9):
- 100% of guarded writer functions carry `@capability`.
- 100% of invariant-anchor functions carry `@protects`.
- CI lint green.
- Phase 2 introduces ≤200 net LOC (decorator + audit fixtures);
  decorator additions to existing files do not count as new LOC.
**Rollback:** decorators are no-ops at runtime initially; can be removed
file-by-file. CI lint can be downgraded to warning.

## §5 Phase 3 — Generative route function + delete digest_profiles (days 41-50, 10d)

**Owner:** implementer + critic.
**Deliverables:**

| Day | Work |
|---|---|
| 41-44 | Author `src/architecture/route_function.py` (≤200 LOC pseudo-code in ULTIMATE_DESIGN §4) |
| 43-46 | Author `tests/test_route_card_token_budget.py` (T0..T3) |
| 45-48 | Delete `architecture/digest_profiles.py` (6,001 LOC); delete `scripts/topology_doctor_digest.py`, `_packet_prefill.py`, `_context_pack.py`, `_core_map.py`; delete `topology.yaml :: digest_profiles:` block (remaining ~25 profiles after Phase 0.D) |
| 48-50 | **Phase 3 mid-drift check** — critic runs M5 test suite; verifies no helper has acquired a `forbidden_files` field that crosses capability boundaries; reads ritual_signal from Phase 2 traffic; signs `evidence/phase3_drift_check.md` |

**Dependencies:** Phase 2 (decorators must be in place so route function
has data to query).
**Exit criteria** (briefing §9):
- Route function code ≤500 LOC (Python `wc -l`).
- Profile catalog entries: 0.
- T0 ≤500 tokens, T1 ≤1000, T2 ≤2000, T3 ≤4000 — all four tests green.
- Phase 3 mid-drift check signed.
- Net delete ≥ net add for the phase: ≥6,001 LOC removed (digest_profiles
  alone) vs. ≤200 LOC added (route function + tests).
**Rollback:**
- Route function: revert; restore digest_profiles from git tag
  `pre-phase3` (created 41-day-1).
- Mid-drift check fail: phase halts; operator signs override or rolls
  back Phase 2 + Phase 3.

## §6 Phase 4 — Enforcement layer (days 51-70, 20d)

**Owner:** implementer.
**Deliverables (one gate per sub-phase):**

| Day | Gate (ULTIMATE_DESIGN §5) |
|---|---|
| 51-55 | **Gate 1: Edit-time** — Write-tool capability hook; consults route card; refuses or warns per reversibility class |
| 56-60 | **Gate 2: Type-time** — `LiveAuthToken` phantom; `LiveExecutor` / `ShadowExecutor` ABCs; mypy/pyright red on missing token at submit boundary |
| 61-64 | **Gate 3: Commit-time** — diff verifier; reads decorators on changed files; rejects commit if `original_intent.does_not_fit` matches the task |
| 65-67 | **Gate 4: Pre-merge** — replay-correctness from Phase 0.G promoted to required CI lane (R2 mitigation: deterministic seed window only) |
| 68-70 | **Gate 5: Runtime** — kill switch + settlement-window freeze lifted from evaluator code to topology layer; non-bypassable |

**Dependencies:** Phase 3.
**Exit criteria** (briefing §9):
- Hard-kernel paths blocked at Write tool: 100% (synthetic edit attempt
  fails).
- LiveAuthToken enforced at submit boundary: yes (type-check fails
  without token).
- Replay-correctness gate live in CI: yes; seeded regression caught.
- Each gate emits `ritual_signal` per CHARTER §3.
- Each gate carries `sunset_date` 90 days from authoring (CHARTER §5).
**Rollback per gate:**
- Gate 1: feature flag `ZEUS_ROUTE_GATE_EDIT=off`.
- Gate 2: phantom type changes are non-trivial to roll back; mitigation
  is a `@untyped_for_compat` escape hatch with 30-day expiry.
- Gate 3: feature flag `ZEUS_ROUTE_GATE_COMMIT=off`.
- Gate 4: CI lane disable.
- Gate 5: documented manual override per CUTOVER_RUNBOOK.

## §7 Phase 5 — Telemetry + cutover + 20h replay re-run (days 71-90, 20d)

**Owner:** implementer + critic + operator.
**Deliverables:**

| Day | Work |
|---|---|
| 71-74 | Author `tests/test_help_not_gate.py` with full INV-HELP-NOT-GATE coverage (CHARTER §7); ship as required CI |
| 73-77 | Wire M1-M5 telemetry: `logs/ritual_signal/<YYYY-MM>.jsonl` writers in every gate from §5 + route function; monthly critic review job scheduled |
| 76-80 | `zeus-ai-handoff` rescoping work stream (parallel; briefing §10 decision #9): apply M2 (`mandatory: false` default), M4 (`original_intent` frontmatter), M5 (covered by INV-HELP-NOT-GATE) |
| 80-83 | Run 20-hour replay (re-execute the original 2026-05 autonomous session under the new system); measure topology-attributable friction; baseline from Phase 0.A is ~10h, target ≤2h |
| 83-85 | **Phase 5 mid-drift check** — critic + operator review of M1 ritual_signal accumulated since Phase 4 cutover; check no new helper has acquired cross-capability blocking |
| 85-90 | Cutover per CUTOVER_RUNBOOK gradual sequence; first-24h / 7d / 30d telemetry watch |

**Dependencies:** Phase 4 + ≥7d of Phase 4 telemetry data.
**Exit criteria** (briefing §9):
- 20-hour replay friction ≤ 2h (acceptance test).
- All 5 anti-drift mechanisms wired (per-mechanism test green).
- INV-HELP-NOT-GATE relationship test passes.
- `zeus-ai-handoff` auto-summon disabled (SKILL.md frontmatter audit
  shows `mandatory: false`).
- `[skip-invariant]` rate over 30d post-cutover < 1/week
  (R10 floor: <2/week pre-cutover in shadow).
- Total topology infrastructure ≤1,500 LOC.
- Total bootstrap ≤30,000 tokens.
**Rollback:**
- Cutover halts at any sub-step that fails its CUTOVER_RUNBOOK
  threshold; the per-gate flags from Phase 4 disable the new
  enforcement; legacy `topology_doctor` re-enabled from git tag.

## §8 Cross-phase invariants (always true)

These are not phase deliverables but guard rails the implementer
verifies at every commit:

- **Hard-token-budget tests are green** (briefing §6 #6). T0..T3 token
  caps are failing CI on violation, not soft warnings.
- **Sunset on every new artifact** (briefing §6 #7). PR template asks
  for `sunset_date` on every new YAML key; missing field fails
  `tests/test_charter_sunset_required.py`.
- **No live trading unlock** (briefing §6 #1). The plan contains zero
  references to enabling `ZEUS_HARVESTER_LIVE_ENABLED` or any other
  live-mode flag. Phase 4 Gate 2 strengthens the lock.
- **No production DB writes** (briefing §6 #2). All testing uses
  fixtures and the seed window from Phase 0.G.
- **No archive rewrite** (briefing §6 #4). `docs/archives/**` is read,
  never written.

## §9 Phase-by-phase rollback dependency table

| Phase | Reversible by | Affects upstream | Affects downstream |
|---|---|---|---|
| 0.A-C | revert commits / unsign ADR | none | blocks 0.D-H |
| 0.D | git revert deletion | requires 0.B | enables Phase 3 cleanly |
| 0.E | revert decorator commit | requires 0.B | enables 0.F |
| 0.F | disable shadow runner | requires 0.E | required for 0.H |
| 0.G | disable CI lane | none (Chronicler unchanged) | required for Phase 4 |
| 1 | revert YAML changes | requires 0.H | required for Phase 2 |
| 2 | remove decorators | requires Phase 1 | required for Phase 3 |
| 3 | restore digest_profiles from git tag | requires Phase 2 | required for Phase 4 |
| 4 | per-gate feature flag (gate 2 has 30d type-escape) | requires Phase 3 | required for Phase 5 |
| 5 | full cutover rollback per CUTOVER_RUNBOOK | requires Phase 4 + telemetry data | post-cutover |

## §10 Ownership map

| Role | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|---|
| planner / critic | A baseline measurement | — | — | mid-drift check | — | mid-drift check |
| architect | B catalog | — | — | — | — | — |
| implementer | D, E, F, G | all | all | all | all | telemetry + cutover |
| operator | C, H | INV gap policy | — | — | — | cutover sign-off + 20h replay sign-off |
