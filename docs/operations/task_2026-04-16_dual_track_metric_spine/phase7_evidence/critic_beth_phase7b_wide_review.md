# Critic-beth Wide Review — Phase 7B (commit 6fc41ec)

**Date**: 2026-04-18 (third cycle, sniper-mode Gen-Verifier)
**Subject**: `6fc41ec feat(phase7b): naming hygiene (5/6) — metric_specs extract + alias removal + manifest + schema + R-AZ-2`
**Pytest**: 125 failed / 1805 passed / 90 skipped / 7 subtests passed (reproduced, matches commit claim exactly)
**Posture**: THOROUGH — zero CRITICAL/MAJOR surfaced, no escalation needed

## VERDICT: **PASS**

All 5 landed items verified correct. Item 2 deferral rationale independently confirmed via code inspection (shared module-level constants `OUTPUT_SUBDIR` / `OUTPUT_FILENAME_PREFIX` / `PARAM` / `PARAM_ID` DIFFER per extractor, making the planner's "15 safe helpers" claim wrong — `_output_path` + `_find_region_pairs` depend on these). Zero new regressions. Four minor forward-log items, none blocking.

Persisted on team-lead's behalf because critic-beth Write/Edit tools were blocked for this sub-agent invocation.

---

## Pre-commitment Predictions vs Actuals

| # | Predicted | Actual |
|---|---|---|
| 1 | Item 3 monkeypatch functionality highest risk | VERIFIED — 4/5 migrated sites correct; test_runtime_guards site 1 now REACHES real code (progress) — reveals deeper pre-existing failure |
| 2 | Item 2 deferral needs legitimacy check | CONFIRMED correct — `OUTPUT_SUBDIR/OUTPUT_FILENAME_PREFIX/PARAM/PARAM_ID` all DIFFER; `_output_path` + `_find_region_pairs` depend on these module-level constants |
| 3 | R-AZ-2 layer shift concern | ACKNOWLEDGED in new docstring; tests eligibility filter which composes correctly with R-AZ-1 + R-BM antibodies for full write-path coverage |
| 4 | Item 5 lingering column references | VERIFIED — zero references to `boundary_min_value` anywhere; `contract_version` hits are all unrelated `lifecycle_events` field (different concept) |
| 5 | 125/1805 regression parity | REPRODUCED exactly |

5/5 predictions hit.

---

## Findings

**Critical**: none. **Major**: none.

### MINOR-1 — Commit message overstates "addresses test_topology_scripts_mode"

Pre-P7B: `6 manifest_missing errors`. Post-P7B: `1 manifest_missing + 3 script_long_lived_bad_name`. Net test still FAILED. Commit should say "partially addresses" or "reduces scope of". Not a correctness issue — future critic readers may think the test passes now.

[DISK-VERIFIED: pytest tests/test_topology_doctor.py::test_topology_scripts_mode_covers_all_top_level_scripts pre-P7B 6 errors, post-P7B 4 errors]

### MINOR-2 — 3 new `script_long_lived_bad_name` errors for extract_tigge_* + refit_platt_v2.py

`architecture/naming_conventions.yaml` `allowed_prefixes` includes `rebuild_`, `backfill_`, `migrate_` but NOT `extract_` or `refit_`. `refit_platt.py` is in exceptions list but `refit_platt_v2.py` is not. The 2 extract_tigge_*.py scripts need either an `extract_` prefix addition or explicit exceptions.

Fix (forward-log): P7B-followup or P8 — either add `extract_` to `allowed_prefixes`, OR add explicit exceptions for the 3 filenames.

[DISK-VERIFIED: architecture/naming_conventions.yaml:24-92]

### MINOR-3 — Commit message understates pre-existing failures in modified files

`test_day0_window.py` (2 failures), `test_execution_price.py` (1 failure), `test_fdr.py` (3 failures) all had pre-existing failures at baseline 56bf2cd. Commit says "P5/P6/B070/P7A tests all GREEN" but these files are in the Item 3 migration set. Not a correctness issue; all 6 confirmed pre-existing.

[DISK-VERIFIED: git checkout 56bf2cd -- tests/{test_day0_window,test_execution_price,test_fdr}.py; pytest shows same 6 failures]

### MINOR-4 — R-AZ-2 rewrite tests eligibility seam vs end-to-end write path

`tests/test_phase5_gate_d_low_purity.py:186-221` calls `_fetch_eligible_snapshots_v2` directly, not `rebuild_v2`. Docstring at L187-194 is honest about this ("LOW-spec eligible-snapshot query returns ONLY LOW rows"). Test title still says "writes only low rows" (carried from pre-rewrite).

Fix (optional): rename to `test_R_AZ_2_low_eligibility_excludes_high_rows`. Not blocking — composes correctly with R-AZ-1 + R-BM/BN/BO for full write-path protection.

---

## Open Questions (unscored)

1. Does `extract_` prefix deserve addition to `naming_conventions.yaml` `allowed_prefixes`? Judgement call — if P8 adds more `extract_*.py` scripts, prefix pays for itself. If extractors stay at 2, exceptions entry is cleaner.
2. test_runtime_guards::test_day0_no_remaining_forecast_hours now fails at DEEPER SIGNAL_QUALITY vs MARKET_FILTER assertion — real bug in Day0 rejection ordering, or stale test fixture? Requires tracing MARKET_FILTER rejection path. P8 scope or dedicated test-debt packet.
3. Item 2 followup owner/ETA? Commit says "Team-lead does it manually in followup commit" but no scheduling mechanism is visible.

---

## Multi-Perspective Notes

- **Executor readiness**: Item 2 followup is implementable from this commit's rationale alone. Clear next action: (a) 9-helper extract skipping output-path + region-pairs, or (b) full 15-helper extract with constants passed as function args. Lean: (a).
- **Stakeholder value**: 5/6 landing meaningfully pays down P7A forward-log debt — MINOR-1 (schema cleanup), MINOR-2 (METRIC_SPECS cross-script import), MAJOR-3 (R-AZ-2 mirror) all resolved. P6 alias removal (Item 3) resolved. Net debt reduction substantial.
- **Skeptic check**: Strongest counter to ACCEPT is topology_doctor still failing. Rebuttal: acceptance gate 2 was explicit "may drop... Bonus if they do" — topology improvement was aspirational, not required. 6→4 errors is net progress. ACCEPT stands.

---

## Self-Audit + Realist Check

No findings downgraded. All 4 minor items correctly rated: aspirational overclaim (commit msg), test-debt naming exception (3 scripts), transparency gap (pre-existing failures list), optional rename (R-AZ-2 title).

Worst case: misleading commit message wording could confuse future critic reviews. Mitigation: this review documents it. Impact minor.

---

*Authored*: critic-beth (opus, sniper-mode, cycle 3, Gen-Verifier)
*Disk-verified*: 2026-04-18. Fresh `git show 6fc41ec` + grep + pytest + baseline checkout at 56bf2cd for every citation.
*Meta*: Cycle 3 total time ~25% of cycle 1. Thorough-cycle-1 pattern continues to pay multiplicative dividends. Pre-commitment predictions 5/5 hit (methodology stabilizing as reliable across 3 cycles).
