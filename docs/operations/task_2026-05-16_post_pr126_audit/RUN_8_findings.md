# Run #8 — New findings (F28+)

## F28 — META: dual-index numbering inconsistency between v1 brief and v2 reference

**Severity**: SEV-2 (process)
**Category**: Cat-N (audit-package-coherence)
**Evidence**:
- v1 (operator brief and FROZEN package `docs/operations/task_2026-05-16_deep_alignment_audit/`): F1…F25 + add-on F26, F27 introduced in Runs 5-6.
- v2 (`docs/operations/task_2026-05-16_post_pr126_audit/FINDINGS_REFERENCE_v2.md`): renumbered as F1…F24 with DIFFERENT mappings (v1.F25 ≠ v2.F25).
**Impact**: any operator alternating between docs gets confused about which F-number refers to which defect. Cross-references in `STATUS.md`, `RUN_*_findings.md`, and `LEARNINGS.md` become ambiguous.
**Root cause**: v2 was created in Run #7 with locally-sequential numbering rather than as a delta-on-v1. No reconciliation table was attached.
**Call-to-action** (this run): add a mapping table at the top of `FINDINGS_REFERENCE_v2.md` `v1.F# ↔ v2.F#`. Going forward, all new findings get a single globally-unique F-number with no renumbering.
**Verification**: open `FINDINGS_REFERENCE_v2.md` after this run; the first H2 section MUST be `## Numbering reconciliation` with the mapping.

---

## F29 — `REDEEM_REVIEW_REQUIRED` is in `_TERMINAL_STATES` but NOT excluded from the `ux_settlement_commands_active_condition_asset` UNIQUE INDEX

**Severity**: SEV-2 (NOT Karachi 5/17 blocking)
**Category**: Cat-K (design-decision-incomplete)
**Evidence**:
- `src/execution/settlement_commands.py:57-59` UNIQUE index excludes only `('REDEEM_CONFIRMED','REDEEM_FAILED')`.
- `src/execution/settlement_commands.py:100-104` `_TERMINAL_STATES = {REDEEM_CONFIRMED, REDEEM_FAILED, REDEEM_REVIEW_REQUIRED}` — REVIEW_REQUIRED IS terminal in the runtime classification but not from the index's perspective.
**Impact**: a settlement that reached REVIEW_REQUIRED (genuinely terminal per code) STILL blocks a re-issued settlement command for the same `(condition_id, market_id, payout_asset)` triple. Operator running a recovery script gets `IntegrityError: UNIQUE constraint failed`. Forces a manual `DELETE FROM settlement_commands WHERE state='REDEEM_REVIEW_REQUIRED'` workaround.
**Karachi 5/17 impact**: NONE (one position per triple; no re-issue path).
**Call-to-action** (post-Karachi): either (a) add `REDEEM_REVIEW_REQUIRED` to the index exclusion list (treat as terminal for blocking), or (b) document why REVIEW_REQUIRED blocks (e.g., to prevent operators from masking review-required incidents by replaying).
**Probe to settle**: `git log -p main -- src/execution/settlement_commands.py | grep -B5 -A5 "REVIEW_REQUIRED"` to see if a prior commit message explains the intent.

---

## F30 — `scripts/migrations/` runner DOES NOT enforce `last_reviewed` header drift

**Severity**: SEV-3 (process)
**Category**: Cat-N (audit-package-coherence)
**Evidence**: `scripts/migrations/__init__.py` header says `last_reviewed=2026-05-16; last_reused=never`. Per the user memory `code-provenance` antibody, every reuse should update `last_reused:`. There is no automated check.
**Impact**: future migrations added as siblings inherit the bare-runner pattern without a fresh review. The provenance-rule from `CLAUDE.md` is not mechanically enforced.
**Call-to-action** (post-Karachi): part of PR-L (F23 fix). The new `scripts/migrations apply` CLI should also `grep -L "last_reviewed=" scripts/migrations/2*.py` and refuse to run if any migration file lacks the header.

---

## F31 — `market_events_v2` reader-side audit gap

**Severity**: SEV-2 (deferred from F19 INVESTIGATE-FURTHER)
**Category**: Cat-J (silent-cross-DB-read)
**Evidence**: F19 confirmed 3-DB write divergence (10541/7953/2112 rows). Reader-side enumeration was NOT run this session.
**Probe (10-min)**: `git -C $zeus grep -n "FROM market_events_v2\|JOIN market_events_v2" main -- src/ scripts/` then for each match, identify the connection it uses (forecasts? trades? attached?).
**Call-to-action**: NEEDS-CODE post-Karachi. Filed alongside F19 fix (PR-J).

---

## Summary

3 new structural findings (F28 meta-numbering, F29 review-required blocking, F30 migration header enforcement) + 1 deferred reader-trace (F31).
None block Karachi 5/17.
F28 is fixed in this same Run #8 by adding the reconciliation table to FINDINGS_REFERENCE_v2.md.
