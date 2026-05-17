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
| **F32** | **Oracle bridge writer not scheduled → runtime permanently MISSING for every city** | **SEV-1** | **NEW (Run #9)** | scripts/bridge_oracle_to_calibration.py + cron | Run #9 | Run #9 |
| **F33** | **Daemon does not escalate on persistent oracle-MISSING-everywhere state**           | **SEV-2** | **NEW (Run #9)** | src/strategy/oracle_penalty.py + RiskGuard notify | Run #9 | Run #9 |
| **F34** | **Passive-only entry pricing in thin books → ~89% non-fill rate**                    | **SEV-3** | **NEW (Run #9)** | src/strategy/market_analysis.py + executor.py    | Run #9 | Run #9 |
| **F35** | **bridge_oracle_to_calibration.py unscheduled (operator-memory archeology confirms F32 is real and NEVER-RAN-IN-PROD)** | **SEV-1** | **NEW (Run #10)** | scripts/bridge_oracle_to_calibration.py + cron | Run #10 | Run #10 |
| **F36** | ~~Settlement live tables empty since 2026-05-07~~ → **DEFECT-INVALID-PROVENANCE (Run #11)**: Run #10 queried wrong DB; harvester writes to zeus-forecasts.db (3634 VERIFIED rows since 5/7, latest 2026-05-17T05:46Z). See RUN_11. | ~~SEV-1~~ | **RETRACTED (Run #11)** | src/ingest/harvester_truth_writer.py:9,395 → zeus-forecasts.db | Run #10 | Run #11 |
| **F37** | **calibration-transfer-eval runs weekly but iterates 0 models (input table empty)** | **SEV-2** | **NEW (Run #10)** | scripts/evaluate_calibration_transfer_oos.py + launchd | Run #10 | Run #10 |
| **F38** | ~~calibration_pairs_v2 = 0 rows~~ → **DEFECT-INVALID-PROVENANCE (Run #11)**: actual count in zeus-forecasts.db is **91,040,450 rows**; zero in zeus-world.db is correct post-K1 archive. See RUN_11 + new F41. | ~~SEV-1~~ | **RETRACTED (Run #11)** | (retracted) | Run #10 | Run #11 |
| **F39** | **com.zeus.calibration-transfer-eval plist comment lies ("DO NOT LOAD") vs reality (loaded + running)** | **SEV-3** | **NEW (Run #10) Cat-N** | ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist | Run #10 | Run #10 |

| **F40** | **scripts/bridge_oracle_to_calibration.py hardcodes `state/zeus-world.db` after PR #114 K1 split moved settlements to zeus-forecasts.db; bridge would emit `{}` even when scheduled** | **SEV-1** | **NEW (Run #11)** Cat-J + Cat-K | scripts/bridge_oracle_to_calibration.py:71 | Run #11 | Run #11 |
| **F41** | **scripts/evaluate_calibration_transfer_oos.py uses `get_world_connection`; reads dead calibration_pairs_v2 (0 rows in world DB; 91M rows in forecasts DB). Live log regression 2026-05-10→2026-05-17 confirms** | **SEV-1** | **NEW (Run #11)** Cat-J | scripts/evaluate_calibration_transfer_oos.py:684 | Run #11 | Run #11 |
| **F42** | **META: PR #114 K1 split migrated writers but did NOT sweep ~30 reader callers using `get_world_connection` against forecast-class tables. F40 + F41 are confirmed; ~10 more files suspect.** | **SEV-1** | **NEW (Run #11)** Cat-K + Cat-J META | src/, scripts/ (~30 callers) | Run #11 | Run #11 |

## Run #11 retraction note (2026-05-17)
F36 + F38 retracted as DEFECT-INVALID-PROVENANCE. Root cause of Run #10's error: the K1 forecast DB split (PR #114, commit `eba80d2b9d`, merged 2026-05-14) MOVED 7 forecast-class tables off `zeus-world.db` and renamed source tables to `_archived_2026_05_11`. Run #10 queried `zeus-world.db` (post-archive empty shell) and `zeus_trades.db` (settlements never lived there) but not `zeus-forecasts.db` (the actual K1 target). Two real regressions surfaced: F40, F41 — reader-side callers that PR #114 missed. F42 frames the meta — likely ~10 more silent reader regressions exist.
