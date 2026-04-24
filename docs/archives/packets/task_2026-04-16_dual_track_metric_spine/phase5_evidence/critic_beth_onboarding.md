# critic-beth — Phase 5B-fix-pack / 5C Onboarding

**Written**: 2026-04-17. Role: persistent adversarial critic, `zeus-phase5fix-5c` team, successor to retired critic-alice (retired at 5B commit `c327872`). Reports to team-lead.

## 1. Reads completed (disk-verified, fresh-session)

### Tier 1 — operating manual + law
1. `~/.claude/agent-team-methodology.md` — context is investment not budget; phantom-work disk-verify; wide-critic L0-L5+WIDEN; §"Critic role — critique TASK not TEAMMATE" (L0.0 peer-not-suspect, 5-tier hypothesis ordering).
2. `~/.claude/CLAUDE.md` — Fitz methodology (K << N structural decisions, relationship tests), Four Constraints, Code Provenance (LEGACY until audited; verdicts CURRENT_REUSABLE / STALE_REWRITE / DEAD_DELETE / QUARANTINED).
3. `AGENTS.md` (root) — dual-track system; §"Forbidden Moves" (paper-mode retired, JSON-before-commit banned per DT#1, bare entry_price banned per INV-21/DT#5); §"Function Naming" points at naming_conventions.yaml.
4. `architecture/naming_conventions.yaml` — Lifecycle/Purpose/Reuse header triad on scripts+tests; allowed-prefix list for durable scripts; packet-ephemeral `task_YYYY-MM-DD_*.py` rule.
5. `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8 — MetricIdentity triad (temperature_metric, physical_quantity, observation_field, data_version); low-Day0 is nowcast not mirror-of-high; DT#1-#7 binding law; forbidden moves (no daily-low before Gate F; no high/low mix in Platt/bin-lookup/calibration).

### Tier 2 — current state
6. `team_lead_handoff.md` — fix-pack scope (4 CRITICAL + 4 MAJOR, ~500 LOC cap); 5C after; co-landing imperative evaluator.py:825 ↔ Phase 6 guard removal; zero-data golden window active; paper retired antibody.
7. `zeus_dt_coordination_handoff.md` — 12-bug RED queue; Section A (pre-5 unlock), B (Phase-5 unlock; B069/B073/B077/B078 RESOLVED, B093 bifurcated), C (DT#1/DT#6 gated).

### Tier 3 — retired team learnings
8. `phase5b_to_phase5c_critic_alice_learnings.md` — L0.0 hypothesis ordering worked (zero false discipline findings in 5B); proposes 6th tier "deferred team-lead ruling I don't see" above benign-mistake; triad invariant (data_version+metric+physical_quantity); contract-module writer-path wiring must be checked AT creation time; `or "fallback"`/`setdefault` = vestigial defense after hard constraint lands.
9. `phase5b_to_phase5c_scout_finn_learnings.md` — `evaluator.py:825` MAX-as-MIN-input live-but-guarded; `wu_daily_collector.py` DEAD_DELETE; `_extract_causality_status` dead; `p_raw_vector_from_maxes` / `remaining_member_maxes_for_day0` misleading names (both tracks flow through); hardcoded abs paths in generate_monthly_bounds.py:37 + heartbeat_dispatcher.py:19.
10. `phase5b_to_phase5c_testeng_grace_learnings.md` — R-AG importability-only = polarity-swap footgun (R-AP reserved); `observation_client.py:87` module-level SystemExit poisons transitive importers (breaks `test_phase6_causality_status.py`); INV-21/INV-22 zero coverage; Phase 2-4 tests lack provenance headers; `test_cross_module_invariants.py` vacuously-true in zero-data window.
11. `phase5b_to_phase5c_exec_dan_learnings.md` — 200 LOC duplicated between mx2t6/mn2t6 extractors (`_tigge_common.py` backlog); quarantined members' `value_native_unit=inner_min` not None (silent trap); `_compute_required_max_step` uses fixed-offset timezone not target-date ZoneInfo (DST 1h drift); MEMBER_COUNT=51 unvalidated; `codes_grib_find_nearest` exception-swallowing silent skip.
12. `phase5b_to_phase5c_exec_emma_learnings.md` — `mode=None` silently bypasses ModeMismatchError (truth_files.py:135); `rebuild_calibration_pairs_v2._build_calibration_pair` data_version from row not spec.allowed; `ensemble_snapshots_v2.temperature_metric DEFAULT 'high'` silent-default hazard; `setdefault("causality",...)` bridges legacy high but stamps OK for any future missing-causality payload; bucket-key lacks physical_quantity secondary discriminator.

### Tier 4 — prior critic products
13. `critic_alice_5B_verdict.md` — PASS verdict, 5B-follow-up backlog (R-AP, `_tigge_common.py`, dead-code audit, MINOR-NEW-1/2 setdefault asymmetry).
14. `critic_alice_phase5b_wide_review.md` — initial ITERATE: CRITICAL-1 (contract off-path) + MAJOR-1 (R-AG importability-only) + MAJOR-2 (`stats.refused` regression). Template for my surface layout.
15. `critic_alice_phase5b_onboarding.md` — onboarding template (authority chain, L0-L5 + Phase-5B additions, top-3 antipatterns, one scope Q). I follow this shape.
16. Phase 4.5 audits (`legacy_code_audit_phase4_5.md` style) — STALE_REWRITE precedent for 51-source `tigge_local_calendar_day_common.py` (Kelvin silent-default + hardcoded abs path); fixture-bypass trap (4B MAJOR-1, 4C+4D MAJOR-2) where R-tests exercised helper not real entry point. Head commit verified `c327872` via `git log --oneline -5`.

## 2. L0-L5 + WIDEN operating posture (L0.0 as item 0)

- **L0.0 — Peer, not suspect.** 6-tier hypothesis ordering (accepting critic-alice's 5C refinement): `concurrent-write` → `memory/report-state lag` → `shell/tool artifact` → `deferred team-lead ruling I don't see` → `benign mistake` → `discipline breach` (LAST, triple-verified + team-lead concurrence). Language: "the diff shows", "the disk reveals". Every "grep reveals X" = fresh bash grep right before writing, raw output pasted inline. Discipline findings never go in a verdict — they go to team-lead as a flagged prefix-missing observation, team-lead owns escalation.
- **L0** — authority chain re-loaded post-subagent-start (above).
- **L1** — INV-##/FM-## in scoped AGENTS for every directory touched (`scripts/`, `src/contracts/`, `src/state/`, `src/engine/`, `src/data/`, `src/signal/`, `tests/`).
- **L2** — Forbidden Moves sweep: paper-mode resurrection; `setdefault` on fields where caller-arg must be authority (MINOR-NEW-1 shape); polarity-swap without semantic rethink; orphan helper / parallel-source (51-source STALE); JSON-write-before-commit (DT#1); bare entry_price at Kelly seam (INV-21); high/low mix in any Platt/bin-lookup/calibration.
- **L3** — silent fallbacks / default-value traps (`DEFAULT 'high'`, `or "high"`, `setdefault`, `mode=None` skip); unit semantics (Kelvin at GRIB level).
- **L4** — source authority at every seam: contract module wired INTO the writer, not co-resident; `decision.*` wins over `payload.*`; triad (data_version + temperature_metric + physical_quantity) cross-checked on every v2 write/read.
- **L5** — phase boundary leakage: no Phase 6/7/9 concern landing early; no 5A/5B contract regressed; co-landing imperatives (evaluator.py:825 ↔ Phase-6 guard; DST fix before real-data batch).
- **WIDE** — "what did I see that wasn't on my checklist?" Every sub-phase boundary. This is where gold lives (Phase 2 critic caught 2 CRITICALs off-list; alice caught CRITICAL-1 contract-off-path the same way).

## 3. Top-3 antipatterns I'll hunt in fix-pack wide review

1. **R-AP behavioral vs importability (fix-pack #1 + testeng-hank).** Assert R-AP tests actually call `classify_boundary_low(inner=..., boundary=...)` with at least (a) `inner_min > boundary_min` → ambiguous True, (b) inner-only → ambiguous False, (c) `inner_min is None + boundary_min` present edge. If R-AP stops at "importable" or fixture-constructs the return shape, it's the same trap as R-AG — polarity flip stays GREEN.
2. **`setdefault` regression after fix-pack #5 lands.** Fix-pack #5 asserts `data_version` against spec. If the fix uses `setdefault` or `row.get(..., spec.allowed_data_version)` it RE-OPENS the 5B MINOR-NEW-1 authority asymmetry by letting stale-row self-report win. Correct shape: unconditional assertion that `row["data_version"] == spec.allowed_data_version` else reject. Same pattern-class governs fix-pack #1 `mode=None` fix — explicit rejection on None, not a `setdefault("mode","live")` backdoor.
3. **DST step-horizon co-coverage with `_compute_required_max_step` fix (fix-pack #3).** exec-dan flagged the fixed-offset vs target-date-ZoneInfo bug. Matching R-letter test MUST synthesize a city whose target_date straddles a DST transition (e.g. London 2025-03-30 / Chicago 2025-03-09) and assert the computed step horizon uses the TARGET-DATE offset, not the issue-time offset. If the test uses a non-DST city (Tokyo/Seoul/Shanghai) it's vacuously correct and silent-passes any regression. This is the canonical Fitz Constraint #4 (data provenance) case: "both agents knew DST, neither questioned the provenance chain."

## 4. Scope question for team-lead

None. Fix-pack scope (8 items, CRITICAL×4 + MAJOR×4, ~500 LOC cap) is well-specified; 5C scope (replay MetricIdentity half-1 + Gate D + B093 half-1) is deferred cleanly; co-landing imperatives are explicit. I'll raise scope questions only if a specific ITERATE finding bumps into a Phase 6/7 law boundary.

---

*Authored*: critic-beth (opus, persistent, fresh onboarding).
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, `git log --oneline -5` confirms `c327872` head; 16 cited files read fresh this session.
