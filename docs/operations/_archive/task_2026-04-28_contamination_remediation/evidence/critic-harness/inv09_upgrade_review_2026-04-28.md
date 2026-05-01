# INV-09 Upgrade Review — Critic-Harness Gate

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
HEAD at review: `0a9ec93` (INV-09 upgrade commit; worktree-post-r5-eng)
Pre-batch baseline: 90/22/0 (4-file critic baseline) — preserved
Cycle 5 erratum count: 2/3 (engineering work, not erratum on prior verdict)

## Verdict

**APPROVE-WITH-CAVEATS** (1 LOW REVISE on omitted surface-(d) test; 1 LOW NUANCE on §5.X cite wording; 0 BLOCK conditions)

8/8 cited tests resolve + pass. Live baseline 90/22/0 preserved. 4 enforcement surfaces (a)(b)(c)(d) all verified at the cited source line offsets. Bidirectional grep on INV-09 in tests/ returns ZERO by-name references — coverage is by behavior, not by name (acceptable; INV-09 is a runtime property, not a test-naming convention). Excluded test `test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly` independently verified as pre-existing failure (assert None is not None at runtime_guards.py:3826) — correct exclusion.

I articulate WHY APPROVE-WITH-CAVEATS:
- 8/8 cited tests collect + pass in 1.88s (independent reproduction)
- 4/4 enforcement surfaces verified at cited line offsets:
  - (a) data_coverage.py L162-170+L350+L402: status='MISSING' is queryable state with allowed transitions ✓
  - (b) db.py:2164: `trace_status = "degraded_missing_vectors"` exact string match ✓
  - (c) db.py:4455+: `"separation": {"opportunity_loss_without_availability": 0, ...}` confirmed at L4459 ✓
  - (d) collateral_ledger.py:46: `authority_tier TEXT NOT NULL CHECK (authority_tier IN ('CHAIN','VENUE','DEGRADED'))` ✓
- CALIBRATION_HARDENING precondition (R3 verdict §1 #5) NOW SATISFIED — both INV-15 + INV-09 upgraded; CALIBRATION_HARDENING gate at Week 13 unblocks
- Pattern fidelity: same BATCH-D + SIDECAR-2 CITATION_REPAIR pattern as INV-16/17/02/14/15

2 LOW caveats below.

## Pre-review independent reproduction

```
$ git log -2 --oneline
0a9ec93 INV-09 upgrade: register 8 existing relationship tests across 3 files (BATCH D pattern)
19e6e04 INV-15 follow-up: add 2 normalization tests + enrich CITATION_REPAIR comment

$ pytest --collect-only on 8 cited tests → 8 collected in 0.92s
$ pytest 8 cited tests → 8 passed in 1.88s
$ pytest 4-file critic baseline → 90 passed, 22 skipped in 1.86s
```

EXACT MATCH. ZERO regression. Cited tests resolve + pass + baseline preserved.

## ATTACK 1 — All 8 cited test paths resolve [VERDICT: PASS]

`pytest --collect-only` for 8 paths → "8 tests collected in 0.92s". No path errors. PASS.

## ATTACK 2 — All cited tests pass [VERDICT: PASS]

8 passed in 1.88s. Zero failures. PASS.

## ATTACK 3 — 4 enforcement surfaces accurately described [VERDICT: PASS]

CITATION_REPAIR comment claims 4 surfaces; independent verification:

| Surface | Cited location | Verified |
|---|---|---|
| (a) data_coverage status='MISSING' | src/state/data_coverage.py | ✓ L162-170 (transition rules) + L350 (eligibility query) + L402 (counter aggregation) |
| (b) trace_status='degraded_missing_vectors' | src/state/db.py:2164 | ✓ exact match at L2164 |
| (c) opportunity_loss_without_availability separation | src/state/db.py:4455+ | ✓ at L4459 within "separation" dict |
| (d) AuthorityTier CHECK constraint | src/state/collateral_ledger.py:46 | ✓ `CHECK (authority_tier IN ('CHAIN','VENUE','DEGRADED'))` exact match at L46 |

4/4 surfaces verified at cited line offsets. PASS.

## ATTACK 4 — Bidirectional grep: any tests missed? [VERDICT: PASS-WITH-LOW-REVISE]

`grep -rn "INV-09\|INV_09\|inv_09" tests/` returns **ZERO** by-name references. INV-09 tests verify the invariant by BEHAVIOR (status='MISSING' transitions, trace_status, opportunity_loss separation, AuthorityTier CHECK), not by docstring naming. Acceptable for this invariant — "missing data is first-class truth" is a runtime property, not a test-naming convention.

**Reverse-grep on enforcement surface (d) AuthorityTier='DEGRADED'**: dispatch noted 5 candidate files (test_unknown_side_effect.py, test_executor_command_split.py, test_live_execution.py, test_risk_allocator.py, test_executor_db_target.py). Independent investigation shows:

- test_unknown_side_effect.py:81 — uses `authority_tier="CLOB"` (NOT DEGRADED; doesn't test the invariant)
- test_executor_command_split.py:92 — same, CLOB only
- test_live_execution.py:117 — same, CLOB only
- test_risk_allocator.py:128 — same, CLOB only (L188 has HeartbeatHealth.DEGRADED, L223 has RiskLevel.DATA_DEGRADED — DIFFERENT enums, not authority_tier)
- test_executor_db_target.py:78 — same, CLOB only

5/5 candidate files use authority_tier as fixture data (CLOB) but DON'T test the DEGRADED state's behavior. Correct exclusion.

**However, BIDIRECTIONAL GREP FOUND ONE MISSED TEST**:

```
tests/test_collateral_ledger.py:290:def test_authority_tier_DEGRADED_when_chain_unreachable(conn):
tests/test_collateral_ledger.py:295:    assert snap.authority_tier == "DEGRADED"
```

`test_authority_tier_DEGRADED_when_chain_unreachable` directly tests surface (d) — verifies that when chain is unreachable, the snapshot's authority_tier IS "DEGRADED" (the third allowed enum value in the CHECK constraint). This is the BEHAVIOR-LEVEL test for surface (d) that the dispatch's 5 candidate files don't provide.

**LOW-REVISE-INV09-1**: include `tests/test_collateral_ledger.py::test_authority_tier_DEGRADED_when_chain_unreachable` in the cited list. Per BATCH-D pattern's own discipline ("register all existing tests that enforce the invariant"), this surface-(d) behavior test should be cited alongside the schema-CHECK reference. Non-blocking; recommend engineering executor add in next pass.

## ATTACK 5 — Excluded failing test correctly identified [VERDICT: PASS]

Dispatch §4 noted exclusion of `test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly`. Independent verification: this test is at `tests/test_runtime_guards.py:3779` (NOT test_db.py as the executor's note implied — minor path drift in note). Run independently:

```
$ pytest tests/test_runtime_guards.py::test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly
FAILED tests/test_runtime_guards.py::test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly
1 failed in 1.22s — assert row is not None / E assert None is not None
```

**Pre-existing failure CONFIRMED**. Excluding it from INV-09 cited list is correct (would have introduced a new failure into the YAML-cited set). Failure unrelated to INV-09 upgrade work.

PASS.

## ATTACK 6 — Methodology §5.X 6th case study cite [VERDICT: PASS-WITH-LOW-NUANCE]

CITATION_REPAIR comment cites "methodology §5.X case study; same as INV-15 + INV-16/17 reverts". Verified §5.X exists at L259 of methodology doc. Cite resolves correctly.

**LOW-NUANCE-INV09-1**: dispatch §6 asks "Methodology §5.X 6th case study cite valid?" — but §5.X is the SAME case study (BATCH D INV-16/17), not a "6th case study" per se. The comment says "same as INV-15 + INV-16/17 reverts" which implies INV-09 follows the established pattern, not that there's a new §5.X.6 sub-case-study. Cite is valid; dispatch wording slightly misleading. Non-blocking; comment text is accurate.

PASS-WITH-NUANCE.

## ATTACK 7 — INV-09 statement vs test semantic match [VERDICT: PASS]

INV-09 statement: *"Missing data is first-class truth."* + why: *"Opportunity loss and degraded reliability must be explicit facts, not log noise."*

Test verification mapping:
- test_log_opportunity_fact_preserves_missing_snapshot... → opportunity loss preserved as fact (matches "explicit facts not log noise")
- test_query_p4_fact_smoke_summary_reports_missing_tables_explicitly → missing data REPORTED EXPLICITLY (matches "first-class truth")
- test_query_p4_fact_smoke_summary_separates_layers → opportunity_loss separation per surface (c)
- test_R6_scanner_missing_flipped_to_written_by_append → MISSING is queryable state with transitions (surface a)
- test_R9_written_cannot_be_downgraded_to_missing → MISSING transition rules enforced (surface a)
- test_day0_missing_observation_is_pre_vector_traceable → trace_status='pre_vector_unavailable' (related to surface b)
- test_load_portfolio_rehydrates_chain_only_quarantine_fact_when_projection_degraded → degraded fact rehydrated (surface b)
- test_partial_stale_policy_uses_degraded_json_fallback → degraded fallback explicit (surface b)

8 tests cover surfaces (a)(b)(c). Surface (d) is covered ONLY via schema CHECK constraint (no behavior test cited). LOW-REVISE-INV09-1 above addresses this gap with the missed test.

Semantic match: STRONG for (a)(b)(c); SCHEMA-ONLY for (d) without LOW-REVISE-INV09-1 fix. PASS.

## ATTACK 8 — CALIBRATION_HARDENING precondition fully satisfied [VERDICT: PASS]

Per round3_verdict §1 #5 LOCKED: "INV-15 + INV-09 must be upgraded BEFORE CALIBRATION_HARDENING starts (~6-10h precondition)".

Status:
- INV-15 upgrade: COMPLETE + LOCKED (commit 19e6e04 with both critic LOW caveats addressed; my 14th cycle review APPROVE)
- INV-09 upgrade: COMPLETE pending this review (commit 0a9ec93)

Both halves of the precondition pair landed. CALIBRATION_HARDENING Week 13 gate UNBLOCKS pending operator confirmation.

PASS.

## ATTACK 9 — Cycle 5 erratum count tracking [VERDICT: PASS]

Per dispatch §8 (parallel to INV-15): engineering upgrade work, NOT post-implementation falsification of prior verdict. Does NOT count toward §5.Z3.1 ≥3 trigger.

Cycle 5 erratum count remains 2/3 (DRIFT-V1 + heredoc fix). §5.Z3.1 audit-first auto-trigger NOT activated.

PASS.

## ATTACK 10 — Cross-batch coherence with prior CITATION_REPAIR work [VERDICT: PASS]

INV-09 follows established pattern from INV-16/17/02/14/15:
- Same CITATION_REPAIR comment header format
- Same explicit cross-reference to methodology §5.X case study
- Same "schema-citation gap, not enforcement gap" framing
- Same author rationale: BATCH D-style coverage repair
- 4-surface enforcement analysis is RICHER than prior INV upgrades — INV-09 has multiple distinct enforcement points (data_coverage table + trace_status string + separation dict + CHECK constraint), which the comment correctly enumerates

Pattern fidelity confirmed across BATCH D → SIDECAR-2 → INV-15 → INV-09. Cross-batch coherence intact. The 4-surface enforcement analysis is a quality enrichment over single-surface upgrades.

PASS.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| LOW-REVISE-INV09-1 | LOW | `tests/test_collateral_ledger.py::test_authority_tier_DEGRADED_when_chain_unreachable` is a behavior test for surface (d) NOT in the cited list; bidirectional grep finds it via `authority_tier.*DEGRADED` reverse search | Add to `tests:` block in invariants.yaml INV-09 (raises cite count 8 → 9) | Engineering executor (next pass; non-blocking) |
| LOW-NUANCE-INV09-1 | LOW | Dispatch §6 wording "Methodology §5.X 6th case study" misleading — §5.X is the established case study, not a 6th sub-case; comment text is accurate ("same as INV-15 + INV-16/17 reverts") | None required; dispatch wording, not commit content | Note for future dispatch templates |

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The LOW-REVISE-INV09-1 caveat is a real bidirectional-grep find — I independently checked the 5 dispatch-mentioned candidate files for AuthorityTier=DEGRADED behavior tests, found they only use CLOB as fixture data (not testing DEGRADED), then ran a broader reverse grep `authority_tier.*DEGRADED` and discovered `test_collateral_ledger.py:290` which DIRECTLY tests surface (d)'s DEGRADED state behavior. This is exactly the BATCH-D-pattern coverage gap that the discipline says to surface.

I have NOT written "narrow scope self-validating" or "pattern proven without test." I engaged the strongest claim (4 enforcement surfaces + 8 tests + CALIBRATION_HARDENING precondition satisfied) at face value and verified each via:
- 8/8 pytest collect + run
- 4/4 source line-offset inspection of enforcement surfaces
- bidirectional grep INV-09 in tests/ (zero by-name) AND `authority_tier.*DEGRADED` (found 1 missed test)
- independent reproduction of the excluded failing test
- semantic-match mapping per surface

15th critic cycle in this run pattern (BATCH A-D + SIDECAR 1-3 + Tier 2 P1-P4 + Verdict Review + Stage 4 Review + INV-15 + INV-09). Same discipline applied throughout.

## Final verdict

**APPROVE-WITH-CAVEATS** — INV-09 upgrade lands cleanly; CALIBRATION_HARDENING precondition pair (INV-15 + INV-09) NOW SATISFIED; recommend engineering executor add the 1 missed surface-(d) behavior test in next pass for full BATCH-D-pattern coverage.

End INV-09 upgrade review.
End CALIBRATION_HARDENING precondition review pair.
