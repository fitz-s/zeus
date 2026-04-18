# Team-Lead Handoff (post-5B commit, pre-compact, 2026-04-17)

**Written**: 2026-04-17 after Phase 5B commit `c327872` pushed + team `zeus-dual-track` retired with full learnings extracted. Supersedes all earlier handoffs.

## IMMEDIATE NEXT ACTIONS (post-compact, in order)

1. Read `~/.claude/agent-team-methodology.md` — operating manual. Pay attention to §"Critic role" (L0.0 peer-not-suspect) and §"Value extraction".
2. Read `~/.claude/CLAUDE.md` § "Code Provenance" + § "Fitz's Core Methodology" + § "Four Constraints of Delegated Intelligence".
3. Read THIS file IN FULL.
4. Read `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8.
5. Read the 5 learnings docs at `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/phase5b_to_phase5c_*_learnings.md` — these are the multi-phase mental model of the retired team, your only bridge to 5A+5B context without a live teammate.
6. Read `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/critic_alice_5B_verdict.md` — final PASS verdict + 6 forward-backlog items.
7. `git log --oneline -5` to confirm state: `c327872 Phase 5B` should be top.
8. Check `~/.claude/teams/` — team `zeus-dual-track` should be DELETED (retired). If still present, user didn't trigger retirement yet; ask.
9. Spawn FRESH team for Phase 5B-fix-pack (see § "Fresh team bootstrap" below).

## Branch + commit state

Branch: `data-improve`. Recent commits (top of `origin/data-improve` is `c327872`):

```
c327872 Phase 5B: low historical lane + ingest contract gate + B078 absorbed
977d9ae Phase 5A: truth-authority spine + MetricIdentity view layer
94cc1f9 fix(B063): rescue_events_v2 audit table with provenance authority
177ae8b fix(B091): forward decision_time to evaluator + explicit fabrication warnings
ef09dc3 docs(handoff): DT coordination handoff for 12 truly-RED bugs (Phase-5 split)
```

Both 5A + 5B pushed. No pending commits.

## Phase order (revised post-learnings)

1. **Phase 5B-fix-pack** ← NEW, fresh team's FIRST commit. Addresses 8 items from cross-team learnings (CRITICAL + MAJOR severity, fix-pack-appropriate scope). See § "Phase 5B-fix-pack scope" below.
2. **Phase 5C** ← after fix-pack. Replay MetricIdentity half-1 + Gate D test + B093 half-1.
3. **Phase 6** — Day0 split (`Day0HighSignal` / `Day0LowNowcastSignal`) + DT#6 graceful-degradation law + B055 absorption. CRITICAL co-landing hazard: `evaluator.py:825` MAX-array-passed-as-MIN MUST be fixed in the same commit that removes the `Day0Signal.__init__` guard for low metric.
4. **Phase 7** — metric-aware rebuild + model cutover. Migrate replay to `historical_forecasts_v2` (B093 half-2).
5. **Phase 8** — low shadow mode.
6. **Phase 9** — low limited activation (Gate F).

## Phase 5B-fix-pack scope (8 items, ~500 LOC target)

The fresh team's FIRST commit. Scope consolidated from the 5 learnings docs; omits items that are Phase 7+ or too big for a fix-pack.

### CRITICAL-severity (4 items)

1. **`mode=None` bypasses ModeMismatchError** (exec-emma finding). `src/state/truth_files.py::read_mode_truth_json` — explicit `None` should be rejected, not silently accepted. Fix: reject at entry + update R-AC regression test to cover.
2. **Quarantined members `value_native_unit` silent trap** (exec-dan finding). `scripts/extract_tigge_mn2t6_localday_min.py` — when `training_allowed=False`, member-level `value_native_unit` must be `None`, not inner-bucket min. Prevents downstream consumer who reads value-but-skips-training_allowed from seeing wrong data. Add assertion to extractor JSON + matching test.
3. **DST step-horizon 1h drift** (exec-dan finding). `scripts/extract_tigge_mn2t6_localday_min.py::_compute_required_max_step` uses point-in-time offset; must use target-date local offset. Add R-letter test with DST-boundary synthetic city.
4. **`observation_client.py:87` module-level SystemExit** (testeng-grace finding). Move the guard to callsite / lazy import so transitive importers don't crash on missing `WU_API_KEY`. This topology land mine is blocking proper regression testing — fixing it unblocks `test_phase6_causality_status.py` + many dev-env tests.

### MAJOR-severity (4 items)

5. **Rebuild `data_version` not asserted against spec** (exec-emma finding). `scripts/rebuild_calibration_pairs_v2.py` — assert snapshot `data_version` is in `{HIGH_LOCALDAY_MAX.data_version, LOW_LOCALDAY_MIN.data_version}` before processing. Reject rows with stale/unknown data_version.
6. **Contract rejection log level WARNING → ERROR** (exec-emma finding). `scripts/ingest_grib_to_snapshots.py::ingest_json_file` — `decision.accepted=False` path should log at ERROR level for operational visibility. One-line change.
7. **`_extract_causality_status` dead code delete** (triple-confirmed: scout + exec-emma + critic). `scripts/ingest_grib_to_snapshots.py:107` — function defined but never called post-contract-wiring. Delete.
8. **`wu_daily_collector.py` DEAD_DELETE** (scout finding). `main.py:73` lazy-guard still makes it importable. Delete module + update import site.

### OUT of fix-pack scope (defer)

- `_tigge_common.py` extraction (12-helper refactor, ~300 LOC, cross-cuts 2 extractors) — separate chore commit after 5C.
- INV-21 / INV-22 zero coverage (architectural packet, not fix-pack scope) — Phase 9 risk-layer work.
- `evaluator.py:825` MAX→MIN silent corruption — dead-code today (guarded by NotImplementedError); **co-landing imperative with Phase 6 guard removal**, not fix-pack.
- Hardcoded absolute paths in Zeus core (2 sites) — separate env-var refactor.
- Phase 2-4 tests missing provenance headers — bulk retrofit commit.
- `test_cross_module_invariants.py` vacuously-true — needs structural fix + real data, post Phase 9.
- Naming drift (`p_raw_vector_from_maxes`) — Phase 7 naming pass.
- `setdefault` trust-boundary weakener (critic MINOR-NEW-1) — structural hardening in 5C/Phase 6.

## Phase 5C scope (after fix-pack)

- `src/engine/replay.py::_forecast_reference_for` — sentinel strings → typed status fields (B093 half-1):
  - `decision_reference_source: Literal["historical_decision","forecasts_table_synthetic"]`
  - `decision_time_status: Literal["OK","SYNTHETIC_MIDDAY","UNAVAILABLE"]`
  - `agreement: Literal["AGREE","DISAGREE","UNKNOWN"]`
- `_forecast_rows_for` SQL: add `AND temperature_metric = ?` filter (per scout's Phase 5 inventory §5C).
- `_forecast_reference_for` branching: metric-conditional value read (`forecast_high` vs `forecast_low`).
- `_decision_ref_cache` cache key MUST include `temperature_metric` (scout's learning doc flag).
- Gate D test: `tests/test_phase5_gate_d_low_purity.py` — asserts high + low Platt models don't share buckets; no cross-metric leakage in `calibration_pairs_v2`.
- Leave `historical_forecasts_v2` table migration to Phase 7 (B093 half-2).

## Fresh team bootstrap (post-compact)

### Team name
`zeus-phase5fix-5c` or similar. Fresh from `zeus-dual-track`.

### Team composition (5 roles, same structure as retired team)
- `critic-beth` (opus critic) — wide adversarial review at sub-phase boundaries.
- `scout-gary` (sonnet explore) — inventory + landing-zone scan.
- `testeng-hank` (sonnet test-engineer) — R-letter drafting (next namespace: R-AP onward).
- `exec-ida` (sonnet executor) — fix-pack owner; 5C co-owner.
- `exec-juan` (sonnet executor) — 5C co-owner; takes replay_metric + Gate D.

(Names are arbitrary; what matters is fresh identity + clean onboarding.)

### Mandatory reads for every fresh teammate

Put this in every brief, no exceptions:

1. `~/.claude/agent-team-methodology.md` — full.
2. `~/.claude/CLAUDE.md` (global Fitz methodology + Four Constraints + Code Provenance).
3. `/Users/leofitz/.openclaw/workspace-venus/zeus/AGENTS.md` — root law + §"Function Naming" + §"Forbidden Moves".
4. `architecture/naming_conventions.yaml` — file-header format, R-letter rules.
5. `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8.
6. THIS handoff file IN FULL.
7. **All 5 learnings docs** at `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/phase5b_to_phase5c_*_learnings.md` — they are the retired team's multi-phase mental model; read ALL of them, not just the one matching your role.
8. `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/critic_alice_5B_verdict.md` — PASS verdict + 6 forward-backlog items.
9. Role-specific reads (e.g. critic reads prior critic products; testeng reads R-letter ruling).

### Leadership notes (from 5B self-review, for your post-compact self)

- **testeng R-letter mapping**: in 5B, testeng-grace silently reshuffled R-letters (my R-AJ=B078 became her R-AJ=causality). Caught late. Fresh testeng brief: include explicit R-letter → test-class mapping; require testeng to confirm mapping BEFORE drafting; reject silent reshuffles.
- **Concurrent-write timing**: in 5B, executors took testeng's initial-RED broadcast as implicit green-light; HOLD messages arrived late. Fresh team brief: explicit "do not start implementation until [X confirmation event]". Gate execution on positive confirmation, not absence of stop.
- **peer-not-suspect L0.0**: already in methodology file. Fresh critic inherits it as background context, not as corrective patch. Reference the file; don't reprint the whole §.
- **Citation prefix**: universal rule, non-negotiable. Every Edit/Write status needs `[AUTHORIZED by: ...]` + `[DISK-VERIFIED: ...]`.
- **Disk is truth**: main thread always disk-verifies before accepting a teammate claim as fact.
- **Fix-pack first, 5C second**: DO NOT let fresh team try to bundle fix-pack + 5C in one commit. Critic wide-review quality drops past ~500 LOC.

## 5B-follow-up backlog not in fix-pack (for 5C+ owners)

From critic's final verdict + cross-team learnings:

1. **R-AP** (testeng-hank): behavioral tests for `classify_boundary_low`.
2. **`_tigge_common.py` extraction** (exec-dan's list): 12 shared helpers across mx2t6 + mn2t6 extractors. Post-5C or Phase 7.
3. **MINOR-NEW-1**: caller `metric` arg as contract authority (unconditional assignment, not `setdefault`) in `ingest_grib_to_snapshots.py::ingest_json_file`.
4. **MINOR-NEW-2**: causality `setdefault` gated on `metric.temperature_metric == "high"`.
5. **`scripts/scan_tigge_mn2t6_localday_coverage.py`**: diagnostic scanner per remediation §8. Scanner-isolation antibody R-AM.4 protects ingest.
6. **INV-21 / INV-22 coverage**: DT#5 + DT#3 machine-check gap. Phase 9 risk-layer packet.

## Critical co-landing imperatives (do not decouple)

- **`evaluator.py:825` MAX→MIN fix** MUST land in the same Phase 6 commit that removes `Day0Signal.__init__` low-metric NotImplementedError guard. Silent corruption risk if decoupled.
- **DST step-horizon fix** (fix-pack #3) MUST land before any real-data batch run. Zero-data window protects us today.

## Gate status post-5B-fix-pack

- Gate A, B, C open (Phases 2-4 closed).
- Phase 5A committed at `977d9ae`.
- Phase 5B committed at `c327872`.
- Phase 5B-fix-pack committed — 14/14 R-AP..R-AU GREEN; regression +12 passing vs pre-fix (R-AT observation_client lazy-import unblocked SystemExit-poisoned collection). True post-unblock baseline: 138 failed / 1716 passed. critic-beth PASS at `phase5_evidence/critic_beth_phase5fix_wide_review.md`.
- Phase 5C next (replay MetricIdentity half-1 + Gate D + B093 half-1).
- Phase 6 after 5C (Day0 split + DT#6 + B055) — co-landing imperative: `evaluator.py:825` MAX→MIN fix with guard removal.

## R-letter namespace ledger (locked + available)

- R-A..R-P: Phases 1-4 (locked).
- R-Q..R-U: Phase 4.5 (locked).
- R-AA: Phase 4.6 (locked).
- R-AB..R-AE: Phase 5A (locked at 977d9ae).
- R-AF..R-AO: Phase 5B (locked at c327872).
- **R-AP onward: available for fix-pack + 5C + later.**
- R-V..R-Z: reserved per emma final_dump (Phase 6/7/9).

Full ledger: `phase4_evidence/r_letter_namespace_ruling.md`.

## Zero-Data Golden Window (STANDING)

- v2 tables: zero rows.
- No real ingest/batch extraction until user lifts.
- Smoke tests ≤ 1 GRIB file, output `/tmp/`, never commit.
- Full batch requires: user approval + download complete + critic PASS.
- Structural fixes are free now (exploit this window).

## Paper mode retired (STANDING antibody)

`src/config.py::ACTIVE_MODES = ("live",)`. `mode_state_path` carries "paper retired" antibody msg. **DO NOT re-add paper mode.** Zeus is live-only.

## Retired team final disposition

- critic-alice (opus): retired. Learning doc: `phase5b_to_phase5c_critic_alice_learnings.md`. Contribution: L0.0 peer-not-suspect refinement; PASS verdict on 5B with 1 CRITICAL + 2 MAJOR caught + all addressed.
- scout-finn (sonnet): retired. Learning doc: `phase5b_to_phase5c_scout_finn_learnings.md`. Contribution: DT-v2 package triage + 6 latent-issue flags including `evaluator.py:825` co-landing hazard.
- testeng-grace (sonnet): retired. Learning doc: `phase5b_to_phase5c_testeng_grace_learnings.md`. Contribution: 41 R-letter tests (spec-anchored, no fixture-bypass). One memory-lag incident handled cleanly via peer-not-suspect.
- exec-dan (sonnet): retired. Learning doc: `phase5b_to_phase5c_exec_dan_learnings.md`. Contribution: mn2t6 extractor (~835 LOC) + ingest unblock + 5 sharp GRIB/DST flags. Probation withdrawn.
- exec-emma (sonnet): retired. Learning doc: `phase5b_to_phase5c_exec_emma_learnings.md`. Contribution: contract module + rebuild/refit refactor + B078 + 11 latent-issue flags.

All learnings filed in `phase5_evidence/`. Team config cleaned from `~/.claude/teams/zeus-dual-track/`.

## Standing do-nots (post-compact reminders)

- Do not trust any teammate's claim without disk-verify.
- Do not re-add paper mode (antibody in place).
- Do not push full-batch extraction without user approval.
- Do not bundle fix-pack + 5C in one commit.
- Do not decouple `evaluator.py:825` fix from Phase 6 guard removal.
- Do not skip the 5 learnings docs on fresh-team onboarding.
- Do not let fresh testeng silently reshuffle R-letter spec — confirm mapping first.

## OMC session-end hook

Still patched (`~/.claude/plugins/marketplaces/omc/scripts/session-end.mjs` early-returns by default). `OMC_ENABLE_SESSION_END=1` restores legacy behavior. Re-apply if `omc update` runs.

## Status files on disk

- This file (authoritative post-compact handoff).
- Phase 5 evidence: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/`.
- Coordination handoff (bug-fix agent): `docs/to-do-list/zeus_dt_coordination_handoff.md` (B069/B073/B077/B078 ✅ RESOLVED; B093 bifurcated).
- Methodology (global): `~/.claude/agent-team-methodology.md`.
- Global rules: `~/.claude/CLAUDE.md`.

Phase 5B is a clean milestone. Team retired per plan. Fresh team will pick up with fix-pack then 5C. Zero forward discipline concerns.
