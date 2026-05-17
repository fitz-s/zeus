# FINDINGS_REFERENCE v2 — post-PR-126 master index

Baseline: main HEAD `9259df3e9c` (2026-05-17). PR #126 (cascade liveness), PR #130 (ref/authority docs), PR #132/#133 (db_writer_lock Phase 0/0.5/1, Track A.6 daemon retrofit).
Predecessor (frozen, SUPERSEDED for master-index reading): `../task_2026-05-16_deep_alignment_audit/FINDINGS_REFERENCE.md`.

Severity scale: SEV-0 (ship blocker / money loss / silent corruption), SEV-1 (high — likely data integrity, masking risk), SEV-2 (medium — process/discipline), SEV-3 (low — hygiene).

## Numbering reconciliation (added Run #8)

v1 (FROZEN brief, `../task_2026-05-16_deep_alignment_audit/`) and v2 (this doc) DO NOT share F-numbering — same number can refer to entirely different defects. This document remains canonical for the post-PR-126 package; cross-walk to v1 numbers is in `RUN_8_resolution_sweep.md`. Going forward, all new findings receive a globally-unique F-number with no renumbering. See F28 in `RUN_8_findings.md` for the meta-defect.

| F#  | Title                                                                  | Sev   | Status                  | Owner module                 | First seen | Last verified |
|-----|------------------------------------------------------------------------|-------|-------------------------|------------------------------|------------|---------------|
| F1  | ZEUS_FORECASTS_DB legacy path                                          | SEV-1 | FIXED                   | src/state/db.py              | Run #1     | Run #7        |
| F2  | decision_id NULL on selection_hypothesis_fact (superseded by F25)      | SEV-1 | WORSE                   | src/selection/...            | Run #1     | Run #7        |
| F3  | unit-system co-mingling                                                | SEV-1 | OPEN                    | src/signal/, src/calibration | Run #2     | Run #5        |
| F4  | settle_status ghost column                                             | SEV-2 | FIXED                   | src/state/db.py              | Run #2     | Run #7        |
| F5  | collateral_ledger raw sqlite3.connect                                  | SEV-2 | OPEN-acknowledged       | src/state/collateral_ledger.py | Run #2   | Run #7        |
| F6  | candidate_fact orphan rows                                             | SEV-2 | OPEN                    | src/selection/               | Run #2     | Run #5        |
| F7  | order_intent / venue_command lineage                                   | SEV-1 | OPEN                    | src/execution/               | Run #3     | Run #5        |
| F8  | observations_v2 dual-write window                                      | SEV-2 | OPEN                    | src/data/, src/state/db.py   | Run #3     | Run #7        |
| F9  | calibration_pairs_v2 not promoted                                      | SEV-2 | OPEN-progress           | scripts/, src/calibration    | Run #3     | Run #7        |
| F10 | risk_state.db separate-process drift                                   | SEV-2 | OPEN                    | src/riskguard/               | Run #3     | Run #5        |
| F11 | BulkChunker yields LIVE only at chunk boundary                         | SEV-2 | OPEN-progress           | src/state/db_writer_lock.py  | Run #4     | Run #7        |
| F12 | migration script idempotency unverified                                | SEV-2 | OPEN                    | scripts/migrations/          | Run #4     | Run #6        |
| F13 | settlement_commands ux index excludes only 2 terminals (→ F27)         | SEV-1 | NEW-SCOPE               | src/state/db.py              | Run #4     | Run #7        |
| F14 | redeem cascade liveness contract missing                               | SEV-0 | FIXED (PR #126)         | architecture/                | Run #4     | Run #7        |
| F15 | chain reconciliation skip_voiding interplay                            | SEV-1 | OPEN                    | src/state/chain_reconciliation.py | Run #4 | Run #5      |
| F16 | redeem auto-execution gate missing operator path                       | SEV-0 | FIXED (PR #126)         | src/execution/settlement_commands.py | Run #4 | Run #7  |
| F17 | forecasts.db user_version drift risk                                   | SEV-1 | FIXED-held              | src/state/db.py              | Run #5     | Run #7        |
| F18 | INSERT OR IGNORE silent loss                                           | SEV-2 | OPEN                    | src/data/market_scanner.py   | Run #5     | Run #6        |
| F19 | collateral ledger schema not in registry                               | SEV-3 | OPEN                    | architecture/                | Run #5     | Run #5        |
| F20 | position_lots reconciliation                                           | SEV-2 | OPEN                    | src/state/                   | Run #5     | Run #5        |
| F21 | legacy observation_instants writer active                              | SEV-2 | OPEN                    | src/data/hourly_instants_append.py:229 | Run #6 | Run #7  |
| F22 | market_events_v2 dual-write via raw sqlite3.connect                    | SEV-2 | OPEN-acknowledged       | src/data/market_scanner.py:610 | Run #6   | Run #7        |
| F23 | migration runner architecturally bare (no ledger)                      | SEV-1 | OPEN                    | scripts/migrations/          | Run #6     | Run #7        |
| F24 | decision_id NULL accelerated 693→1518 (superseded by F25)              | SEV-1 | EXPANDED → F25          | src/selection/, src/signal/  | Run #6     | Run #7        |
| **F25** | **Triple-NULL systemic snapshot-write failure (3 fact tables)**     | **SEV-0** | **NEW**             | src/selection/, src/opportunity/, src/signal/ | Run #7 | Run #7 |
| **F26** | **Two-truth SQLITE_CONNECT_ALLOWLIST divergence**                   | **SEV-2** | **NEW**             | src/state/db_writer_lock.py + tests/conftest.py | Run #7 | Run #7 |
| **F27** | **REDEEM_OPERATOR_REQUIRED unique-index lockout (PR-126 review gap)** | **SEV-1** | **NEW**             | src/state/db.py + src/execution/settlement_commands.py | Run #7 | Run #7 |
| **F28** | **META: dual-index numbering inconsistency v1↔v2**                  | **SEV-2** | **NEW (Run #8)**    | docs/operations/task_2026-05-16_*                       | Run #8 | Run #8 |
| **F29** | **REDEEM_REVIEW_REQUIRED not excluded from UNIQUE INDEX (sibling of F27)** | **SEV-2** | **NEW (Run #8)** | src/execution/settlement_commands.py                    | Run #8 | Run #8 |
| **F30** | **Migration runner does not enforce last_reviewed header drift**    | **SEV-3** | **NEW (Run #8)**    | scripts/migrations/                                     | Run #8 | Run #8 |
| **F31** | **market_events_v2 reader-side audit gap (deferred from v1.F19)**   | **SEV-2** | **NEW (Run #8)**    | src/, scripts/                                          | Run #8 | Run #8 |

## Carry-forward note
Old `FINDINGS_REFERENCE.md` in the predecessor package now bears a "SUPERSEDED" header pointing here. Run-narrative files (RUN_1…RUN_6_findings.md) remain canonical for their own narrative content.
