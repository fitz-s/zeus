# Extracted Lessons from May 2–8 Task Batch

**Source:** 59 unarchived `docs/operations/task_2026-05-0[2-8]*` dirs  
**Extracted by:** executor (Claude Sonnet 4.6), 2026-05-16  
**Method:** per-dir triage, file-content scan of REPORT.md / RUN.md / DOSSIER.md / QUARANTINE_LEDGER.md / critic review files; only dirs with HISTORICAL_LESSON classification are included.

---

## 1. task_2026-05-02_live_entry_data_contract

**Lesson:** When a commit message quotes a count from a DB query (e.g., "204 LIVE_ELIGIBLE"), always state the aggregation scope explicitly — per-track vs aggregate yields 2× different numbers from the same table, and the next critic will count the other way.  
**Source:** `docs/operations/task_2026-05-02_live_entry_data_contract/PREMISE_ERRATUM_2026-05-03.md` — "Commit message used per-track figures. Critic counted aggregate. Both correct measurements. No divergence. Future commit messages: name the scope."  
**Drill-in:** PREMISE_ERRATUM_2026-05-03.md → "Operational note" section

---

## 2. task_2026-05-04_zeus_may3_review_remediation

**Lesson (a):** Critic round-5 established the anti-rubber-stamp protocol — every claim in a critic review must be backed by an independently-run grep/sed/ps, not the planner's own output; "pattern proven" / "narrow scope self-validating" are prohibited phrases.  
**Source:** `docs/operations/task_2026-05-04_zeus_may3_review_remediation/critic_round5_response.md` §0 "Anti-rubber-stamp pledge" + §1 cite-content verification table (14 PASS / 1 PARTIAL)  
**Drill-in:** `critic_round5_response.md` → §1 full table for the verification methodology

**Lesson (b):** When an operator lock row is written with `system_auto_pause` issuer (precedence=100, auto-expiry=15min), a subsequent auto-pause from an unrelated exception can silently overwrite it and expire it — leaving the lock decorative for ~5 minutes. Operator-issued locks must use `precedence ≥ 200` and `effective_until=NULL`.  
**Source:** `docs/operations/task_2026-05-04_zeus_may3_review_remediation/LOCK_DECISION.md` §3 Amendment 1 and embedded history "between 09:34:17 and 09:39:00 UTC layer 3 was decorative"  
**Drill-in:** `LOCK_DECISION.md` → Amendment 1 + "Lock layer 3 history" in `LIVE_TRADING_LOCKED_2026-05-04.md` (now archived to `archive/2026-Q2/`)

---

## 3. task_2026-05-06_calibration_quality_blockers

**Lesson:** Platt models with `param_A < 0` (strict signal inversion) must be quarantined at fit-time, not caught in production; the fit-time guard must preserve the prior VERIFIED row rather than overwriting it with a bad fit — a bad refit that skips `save_platt_model_v2` is safer than a bad refit that writes and overwrites.  
**Source:** `docs/operations/task_2026-05-06_calibration_quality_blockers/QUARANTINE_LEDGER.md` §"Action taken" item 2: "`_fit_bucket` now checks `cal.A < 0` post-fit and skips both `deactivate_model_v2` and `save_platt_model_v2` — previously-VERIFIED row is preserved intact."  
**Drill-in:** `QUARANTINE_LEDGER.md` → "Action taken" §2 + "Threshold rationale" for A ∈ [0, 0.3) keep-VERIFIED reasoning  
**Antibody location:** `scripts/refit_platt_v2.py::_fit_bucket` (as of PR #70)

---

## 4. task_2026-05-06_topology_redesign

**Lesson:** A navigation topology built on profile catalogs (one YAML entry per module) degrades to O(N) maintenance as N modules grow; the durable alternative is source-tagging (`@capability`, `@protects`) + generative route cards computed on-demand from diff, anchored in physical/economic invariants that don't change as the codebase evolves.  
**Source:** `docs/operations/task_2026-05-06_topology_redesign/BEST_DESIGN.md` §1 "The Design in One Diagram" — STABLE LAYER / SOURCE TAGGING / GENERATIVE LAYER architecture  
**Drill-in:** `BEST_DESIGN.md` §1-§3; companion `PLAN_AMENDMENT.md` for why the profile-catalog approach failed

---

## 5. task_2026-05-07_recalibration_after_low_high_alignment

**Lesson:** The LOW calibration recovery exposed a critical seam: 12Z LOW Platt buckets all had `n_eff < 50` after recovery, so they must be written as `UNVERIFIED` shadow rather than `VERIFIED` — and the live read seam must block LOW season-pool fallback (which would silently substitute another city's calibration) until a contract-bin-preserving fallback proof exists.  
**Source:** `docs/operations/task_2026-05-07_recalibration_after_low_high_alignment/REPORT.md` → "Executive Result" + Before/After table + "Runtime Samples" (Chicago LOW 00Z: LIVE; Chicago LOW 12Z: RAW_UNCALIBRATED)  
**Drill-in:** `REPORT.md` → "Code Changes" for `src/calibration/manager.py` LOW source-tagged request changes

---

## 6. task_2026-05-08_ecmwf_publication_strategy

**Lesson:** The premise "ECMWF publishes long-horizon steps in a later batch than short steps" is empirically WRONG for the Open Data ENS index files Zeus consumes — the CDN publishes all steps as a single batch at T+7h40min. What looks like a publication-window bug is actually a permanent step-grid hole (step=147h is not in the dissemination grid). Before filing a bug against ECMWF publication timing, confirm empirically whether the URL pattern exists at all via HEAD probe.  
**Source:** `docs/operations/task_2026-05-08_ecmwf_publication_strategy/REPORT.md` §1.4 empirical probe table + §1.6 reconciliation + §2 root-cause reframe table  
**Drill-in:** `REPORT.md` §1.6 "Reconciliation" — the single-batch CDN upload vs "Derived products Step 246-360 at 08:01" distinction

---

## 7. task_2026-05-08_ecmwf_step_grid_scientist_eval

**Lesson:** The ECMWF IFS model time-step grid (1h/3h/6h three-part) differs from the Open Data ENS dissemination grid (3h 0–144, 6h 144–360 two-part) — using the wrong grid leads to requesting non-disseminated hourly steps; always verify against the `ecmwf-opendata` PyPI README (authoritative for Zeus's consumption path), not the IFS/MARS underlying grid documentation.  
**Source:** `docs/operations/task_2026-05-08_ecmwf_step_grid_scientist_eval/REPORT.md` §1.4 "Reconciliation of the brief's three-part claim" — "haiku's three-part claim is half-correct: accurate for IFS model grid, inaccurate for ENS dissemination stream Zeus actually consumes."  
**Drill-in:** `REPORT.md` §1.2 (Open Data dissemination grid verbatim) + §2 per-product step grid table

---

## 8. task_2026-05-08_phase_b_download_root_cause

**Lesson:** APScheduler "job ran to completion" (rc=0 at scheduler level) does NOT mean the download succeeded — the internal `download_failed` verdict is written to `source_run` table; zero new `source_run` rows post-2026-05-04 proved the scheduler was firing while returning false positives. Any health dashboard must read `source_run` row counts, not scheduler job completion timestamps.  
**Source:** `docs/operations/task_2026-05-08_phase_b_download_root_cause/DOSSIER.md` §VERDICT item 3: "`scheduler_jobs_health.json` records `last_success=2026-05-08T14:21:37Z` — this is job completion timestamp, not successful download. Zero `source_run` rows exist post-2026-05-04."  
**Drill-in:** `DOSSIER.md` §FINDINGS Finding 1 for the multi-day failure history embedded in zeus-ingest.err since 2026-05-01

---

## 9. task_2026-05-08_obs_outside_bin_audit

**Lesson:** 612 `harvester_live_obs_outside_bin` QUARANTINED settlements decomposed into two root causes traceable via DB query: (a) 317 London F-bin rows (unit mismatch — pre-reconfiguration markets), (b) 181 F-unit NULL-bin rows (market questions with no bin bounds), and (c) ~114 other cities. SQL methodology: query `provenance_json` JSON fields on `settlements_v2` grouped by city + unit + bin_lo + bin_hi to distinguish structural unit errors from missing-data errors.  
**Source:** `docs/operations/task_2026-05-08_obs_outside_bin_audit/RUN.md` — 8-query sequence with result annotations  
**Drill-in:** `RUN.md` → query 6 (city × unit), query 8 (NULL-bin cluster)

---

## 10. task_2026-05-08_262_london_f_to_c

**Lesson:** `_parse_temp_range(question)` strips the unit symbol from market question text; when London was reconfigured from F to C, all 317 historical Gamma markets (F-labeled bins) produced QUARANTINED settlements because the stripped numeric bin [40, 41] was compared against a C observation (5°C). The fix requires extracting and preserving the unit from the `range_label` field and converting bin bounds at settlement-reconstruction time.  
**Source:** `docs/operations/task_2026-05-08_262_london_f_to_c/RUN.md` §"Root cause" + §"Code path" diagram  
**Drill-in:** `RUN.md` → §"F->C transform" for the exact conversion formula + sample rows table

---

## 11. task_2026-05-08_100_blocked_horizon_audit

**Lesson:** STEP_HOURS `range(3, 279, 3)` (max 276h) blocked D+10 readiness for UTC+12 cities (Amsterdam required steps up to 252h; others up to 282h); the ECMWF API 240h ceiling applies only to `type=ep` — Zeus uses `type=["cf","pf"]` so the ceiling does not apply and extending to 282h is safe.  
**Source:** `docs/operations/task_2026-05-08_100_blocked_horizon_audit/RUN.md` §"pre-state" (BLOCKED root cause) + §"changes" (STEP_HOURS fix) + §"tests added" (15 tests in 2 files)  
**Drill-in:** `RUN.md` → "ECMWF API ceiling" note for the `type=ep` vs `type=cf/pf` distinction

---

## 12. task_2026-05-08_f1_subprocess_hardening

**Lesson:** The 600s download timeout was the primary failure cause after PR#94 extended STEP_HOURS (empirical full-fetch measured 609.6s); bounded retry with delay-escalation (0, 60, 180s) plus distinguishing grid-valid 404 (→ `SKIPPED_NOT_RELEASED`, no retry) from other rc≠0 (→ retryable) is more reliable than a single-attempt hard failure.  
**Source:** `docs/operations/task_2026-05-08_f1_subprocess_hardening/RUN.md` §"Summary" (a)/(b)/(c) + §"Tests" for the 5 test cases  
**Drill-in:** `RUN.md` → §"Summary" item (b) for the 404-classification logic

---

## 13. task_2026-05-08_alignment_repair_workflow

**Lesson:** When transitioning from a large audit phase to implementation, breaking work into independent verifiable packets (S1 provenance → S2 lifecycle funnel → S3 calibration status → S4 price evidence) with required topology navigation before EACH packet prevents scope creep and maintains money-path invariants; topology navigation returning `advisory_only` for docs-only changes is expected and safe to proceed through.  
**Source:** `docs/operations/task_2026-05-08_alignment_repair_workflow/TASK.md` §"Safe Implementation Queue" + §"Required Workflow Per Packet"  
**Drill-in:** `PROGRESS.md` → "Repair Focus Set" + "Topology Navigation" for the advisory_only vs blocked distinction

---

## 14. task_2026-05-08_post_merge_full_chain

**Lesson:** Post-merge health verification requires restarting ALL affected daemons (not just checking plist presence), re-verifying catch-up job completion via `source_run` rows (not `scheduler_jobs_health.json`), and treating "Phase B BLOCKED" as a hard stop requiring root-cause before declaring the merge healthy.  
**Source:** `docs/operations/task_2026-05-08_post_merge_full_chain/RUN.md` Phase A (daemon restart + STEP_HOURS verification) and Phase B (ECMWF download BLOCKED — download failure surfaced via APScheduler false positive)  
**Drill-in:** `RUN.md` → Phase B acceptance criteria gap (scheduler shows success, `source_run` shows failure)

---

## 15. task_2026-05-03_ddd_implementation_plan.md

**Lesson:** This file is a stub indicating the actual content was already moved to `archive/task_2026-05-03_ddd_implementation_plan/` — active references remain in `src/oracle/data_density_discount.py`, `src/calibration/store.py`, and `architecture/calibration.yaml`. No novel lesson extractable from the stub.  
**Classification note:** ROUTINE_ARCHIVE of an already-archived stub file.  
**Drill-in:** `archive/task_2026-05-03_ddd_implementation_plan/INDEX.md` for complete file inventory

---

*15 lessons extracted from 15 HISTORICAL_LESSON-classified dirs (May 2–8 batch).*  
*Remaining 44 dirs: ROUTINE_ARCHIVE (PLAN.md only or KEEP_ACTIVE — see ARCHIVE_QUEUE_FOR_NEXT_PR.md).*

---

## Extended Batch — May 9–14 (SURFACE 1 EXTENDED)

*2 additional HISTORICAL_LESSON dirs from the deeper 3-day lifecycle audit.*

---

## 16. task_2026-05-09_pr_workflow_failure

**Lesson:** The pre-B2 merge gate was scoped only to the loudest bot signals (Codex P0/P1, CHANGES_REQUESTED); Copilot inline suggestions and Codex P2 ("should fix") leaked through, allowing PR #106 to merge with 6 unaddressed comments — exposing a cost-economics / quality-economics split: the 300-LOC gate covered cost (don't open tiny PRs) but no gate covered quality (don't merge without processing every comment). The canonical fix encodes both in four principles + strict B2 hook that blocks on ANY unresolved thread.  
**Source:** `docs/operations/task_2026-05-09_pr_workflow_failure/ANALYSIS.md` — "historical record — superseded fix design"; §"Where the merge-gate hook failed" (design gap table); §"Where the workflow doctrine failed"  
**Drill-in:** `ANALYSIS.md` §"What actually happened on PR #106" (644s timeline) + §"Where the merge-gate hook failed" (severity-tag gap table); canonical fix at `architecture/agent_pr_discipline_2026_05_09.md` (four-principle framework)

---

## 17. task_2026-05-11_tigge_vm_to_zeus_db

**Lesson (a):** The OpenData ingest pipeline mislabeled `data_version` as `ecmwf_opendata_mx2t6_local_calendar_day_max_v1` when the actual downloaded parameter was `mx2t3` — 1342 production rows from 2026-05-05 onward carry this defect. The download script itself (`--param mx2t3`) was correct; the bug is in downstream JSON-build / `data_version` assignment. A STAGE_DB-only fix (`UPDATE training_allowed=0`) must be replicated to production before any production calibration rebuild can pass the preflight gate.  
**Source:** `docs/operations/task_2026-05-11_tigge_vm_to_zeus_db/EVIDENCE_2026-05-11_session.md` §"Open issue" — "data_version field: writes `mx2t6` when source product is actually `mx2t3`. Production zeus-world.db has 1342 rows with this defect."  
**Drill-in:** `EVIDENCE_2026-05-11_session.md` §"STAGE-only fix caveat" for promotion sequence; `POST_REBUILD_HANDOFF_2026-05-11.md` §2a-2b for ECMWF ingest diagnostic

**Lesson (b):** The critical-path from rebuild-complete to live-active requires 6 gated steps including an explicit operator-gated STAGE_DB → production promotion (Step 1); `evaluate_entry_forecast_rollout_gate` will only return `LIVE_ELIGIBLE` after `entry_forecast_promotion_evidence.json` is atomically written via the CLI (`promote_entry_forecast propose --commit`), and the rollout mode must be flipped in `config/settings.json` (NOT an env var — `ZEUS_ENTRY_FORECAST_ROLLOUT_MODE` is not wired).  
**Source:** `docs/operations/task_2026-05-11_tigge_vm_to_zeus_db/POST_REBUILD_HANDOFF_2026-05-11.md` §"Critical path: rebuild-complete → live-active" (6-step table, Steps 4-6)  
**Drill-in:** `POST_REBUILD_HANDOFF_2026-05-11.md` §Step 5 for rollout mode flip CLI invocation; §"TIME-WINDOW RISK" section (truncated — read full file for the time-sensitivity notes)

---

*17 total lessons extracted across May 2–14 batch (15 from May 2–8 + 2 from May 9–14).*  
*See ARCHIVE_QUEUE_FOR_NEXT_PR.md for full surface triage (79 items across 4 surfaces).*
