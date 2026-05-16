# Migration Path: topology_doctor v_current → v_next

Created: 2026-05-15
Status: SPEC ONLY — no implementation code; implementation belongs in
        05_execution_packets/

Source files affected by migration:
- scripts/topology_doctor.py (main facade)
- scripts/topology_doctor_digest.py (admission logic)
- architecture/admission_severity.yaml (severity registry)
- architecture/test_topology.yaml (companion/coverage declarations)
- architecture/task_boot_profiles.yaml (boot profile definitions)

The migration is designed so each phase is independently reversible.
No phase requires a big-bang cutover.

---

## Phase 1 — Additive Parallel Route

**Goal**: Introduce v_next data structures alongside current structures.
No behavior changes. No test breakage. The current admission path runs
unchanged; v_next structures are built and populated but not consulted.

**Entry criteria**:
- UNIVERSAL_TOPOLOGY_DESIGN.md accepted (no Zeus-identifier leakage in
  universal section)
- ZEUS_BINDING_LAYER.md accepted
- This MIGRATION_PATH.md accepted
- At least one failing probe in 99_verification/REGRESSION_PROBE_SUITE.md
  demonstrating a current LEXICAL_PROFILE_MISS or UNION_SCOPE_EXPANSION
  that v_next would handle correctly

**What ships in Phase 1**:
1. New Zeus binding layer YAML file (from ZEUS_BINDING_LAYER.md spec)
   loaded by topology_doctor.py at startup but not consulted by
   admission logic
2. `AdmissionDecision` struct added to topology_doctor_digest.py as a
   dataclass; current code continues to return the legacy dict alongside
3. Coverage Map builder function added (reads ZEUS_BINDING_LAYER.md
   coverage_map section); validates all files in a change set against
   the coverage map, logs gaps to a new `coverage_gaps` field in output
   but does not gate on them
4. Hard Safety Kernel check function added; runs in parallel with current
   routing and logs HARD_STOP matches to `kernel_alerts` field in output;
   does not block on them yet
5. Typed-intent resolution already present (K3 from navigation_topology_v2);
   Phase 1 confirms it is wired to the new struct's `intent_class` field
6. Friction budget counter added to session state; populated on each
   admission call; surfaced in output but no SLO gate yet

**Tests to add in Phase 1**:
- Coverage map builder correctly maps all current `src/**` files to
  profiles or orphaned/hard_stop_paths (no gaps in current surfaces)
- Hard Safety Kernel correctly flags each HARD_STOP pattern from binding layer
- AdmissionDecision struct round-trips through admission call without
  changing any existing dict output fields

**Exit criteria**:
- All existing tests pass unchanged
- Coverage map builder runs without gaps on current repo
- Hard Safety Kernel flags correct patterns in test fixtures
- Friction budget counter increments correctly on each admission call

**Rollback**: Delete the new YAML binding file and the new struct/function
additions in topology_doctor_digest.py. One revert commit. Zero behavior impact.

---

## Phase 2 — Shadow Blocking

**Goal**: Run the v_next admission algorithm in parallel with the current
algorithm. Compare outputs. Log discrepancies. The current algorithm's
result is the authoritative result. v_next runs as a shadow.

**Entry criteria**:
- Phase 1 exit criteria met
- At least two weeks of Phase 1 data showing coverage map and kernel
  are stable (no false positives on known-good operations)

**What ships in Phase 2**:
1. v_next admission function `admit_v_next()` added to
   topology_doctor_digest.py; implements the new admission unit
   `(typed-intent, file-path-list, profile-hint?)` from the spec
2. Every call to the current `admit()` also calls `admit_v_next()`;
   result is logged to a shadow log with a diff field
3. Shadow log format: jsonlines; one record per admission; fields:
   timestamp, files, intent, current_result (profile+severity),
   v_next_result (AdmissionDecision), diff (AGREE/DISAGREE_PROFILE/
   DISAGREE_SEVERITY/DISAGREE_COMPANION/DISAGREE_HARD_STOP)
4. Cohort Admission function added; wired into admit_v_next()
5. Composition Rules C1-C4 implemented; wired into admit_v_next()
6. Missing-phrase generator: when v_next would ADMIT but current ADVISORY,
   log what phrase would have helped in current system

**Tests to add in Phase 2**:
- For each friction pattern in REGRESSION_PROBE_SUITE.md: current system
  reproduces the pattern; v_next resolves it (AGREE_ADMIT vs current ADVISORY)
- Shadow log DISAGREE_HARD_STOP never fires on known-safe operations
- Cohort admission correctly admits all declared Zeus cohorts
- Composition Rule C1 correctly admits new-test + test_topology.yaml
  under the test_suite profile

**Exit criteria**:
- Shadow runs for at least 10 distinct admission calls (mix of plan,
  create_new, modify, audit intents)
- DISAGREE rate for HARD_STOP is 0%
- DISAGREE rate for severity (current ADVISORY, v_next ADMIT) matches
  the friction patterns documented in REGRESSION_PROBE_SUITE.md
- No DISAGREE_HARD_STOP or DISAGREE_SEVERITY in the wrong direction
  (v_next more permissive than current on HARD_STOP paths)

**Rollback**: Delete `admit_v_next()`, shadow log writer, and shadow log files.
One revert commit. Current admission path unaffected.

---

## Phase 3 — Cutover Candidates (Low Blast Radius First)

**Goal**: Switch specific profile categories to use v_next as the authoritative
admission result, starting with profiles where blast radius is lowest.
Current algorithm kept as fallback for non-migrated profiles.

**Profile cutover order** (lowest to highest blast radius):
1. `packet_evidence` profile — docs/operations/task_* only; no source
2. `scripts_tooling` profile — scripts/*.py and *.sh; no money path
3. `agent_runtime` profile — topology tooling files; meta but not money
4. `architecture_docs` profile — architecture YAMLs; influential but not live
5. `config_management` profile — config/*.yaml; requires careful test coverage
6. `test_suite` profile — tests/**; enforces companion requirement cohort
7. `docs_authority` profile — docs/reference, AGENTS.md; authority docs
8. `monitoring` profile — observability; read-only runtime
9. `data_ingestion` profile — ingestion sources; not pricing
10. `state_read_model` profile — state DB read paths; not mutation
11. `calibration` profile — calibration update paths; requires proof gates
12. `forecast_pipeline` profile — cycle runner; adjacent to pricing
13. `money_path_pricing` and `money_path_execution` — last; max ceremony

**Entry criteria for each cutover candidate**:
- Shadow log shows AGREE rate ≥ 95% for the target profile over the
  preceding two weeks
- No DISAGREE_HARD_STOP for the target profile's file patterns
- At least one DISAGREE that was a v_next improvement (friction pattern resolved)

**What ships per cutover candidate**:
- Admission router updated: if profile == <target_profile>, use admit_v_next()
  result as authoritative; current result demoted to informational log
- Probe in REGRESSION_PROBE_SUITE.md confirming the target profile's
  friction patterns are resolved post-cutover
- Binding layer artifact_authority_status entry confirmed current for
  the target profile's governing authority docs

**Exit criteria for Phase 3**:
- All 13 profiles above have completed individual cutover
- Zero regressions: no previously passing operation now blocked
- REGRESSION_PROBE_SUITE.md probes all green for each completed cutover

**Rollback per profile**: One-line config change routing the target profile
back to current algorithm. No code revert required if router uses a config flag.

---

## Phase 4 — Full Cutover with Revert Path

**Goal**: Remove the current admission path. v_next is the sole admission
algorithm. The legacy `admit()` function is deleted or archived. Revert
path exists via git tag and shadow log replay.

**Entry criteria**:
- Phase 3 complete for all 13 profiles
- Shadow log confirms no admission type has DISAGREE_HARD_STOP in past 30 days
- REGRESSION_PROBE_SUITE.md probes all green for all profiles
- Friction budget p50 is 1 (first attempt succeeds for median admission)
- Friction budget p95 is ≤ 2 (two attempts covers 95th percentile)

**What ships in Phase 4**:
1. Legacy `admit()` function deleted from topology_doctor_digest.py
2. `admit_v_next()` renamed to `admit()` (API compatibility maintained)
3. Shadow log writer deleted; admission log switches to v_next-only format
4. Git tag `topology-vnext-cutover` on the cutover commit
5. AGENTS.md updated to reflect v_next admission API
6. All `--navigation` CLI examples in docs updated to include `--intent` flag

**Revert path**:
- `git revert` to the tag `topology-vnext-cutover` restores legacy path
- Shadow log from Phase 2/3 provides historical admission behavior for
  any dispute about what the old system would have decided

**Post-cutover validation** (run day 1, day 7, day 30):
```
All REGRESSION_PROBE_SUITE.md probes pass
Friction budget p50 ≤ 1 (first attempt succeeds)
Friction budget p95 ≤ 2
Zero HARD_STOP false positives in 30-day window
Zero coverage gaps in current src/** patterns
```

**Rollback**: `git revert <cutover-tag>`. Full restoration in one commit.
Shadow logs preserved in tmp/ for 90 days post-cutover for audit purposes.

---

## Migration Summary

| Phase | Action | Current System | V_next Role | Rollback Cost |
|-------|--------|---------------|-------------|---------------|
| 1 | Build data structures | Sole authority | Shadow (not consulted) | One revert commit |
| 2 | Shadow-run v_next | Sole authority | Shadow (logged, compared) | Delete shadow writer |
| 3 | Cutover low-risk profiles | Fallback for non-migrated | Authority for migrated | One-line config per profile |
| 4 | Full cutover | Deleted | Sole authority | git revert to tag |

Total migration: designed for 4-6 weeks of parallel running before full
cutover. Phase 1 alone produces immediate value (coverage gap visibility,
kernel alert logging) with zero behavior risk.
