# Team-Lead Handoff (post-P7A, 2026-04-18)

**Written**: 2026-04-18 post Phase 5 + 6 + 7A complete (Gen-Verifier mode). Team infrastructure RETIRED — `TeamDelete` applied. New composition: team-lead (Opus, main context) + critic-beth (Opus, persistent via disk-durable learnings + fresh spawn per phase) + ephemeral subagents (Sonnet/Haiku per task shape). Supersedes all earlier handoffs.

## IMMEDIATE NEXT ACTIONS (post-compact, in order)

1. Read `~/.claude/agent-team-methodology.md` — operating manual. §"Critic role" (L0.0 peer-not-suspect, 6-tier hypothesis ordering).
2. Read `~/.claude/CLAUDE.md` — global Fitz methodology, Four Constraints, Code Provenance.
3. Read THIS file IN FULL.
4. Read `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8 + `zeus_current_architecture.md` §13-§22.
5. Read the 10 learnings docs (5 from `zeus-dual-track` retirement, 5 from `zeus-dual-upgrade-v3` pre-compact) at `phase5_evidence/phase5b_to_phase5c_*.md` + `phase5_evidence/phase5_to_phase6_*.md` — durable multi-phase mental model.
6. Read critic verdict docs: `phase5_evidence/critic_alice_5B_verdict.md` + `critic_beth_phase5fix_wide_review.md` + `critic_beth_phase5c_wide_review_final.md` — structural antibody history.
7. `git log --oneline -6` — confirm Phase 5 top 5 commits.
8. Check `~/.claude/teams/zeus-dual-upgrade-v3/` — team should exist (OMC session-end patch keeps it alive). If missing, re-spawn per § "Team bootstrap (if needed)".
9. Resume team with a brief `phase6 open` SendMessage if existing, else spawn fresh.

## Branch + commit state

Branch: `data-improve`. Top of `origin/data-improve` = `e3a4700`. All pushed.

```
e3a4700 docs(phase6): microplan — Strategy A internal M1-M4 milestones
        ^ ACTUAL CONTENT: full Phase 6 implementation (Day0 split + DT#6 + B055
          absorption). Commit-message mislabeling via team-lead git coordination
          error; content reviewed + PASS by critic-beth.
          User-deferred commit-msg amend (force-push decision).
df1cc71 docs(phase6): contract — Day0 split + two-file co-landing
c001dda docs(operating-contract): P5 structural learnings → Phase 6 protocol
5e5fbf6 docs(phase5-close): handoff text consistency
ecf50bd docs(phase5-close): rebuild_v2 spec kwarg + R-AZ un-xfail + learnings
59e271c fix(phase5C): remove SQL metric filter from legacy forecasts query
821959e Phase 5C: replay MetricIdentity + Gate D core antibody + B093 half-1
3f42842 fix(phase5B-pack): 7 cross-team findings + R-AP..R-AU antibodies
c327872 Phase 5B: low historical lane + ingest contract gate + B078 absorbed
977d9ae Phase 5A: truth-authority spine + MetricIdentity view layer
```

**Phase 5 COMPLETE.** Gate D PASSED via R-AZ-3 structural antibody.
**Phase 6 COMPLETE** at `413d5e0` (impl + ITERATE fix). Closure journey:
- `e3a4700`: full Phase 6 implementation (mislabeled "docs(phase6): microplan" via team-lead git-index coordination error — user-ruled accept cosmetic mislabeling, no force-push amend)
- `413d5e0`: ITERATE MAJOR-1 fix — Day0Signal class-level TypeError re-guard + R2 antibody test upgrade to dual-assertion

Silent-corruption category eliminated at TWO layers:
1. `RemainingMemberExtrema` dataclass (`__post_init__` raises on both-None → MAX/MIN alias unconstructable)
2. `Day0Signal.__init__` class guard (`TypeError` on LOW → direct construction with wrong metric refused)

Defense-in-depth: router seam (Day0Router always dispatches LOW→Day0LowNowcastSignal) + class seam (Day0Signal refuses LOW directly). Fitz P4 category-impossibility preserved at both layers.

critic-beth authoritative verdict at `phase5_evidence/critic_beth_phase6_wide_review.md` (includes ITERATE finding + re-verify PASS addendum). Superseded initial PASS at `phase6_evidence/critic_beth_phase6_wide_review.md` preserved for audit trail.

## Phase order post-P7A

1. ~~**Phase 6**~~ **COMPLETE** at `413d5e0`. Day0 split delivered + critic PASS. See "Phase 6 closure" below.
2. ~~**Phase 7A**~~ **COMPLETE** at `c496c36` + `a872e50`. Metric-aware rebuild cutover + delete_slice metric scoping + CRITICAL-1 read-side fix + MAJOR-1 schema DEFAULT restoration + MAJOR-2 backfill contract gate. See "Phase 7A closure" below.
3. **Phase 7B** ← NEXT. Naming hygiene:
   - remove `remaining_member_maxes_for_day0` backward-compat alias (P6 forward-log)
   - `_tigge_common.py` helper extraction (15 safe mechanical helpers)
   - `architecture/script_manifest.yaml` registration for 5 scripts (incl. new `backfill_tigge_snapshot_p_raw_v2.py` from P7A)
   - Replace `test_R_AZ_2_low_rebuild_writes_only_low_rows` mirror test with real end-to-end LOW fixture (critic's MAJOR-3 from P7A)
   - Extract `CalibrationMetricSpec` + `METRIC_SPECS` to `src/calibration/metric_specs.py` (critic's MINOR-2)
   - Document or drop `contract_version` / `boundary_min_value` schema columns (critic's MINOR-1)
4. **Phase 8** — low shadow mode (`run_replay` metric threading, low-track evaluator produces shadow probability). **ADDED SCOPE** (critic's P6 forward-log): `cycle_runner.py:180-181` DT#6 rewiring — currently `raise RuntimeError` on `portfolio_loader_degraded=True`; must route through `riskguard.tick_with_portfolio` instead. Mechanism exists; routing missing. **ADDED SCOPE** (P7A deferral): B093 half-2 replay migration to `historical_forecasts_v2` — requires Zero-Data Golden Window lift + v2 table population.
5. **Phase 9** — low limited activation (Gate F) + risk-critical DT#2/DT#5/DT#7. **ADDED SCOPE** (critic's P6 forward-log): `Day0LowNowcastSignal.p_vector` proper implementation before Gate F (current impl has lazy-construction delegating to HIGH — acceptable until activation, not acceptable for live low).

## Phase 7A closure

**Commits**: `a872e50` (impl) + `c496c36` (ITERATE fix).

**Delivered**:
- `scripts/rebuild_calibration_pairs_v2.py`: METRIC_SPECS iteration via new `rebuild_all_v2` driver; `_delete_canonical_v2_slice` + `_collect_pre_delete_count` metric-scoped; `_process_snapshot_v2` L298 write-time `metric_identity=spec.identity` (not hardcoded HIGH); `_fetch_verified_observation` read-side metric dispatch (`observed_value` alias); outer SAVEPOINT atomicity
- `scripts/refit_platt_v2.py`: `refit_all_v2` driver, METRIC_SPECS iteration, explicit `metric_identity` required
- `scripts/backfill_tigge_snapshot_p_raw_v2.py` NEW (351 LOC): metric-aware p_raw_json backfill, `assert_data_version_allowed` contract gate, dry-run safety pattern
- `src/state/schema/v2_schema.py`: 3 new columns (contract_version, boundary_min_value, unit); cross-pairing NOT NULL category-impossibility restored on 4 columns (observation_field / physical_quantity / fetch_time / model_version)
- `tests/test_phase7a_metric_cutover.py` NEW: R-BH..R-BO (17 tests) — 3 bug-class + 5 antibodies + 3 iteration antibodies

**Acceptance delivered** (user's master-plan criteria):
- bucket key / query / unique key 都带 metric ✓ (write + read + delete + count)
- high / low 可以同城同日共存 ✓ (per-metric scoping all paths)
- bin lookup 永不跨 metric union ✓ (category-impossibility at SQL seam + Python seam + function signatures)

**Structural antibodies installed**:
- `_fetch_verified_observation` column dispatch (CRITICAL-1 at read seam)
- Schema NOT-NULL on cross-pairing columns (MAJOR-1 at SQL seam)
- `assert_data_version_allowed` gate in backfill (MAJOR-2, belt-and-suspenders pattern inherited)
- R-BM: `_fetch_verified_observation(spec=LOW)` end-to-end SQL dispatch proven
- R-BN: schema INSERT without required columns → IntegrityError
- R-BO: backfill quarantined data_version → DataVersionQuarantinedError

**Critic-beth durable memory** (Gen-Verifier insight):
- P3.1 methodology extended with forward-facing vocabulary: `_requires_explicit_|_must_specify_|_no_default_` — caught new-contract antibodies beyond just stale-guard antibodies
- "Two-seam principle" learned: when fixing a write-side bug, ALWAYS audit the symmetric read-side. L0.0 self-correction surfaced CRITICAL-1 read-side only on second look
- Mirror-test detection heuristic: `try/except: pass` + positive assertion = structurally accidental green

**Regression**: 125 failed / 1805 passed / 90 skipped (+6 passed vs pre-P7A 125/1799 baseline; zero new failures).

**Forward-log (to P7B)**:
- MAJOR-3 (pre-existing from P5C): `test_R_AZ_2_low_rebuild_writes_only_low_rows` mirror test
- MINOR-1: contract_version / boundary_min_value schema columns undocumented
- MINOR-2: CalibrationMetricSpec + METRIC_SPECS should extract to `src/calibration/metric_specs.py`
- P6 carryover: remaining_member_maxes_for_day0 alias removal; _tigge_common.py extraction; script_manifest.yaml 5 scripts

## Phase 6 closure

**Commit**: `e3a4700` (misnamed "docs(phase6): microplan" — content is full impl; amend pending user force-push ruling).

**Delivered**:
- 3 new signal classes: `Day0HighSignal`, `Day0LowNowcastSignal`, `Day0Router`
- `RemainingMemberExtrema` dataclass with `for_metric` constructor + both-None guard
- `day0_window.py`: renamed `remaining_member_maxes_for_day0` → `remaining_member_extrema_for_day0` (legacy alias kept for backward-compat, removal is P7 chore)
- `evaluator.py:784` + `monitor_refresh.py:294` callsites migrated to `Day0Router.route(Day0SignalInputs(...))`
- `day0_signal.py:85-91` LOW NotImplementedError guard REMOVED
- `riskguard.py`: `tick_with_portfolio` DT#6 + B055 absorption path
- `portfolio.py`: existing degraded-path coverage confirmed correct (no new code needed)
- `tests/test_phase6_day0_split.py`: 19 tests, R-BA..R-BG, all GREEN

**Acceptance**:
- R-BA..R-BG: 19/19 GREEN
- Full regression: 138 failed / 1801 passed (flat vs P5 baseline 137/1783; net +18 passed via dead-guard unblock, no new failures)
- `grep NotImplementedError src/signal/day0_signal.py` → zero hits
- AST walk on `day0_low_nowcast_signal.py` → only `__future__` + `numpy` imports (R-BE clean)
- critic-beth wide-review PASS

**Structural antibodies installed**:
- `RemainingMemberExtrema.__post_init__` — both-None raises → MAX/MIN alias unconstructable (Fitz P4 category-impossibility)
- `Day0Router.route` — causality-guard at construction; LOW + `N/A_CAUSAL_*` routes through nowcast, not historical Platt
- `Day0LowNowcastSignal` — does not import `day0_high_signal` (R-BE AST invariant)
- `riskguard.tick_with_portfolio` — DT#6 single-degraded-state transition (not two paths for authority-loss + B055-staleness)

**Coordination-error learning**:
Team-lead accidentally `git add` + `git commit` captured exec-kai's parallel-staged WIP into a commit intended only for the microplan doc. Root cause: git index is shared mutable state between team-lead and exec; team-lead assumed private. Lesson logged for operating contract amendment:

> **P1.1 (to be added)**: Before `git add`, team-lead runs `git status --short`. Any unexpected staged or modified files in index are isolated via `git stash -u` or `git reset HEAD -- <file>` before intentional stage. Shared git index requires explicit coordination, not assumption.

> **P2.1 (to be added)**: Exec "ready for commit" announcement precedes any `git add`. Team-lead owns the commit boundary; exec hands over a staged-file list + diff in SendMessage, team-lead verifies then stages + commits. No parallel staging.

## Phase 6 scope

### Primary deliverable: Day0Signal split

- **New module** `src/signal/day0_high_signal.py` — extract `Day0Signal` (current monolith at `src/signal/day0_signal.py:L80-91` raises `NotImplementedError` for LOW metric).
- **New module** `src/signal/day0_low_nowcast_signal.py` — low-track nowcast path built on `low_so_far`, `current_temp`, `hours_remaining`, remaining forecast hours. NOT a historical Platt lookup.
- **New module** `src/signal/day0_router.py` — routes by `(metric, causality_status)`:
  - HIGH → `Day0HighSignal`
  - LOW + causal OK → `Day0LowNowcastSignal` (historical path forbidden)
  - LOW + causal `N/A_CAUSAL_DAY_ALREADY_STARTED` → nowcast path (not historical Platt)

### CRITICAL co-landing imperative (TWO files, per scout-gary)

**BOTH `src/engine/evaluator.py:825` AND `src/monitor_refresh.py:306` MUST be fixed in the same commit that removes `Day0Signal.__init__` NotImplementedError guard for LOW.** Current state: both sites pass MAX array as MIN input (`remaining_member_extrema` → `member_mins_remaining` per exec-juan's detail). Today DEAD because guard raises NotImplementedError for LOW at `day0_signal.py:L85-92`. When guard removes, silent corruption lights up. Decoupling these fixes (or fixing only one of the two) is the primary structural risk for Phase 6. scout-finn's original 5B learnings only flagged evaluator; scout-gary's P5→P6 learnings added monitor_refresh. Both must be in the co-landing commit.

### DT#6 graceful-degradation law

- `load_portfolio()` authority-loss path must NOT kill entire cycle with RuntimeError.
- Legal behavior: disable new-entry paths; keep monitor / exit / reconciliation running read-only; surface degraded state to operator.
- Integrates with `PortfolioState.authority` field landed 5A (`977d9ae`).
- Absorbs B055 (riskguard trailing-loss 2h staleness).

### Out-of-scope for Phase 6

- Day0 low-track live trading activation — Phase 9 (Gate F).
- `run_replay` public-entry metric threading — Phase 8 (LOW shadow).
- `rebuild_v2` full METRIC_SPECS iteration + R-AZ-1/2 un-xfail — Phase 7.
- `_tigge_common.py` extraction — Phase 7.

## Team `zeus-dual-upgrade-v3` — retained through P5

| Role | Name | Model | Status |
|---|---|---|---|
| critic | critic-beth | opus | retained; L0.0 discipline proven across fix-pack + 5C |
| scout | scout-gary | sonnet | retained; landing-zone + latent-issue discipline proven |
| testeng | testeng-hank | sonnet | retained; one scope-override process-note logged, no discipline finding |
| executor | exec-ida | sonnet | retained; fix-pack owner + 5C CRITICAL-1 cleanup; one out-of-scope Python 3.14 fix with note |
| executor | exec-juan | sonnet | retained; 5C replay owner; schema validation discipline exemplary |

### Process notes from P5 (for Phase 6 brief reference)

- **Scope-ruling authority** = team-lead only. testeng + critic can recommend/push back via a2a; executors must escalate conflicting rulings, not act on peer a2a that contradicts team-lead.
- **Out-of-scope incidental fixes**: executor should ASK team-lead first (Option A) OR proceed with LOUD flag requesting ruling (Option B). exec-ida's `main.py:330` fix was Option B quality (transparent after) but should have been Option A (ask first).
- **Multi-agent disk settling delay**: default hypothesis when disk disagrees with teammate report is concurrent-write or memory lag, NOT discipline breach. Verified 4+ times across P5.

## Durable structural antibodies installed (P5)

- `PortfolioState.authority: Literal[...]` (5A) — fail-closed default.
- `ModeMismatchError` + `mode=None` strict rejection (5A + fix-pack).
- `MetricIdentity` view layer row + top-level emission (5A).
- `validate_snapshot_contract` inline 3-law gate at ingest writer path (5B).
- `CalibrationMetricSpec` + `METRIC_SPECS` pattern for metric-aware rebuild/refit (5B).
- `assert_data_version_allowed` positive-allowlist (fix-pack, converting from quarantine-block).
- `_process_snapshot_v2(spec=...)` per-spec cross-check (fix-pack).
- `_LOW_LANE_FILES` frozenset explicit check vs substring (fix-pack).
- `observation_client._require_wu_api_key()` lazy callsite guard (fix-pack) — unblocked SystemExit-poisoned test collection.
- `R-AM.4` AST-walk assertion: ingest MUST NOT import from `scripts.scan_*` (5B scanner isolation).
- Replay typed-status fields (5C B093 half-1).
- `_decision_ref_cache` metric-aware key tuple (5C).
- `save_platt_model_v2::model_key` per-metric scope (R-AZ-3 GREEN, Gate D core).

## R-letter namespace ledger

Locked:
- R-A..R-P: Phases 1-4
- R-Q..R-U: Phase 4.5
- R-AA: Phase 4.6
- R-AB..R-AE: Phase 5A (locked at `977d9ae`)
- R-AF..R-AO: Phase 5B (locked at `c327872`)
- R-AP..R-AU: Phase 5B-fix-pack (locked at `3f42842`)
- R-AV..R-AZ: Phase 5C (locked at `821959e` / `59e271c` / `ecf50bd`). **R-AZ-1/2 GREEN on merit** via `rebuild_v2(spec=...)` landed in `ecf50bd`; xfail markers removed. No Phase 7 un-xfail work remains.

Reserved:
- R-V..R-Z: future Phase 6/7/9 drafts per emma final_dump rename (pre-P5 ruling)

Available for Phase 6:
- R-BA onwards (new range), plus R-V..R-Z reserved slots if those plans don't re-emerge.

## Regression state post-P5

- 5A tests: 21/21 GREEN
- 5B tests: 41/41 GREEN
- Fix-pack tests: 14/14 GREEN
- 5C tests: 12/12 GREEN (R-AZ-1/2 un-xfailed after exec-ida's post-5C `rebuild_v2(spec=...)` landing)
- **Total P5-specific: 88/88 GREEN** (Gate D fully covered)
- Full regression: 137 failed / 1749 passed / 94 skipped (post-59e271c). Note: 137 failed baseline post-unblock; majority are pre-existing hidden failures exposed by R-AT lazy-import.

## Forward-log (Phase 6+)

1. `evaluator.py:825` MAX→MIN fix CO-LANDING with Day0Signal guard removal (Phase 6). Silent-corruption risk if decoupled.
2. `rebuild_v2` spec param + METRIC_SPECS iteration (Phase 7) → un-xfail R-AZ-1/2.
3. `run_replay` public-entry (L1933 + L2002) metric threading (Phase 8 / shadow activation).
4. Replay migration `forecasts` → `historical_forecasts_v2` (Phase 7 B093 half-2) → enables SQL metric filter.
5. `_tigge_common.py` shared-helper extraction (Phase 7) → 12 duplicated helpers across mx2t6 + mn2t6 extractors.
6. INV-21 / INV-22 zero coverage (Phase 9 risk-layer packet): DT#5 Kelly executable-price + DT#3 FDR family canonicalization.
7. Phase 2-4 test-file provenance header retrofit (chore commit).
8. 2 hardcoded absolute paths in Zeus core scripts (chore commit).
9. Triage pass on 137 pre-existing test failures (separate, post Phase 6).

## Zero-Data Golden Window (STANDING)

Still active. v2 tables zero rows. No real ingest/batch extraction. Smoke ≤1 GRIB → `/tmp/`. Structural fixes free. User lifts when Phase 8 shadow opens.

## Paper mode retired (STANDING antibody)

`ACTIVE_MODES=("live",)` in `src/config.py`. Antibody msg in `mode_state_path`. **DO NOT re-add paper mode.**

## Phase 6 opening brief sketch (for team upon re-engagement)

The fresh engagement of the retained team for Phase 6 uses short briefs pointing at:
- This handoff doc §"Phase 6 scope"
- 10 learnings docs (5 from zeus-dual-track retirement, 5 from zeus-dual-upgrade-v3 pre-compact)
- `docs/authority/zeus_dual_track_architecture.md` §5 (Day0 causality law) + §6 (DT#1-7)
- Current `src/signal/day0_signal.py` state
- `evaluator.py:825` co-landing imperative

Role assignments (proposed, team-lead ruling on re-engagement):
- exec-ida: DT#6 graceful-degradation + PortfolioState.authority integration (extends her 5A seam ownership)
- exec-juan: Day0 split + evaluator.py:825 co-landing (extends his 5C replay/runtime ownership)
- testeng-hank: R-letter drafting for Day0 split + DT#6 + B055
- critic-beth: wide review + L0.0 discipline
- scout-gary: Day0 landing-zone scan pre-implementation

## Team bootstrap (if team missing post-compact)

If `~/.claude/teams/zeus-dual-upgrade-v3/` is gone (OMC patch failed):
- Team name: retain `zeus-dual-upgrade-v3` (the persistent name covers P5→P9)
- Spawn same 5 roles with names from retained team (continuity) OR fresh names (full restart)
- Each brief mandates reading: methodology + root AGENTS + dual-track architecture + THIS handoff + 10 learnings docs + relevant critic verdict docs
- Gate start-of-work on explicit "reads complete" ack

## Standing do-nots (post-compact reminders)

- Don't trust teammate claims without single-quick disk-verify on critical path.
- Don't over-verify — single check, not spiral. Multi-agent disk settling is real.
- Don't re-add paper mode.
- Don't push full-batch extraction without user approval.
- Don't decouple `evaluator.py:825` fix from Phase 6 guard removal.
- Don't bundle Phase 6 with Phase 7 scope (keep commits atomic).
- Don't let testeng override team-lead scope rulings via a2a.
- Don't spend >2x the expected token budget on a single sub-phase without escalating to user.

## OMC session-end hook

Still patched. `OMC_ENABLE_SESSION_END=1` restores original. Re-apply if `omc update` runs.

## Status files on disk

- This file (authoritative post-P5 handoff).
- P5 evidence: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/` — 3 onboarding docs + 3 wide-review docs + 1 verdict doc + 1 landing-zone scan + 1 DT-v2 triage + 10 learnings docs + Phase 5A/5B/5C critic verdicts.
- Coordination handoff: `docs/to-do-list/zeus_dt_coordination_handoff.md` (B069/B073/B077/B078 ✅; B093 ✅ half-1 5C, half-2 Phase 7; B091 ✅; B055 ⏳ Phase 6; B099 ⏳ DT#1 architect packet; B063/B070/B071/B100 ⏳ Section A bugs).
- Methodology (global): `~/.claude/agent-team-methodology.md`.
- Global rules: `~/.claude/CLAUDE.md`.

Phase 5 fully closed. Team retained. Compact-ready. Phase 6 opens post-compact with same team.
