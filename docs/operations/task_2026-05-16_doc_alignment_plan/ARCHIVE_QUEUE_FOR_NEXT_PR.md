# Archive Queue — May 2–8 Task Batch

**Purpose:** Post-merge cleanup list. DO NOT `git mv` these in the current PR (would balloon diff).  
**Execute:** After this PR merges, run as a separate cleanup PR from canonical `main`.  
**Deferred-archive rationale:** Archiving 59 dirs via `git mv` adds ~500-1500 lines to PR diff, breaking review tooling size limits and obscuring the substantive changes in this PR.

**Execution script pattern:** Follow `scripts/archive_migration_2026-05-16.py` (if exists) or write `scripts/archive_migration_2026-Q2_followup.py` using the same `git mv + INDEX.md update` pattern used for the 28 already-archived dirs.

**Canonical archive target:** `docs/operations/archive/2026-Q2/`

---

## Summary Counts

| Category | Count |
|----------|-------|
| ROUTINE_ARCHIVE | 40 |
| HISTORICAL_LESSON (archive after lesson extraction confirmed) | 15 |
| KEEP_ACTIVE (cross-refs or recent commits — do NOT archive) | 10 |
| **Total** | **59** (some overlap — KEEP_ACTIVE dirs that also have lessons are classified KEEP_ACTIVE) |

Note on overlap: 4 dirs are both HISTORICAL_LESSON and KEEP_ACTIVE (`task_2026-05-08_ecmwf_publication_strategy`, `task_2026-05-08_ecmwf_step_grid_scientist_eval`, `task_2026-05-08_phase_b_download_root_cause`, `task_2026-05-08_post_merge_full_chain`). These are classified KEEP_ACTIVE in this table; lessons are already extracted to `EXTRACTED_LESSONS_FROM_MAY_BATCH.md`.

---

## Full Table

| dir | category | reason | extracted_lesson_ref |
|-----|----------|--------|----------------------|
| `task_2026-05-02_live_entry_data_contract` | HISTORICAL_LESSON | PREMISE_ERRATUM + PHASE0_EVIDENCE_LOCK + 4 PLAN versions | EXTRACTED_LESSONS §1 |
| `task_2026-05-03_ddd_implementation_plan.md` | ROUTINE_ARCHIVE | Archived-stub file; content already in archive/. Active refs in src/ noted in stub. | EXTRACTED_LESSONS §15 (stub note) |
| `task_2026-05-04_zeus_may3_review_remediation` | HISTORICAL_LESSON | critic_round5_response (anti-rubber-stamp protocol) + LOCK_DECISION (operator lock precedence lesson) | EXTRACTED_LESSONS §2 |
| `task_2026-05-05_object_invariance_mainline` | ROUTINE_ARCHIVE | PLAN.md only; mainline delivered in PR #90 | — |
| `task_2026-05-05_object_invariance_wave11` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave12` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave13` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave14` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave15` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave16` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave17` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave18` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave19` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave20` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave21` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave5` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave6` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave7` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_object_invariance_wave8` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-05_topology_noise_repair` | ROUTINE_ARCHIVE | PLAN.md only; topology v2 shipped in PR #71/#72 | — |
| `task_2026-05-06_calibration_quality_blockers` | HISTORICAL_LESSON | QUARANTINE_LEDGER — inverted-slope Platt quarantine protocol | EXTRACTED_LESSONS §3 |
| `task_2026-05-06_hook_redesign` | KEEP_ACTIVE | Cross-ref in `architecture/topology_v_next_binding.yaml`; recent commit `6be2f27b1a` (live e2e verification) touches this dir | — |
| `task_2026-05-06_topology_redesign` | HISTORICAL_LESSON | BEST_DESIGN.md — source-tagging + generative route-card architecture; ADR subdir | EXTRACTED_LESSONS §4 |
| `task_2026-05-07_hook_redesign_v2` | ROUTINE_ARCHIVE | PLAN.md only; hook redesign shipped in PR #71/#72 | — |
| `task_2026-05-07_navigation_topology_v2` | ROUTINE_ARCHIVE | PLAN.md only; nav topology v2 shipped in PR #72 | — |
| `task_2026-05-07_object_invariance_wave24` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-07_object_invariance_wave25` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-07_object_invariance_wave26` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-07_recalibration_after_low_high_alignment` | HISTORICAL_LESSON | REPORT — LOW/HIGH alignment recovery, 12Z shadow-only rationale, LOW fallback block | EXTRACTED_LESSONS §5 |
| `task_2026-05-08_100_blocked_horizon_audit` | KEEP_ACTIVE | Cross-ref in `architecture/data_sources_registry_2026_05_08.yaml`; commit `2f436a6b21` | EXTRACTED_LESSONS §11 |
| `task_2026-05-08_262_london_f_to_c` | HISTORICAL_LESSON | RUN.md — unit-stripping bug root cause + F→C conversion fix | EXTRACTED_LESSONS §10 |
| `task_2026-05-08_alignment_repair_workflow` | KEEP_ACTIVE | PROGRESS + TASK live repair workflow docs; commit `2f436a6b21` | EXTRACTED_LESSONS §13 |
| `task_2026-05-08_alignment_safe_implementation` | KEEP_ACTIVE | Commit `2f436a6b21` (recent); implementation packets S1-S4 may still be in-flight | — |
| `task_2026-05-08_deep_alignment_audit` | KEEP_ACTIVE | Commit `2f436a6b21` (recent); audit findings may feed active repair work | — |
| `task_2026-05-08_ecmwf_publication_strategy` | KEEP_ACTIVE | Commit `2f436a6b21`; REPORT is active authority for F1/F2 hardening decisions | EXTRACTED_LESSONS §6 |
| `task_2026-05-08_ecmwf_step_grid_scientist_eval` | KEEP_ACTIVE | Commit `2f436a6b21`; REPORT is active authority for step-grid decisions | EXTRACTED_LESSONS §7 |
| `task_2026-05-08_f1_subprocess_hardening` | HISTORICAL_LESSON | RUN.md — timeout/retry/stderr-capture changes; 5 tests | EXTRACTED_LESSONS §12 |
| `task_2026-05-08_low_recalibration_residue_pr` | KEEP_ACTIVE | Commit `2f436a6b21` | — |
| `task_2026-05-08_object_invariance_remaining_mainline` | ROUTINE_ARCHIVE | PLAN.md only; remaining mainline waves shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave27` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave28` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave29` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave30` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave31` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave32` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave33` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave34` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave35` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave36` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave37` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave38` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave39` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave41` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_object_invariance_wave42` | ROUTINE_ARCHIVE | PLAN.md only; wave shipped in PR #90 | — |
| `task_2026-05-08_obs_outside_bin_audit` | HISTORICAL_LESSON | RUN.md — 8-query SQL methodology for settlement quarantine root-cause decomposition | EXTRACTED_LESSONS §9 |
| `task_2026-05-08_phase_b_download_root_cause` | KEEP_ACTIVE | Commit `2f436a6b21`; DOSSIER is live authority for ECMWF download failure analysis | EXTRACTED_LESSONS §8 |
| `task_2026-05-08_post_merge_full_chain` | KEEP_ACTIVE | Cross-ref in `architecture/script_manifest.yaml` + `architecture/test_topology.yaml`; commit `2f436a6b21` | EXTRACTED_LESSONS §14 |
| `task_2026-05-08_topology_redesign_completion` | ROUTINE_ARCHIVE | PLAN.md only; topology completion shipped in PR #72 | — |
| `task_2026-05-08_track_a6_run` | KEEP_ACTIVE | Commit `2f436a6b21` | — |

---

## Execution Instructions for Post-Merge Archive PR

```bash
# In canonical zeus/ after this PR merges to main
cd /Users/leofitz/.openclaw/workspace-venus/zeus

# Archive ROUTINE_ARCHIVE and HISTORICAL_LESSON dirs (40 + 11 = 51 dirs)
# SKIP all 10 KEEP_ACTIVE dirs
ARCHIVE_TARGET="docs/operations/archive/2026-Q2"

# Example batch — run after confirming KEEP_ACTIVE list hasn't changed
for dir in \
  task_2026-05-02_live_entry_data_contract \
  task_2026-05-03_ddd_implementation_plan.md \
  task_2026-05-04_zeus_may3_review_remediation \
  task_2026-05-05_object_invariance_mainline \
  task_2026-05-05_object_invariance_wave11 \
  task_2026-05-05_object_invariance_wave12 \
  task_2026-05-05_object_invariance_wave13 \
  task_2026-05-05_object_invariance_wave14 \
  task_2026-05-05_object_invariance_wave15 \
  task_2026-05-05_object_invariance_wave16 \
  task_2026-05-05_object_invariance_wave17 \
  task_2026-05-05_object_invariance_wave18 \
  task_2026-05-05_object_invariance_wave19 \
  task_2026-05-05_object_invariance_wave20 \
  task_2026-05-05_object_invariance_wave21 \
  task_2026-05-05_object_invariance_wave5 \
  task_2026-05-05_object_invariance_wave6 \
  task_2026-05-05_object_invariance_wave7 \
  task_2026-05-05_object_invariance_wave8 \
  task_2026-05-05_topology_noise_repair \
  task_2026-05-06_calibration_quality_blockers \
  task_2026-05-06_topology_redesign \
  task_2026-05-07_hook_redesign_v2 \
  task_2026-05-07_navigation_topology_v2 \
  task_2026-05-07_object_invariance_wave24 \
  task_2026-05-07_object_invariance_wave25 \
  task_2026-05-07_object_invariance_wave26 \
  task_2026-05-07_recalibration_after_low_high_alignment \
  task_2026-05-08_262_london_f_to_c \
  task_2026-05-08_f1_subprocess_hardening \
  task_2026-05-08_object_invariance_remaining_mainline \
  task_2026-05-08_object_invariance_wave27 \
  task_2026-05-08_object_invariance_wave28 \
  task_2026-05-08_object_invariance_wave29 \
  task_2026-05-08_object_invariance_wave30 \
  task_2026-05-08_object_invariance_wave31 \
  task_2026-05-08_object_invariance_wave32 \
  task_2026-05-08_object_invariance_wave33 \
  task_2026-05-08_object_invariance_wave34 \
  task_2026-05-08_object_invariance_wave35 \
  task_2026-05-08_object_invariance_wave36 \
  task_2026-05-08_object_invariance_wave37 \
  task_2026-05-08_object_invariance_wave38 \
  task_2026-05-08_object_invariance_wave39 \
  task_2026-05-08_object_invariance_wave41 \
  task_2026-05-08_object_invariance_wave42 \
  task_2026-05-08_obs_outside_bin_audit \
  task_2026-05-08_topology_redesign_completion \
; do
  git mv "docs/operations/$dir" "$ARCHIVE_TARGET/"
done

# Update INDEX.md to reference the newly archived dirs
# Commit: "fix(cleanup): archive 49 completed May-2..8 task dirs to 2026-Q2 post-merge"
```

**Before running:** Re-verify KEEP_ACTIVE dirs via `git log --since=2026-05-09 --oneline -- docs/operations/<dir>` — KEEP status may change as the active repair work lands.

---

## KEEP_ACTIVE — Do NOT Archive

These 10 dirs have either recent commits (post-2026-05-09) or live architecture cross-references. Reassess after their referenced work lands on main.

| dir | keep reason | reassess when |
|-----|------------|---------------|
| `task_2026-05-06_hook_redesign` | Ref in `architecture/topology_v_next_binding.yaml`; commit `6be2f27b1a` | After topology_v_next ships |
| `task_2026-05-08_100_blocked_horizon_audit` | Ref in `architecture/data_sources_registry_2026_05_08.yaml` | After data sources registry stabilizes |
| `task_2026-05-08_alignment_repair_workflow` | Active repair workflow docs (S1-S4 packets in-flight); commit `2f436a6b21` | After all S1-S4 packets land on main |
| `task_2026-05-08_alignment_safe_implementation` | Implementation packets potentially in-flight; commit `2f436a6b21` | After repair branch merges |
| `task_2026-05-08_deep_alignment_audit` | Audit findings feed active repair work; commit `2f436a6b21` | After repair branch merges |
| `task_2026-05-08_ecmwf_publication_strategy` | REPORT is authority for F1/F2 hardening; commit `2f436a6b21` | After F2 AWS mirror fallback (deferred) is decided |
| `task_2026-05-08_ecmwf_step_grid_scientist_eval` | REPORT is authority for step-grid decisions; commit `2f436a6b21` | After step-grid is stable |
| `task_2026-05-08_low_recalibration_residue_pr` | Commit `2f436a6b21` | After cleanup PR for low-recalibration residue closes |
| `task_2026-05-08_phase_b_download_root_cause` | DOSSIER is active authority for ECMWF download failure analysis; commit `2f436a6b21` | After download pipeline is confirmed stable |
| `task_2026-05-08_post_merge_full_chain` | Ref in `architecture/script_manifest.yaml` + `architecture/test_topology.yaml`; commit `2f436a6b21` | After referenced architecture files are updated |
| `task_2026-05-08_track_a6_run` | Commit `2f436a6b21` | After track A6 run is closed |
