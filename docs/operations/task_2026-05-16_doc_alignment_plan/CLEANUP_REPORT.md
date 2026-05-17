# 大扫除 Cleanup Report — 2026-05-16

**Executor:** Claude Sonnet 4.6  
**Worktree:** `zeus-doc-alignment-2026-05-16`  
**Canonical:** `/Users/leofitz/.openclaw/workspace-venus/zeus`  
**Time budget:** 45 min

---

## Per-Phase Summary

| Phase | Action | Outcome |
|-------|--------|---------|
| A | Delete gitignored state checksums + writer-locks + tmp/ | DONE |
| B | LIVE_TRADING_LOCKED_2026-05-04.md disposition | DELETED_BY_MAIN_PR#122 (our archive intent superseded during rebase) |
| C | Canonical /zeus cleanup (deprecated raw, zeus_trades.db, log rotation) | PARTIAL — see details |
| D | Deeper subdir audit | REPORT ONLY — no action taken |

---

## PHASE A — Gitignored Disk Cruft

**Files deleted from worktree `state/`:**

| File | Reason |
|------|--------|
| `state/zeus-world.db.pre-migration-batch_2026-04-24.sha256` | Pre-migration checksum, no longer needed |
| `state/zeus-world.db.pre-pb_2026-04-23.md5` | Pre-migration checksum |
| `state/zeus-world.db.pre-pb_2026-04-23.sha256` | Pre-migration checksum |
| `state/zeus-world.db.pre-pe_2026-04-23.md5` | Pre-migration checksum |
| `state/zeus-world.db.pre-pe_2026-04-23.sha256` | Pre-migration checksum |
| `state/zeus-world.db.pre-pf_2026-04-23.md5` | Pre-migration checksum |
| `state/zeus-world.db.pre-pf_2026-04-23.sha256` | Pre-migration checksum |
| `state/zeus-world.db.pre-pg_2026-04-23.md5` | Pre-migration checksum |
| `state/zeus-world.db.pre-pg_2026-04-23.sha256` | Pre-migration checksum |
| `state/zeus-world.db.pre-reopen2_2026-04-24.sha256` | Pre-migration checksum |
| `state/zeus-world.db.writer-lock.bulk` | Stale writer-lock |
| `state/zeus-forecasts.db.writer-lock.bulk` | Stale writer-lock |

**`tmp/` handling:**
- `tmp/ecmwf_open_data_2026-05-11_00z_mx2t6_high.stderr.txt` — transient HTTP error log, DELETED
- `tmp/phase2_post_extract_then_stop.sh` — cloud ingest orchestration tool (TIGGE autochain + ECMWF JSON transfer + Platt refit pipeline). **MOVED** to `/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/phase2_post_extract_then_stop.sh` (canonical location), then removed from worktree `tmp/`.
- `tmp/` directory removed.

**Verification:** `ls state/zeus-world.db.pre-*` → no matches; `ls state/*.writer-lock.bulk` → no matches; `ls tmp/` → no such directory.

---

## PHASE B — LIVE_TRADING_LOCKED_2026-05-04.md

**Decision: ARCHIVED (stale)**

**Evidence gathered:**

1. **DB control_overrides audit:** The operator lock row (`override_id='operator:tigge_12z_gap:LIVE_UNSAFE_2026_05_04'`, precedence=200) has `effective_until='2026-05-10 10:35:21'` and reason=`EXPIRED_2026-05-10_PLATT_12Z_RETRAINED_195_ACTIVE_BY_BLOCKER_SUPERSESSION_DOC_LANDED`. The lock was explicitly expired 2026-05-10 after Platt 12z retraining completed.

2. **Plist status:** `com.zeus.live-trading.plist` (non-.bak) is present in `~/Library/LaunchAgents/` and loaded via `launchctl list` (PID 86141, though last exit was SIGTERM). Trading daemon has been re-armed since the lock.

3. **Recent commits:** `src/execution/` and `src/control/` have 18 commits since 2026-05-05, including `feat(live): add live order e2e verification` — confirming active live trading development post-lock.

**Action:** `git mv LIVE_TRADING_LOCKED_2026-05-04.md docs/operations/archive/2026-Q2/LIVE_TRADING_LOCKED_2026-05-04.md`

Post-rebase reconciliation 2026-05-16: PR #122 landed before our merge and explicitly deleted `LIVE_TRADING_LOCKED_2026-05-04.md` from the root. The rebase conflict was resolved by accepting main's deletion. Net effect matches our archive intent (file no longer at root), but no archive copy preserved at `docs/operations/archive/2026-Q2/`. If historical content is needed, recover from git history via `git show <pre-rebase-SHA>:LIVE_TRADING_LOCKED_2026-05-04.md`.

**Commit:** see commit hash below.

---

## PHASE C — Canonical /zeus Cleanup

### C.1 — raw.deprecated.1778441727
**Verdict: ALREADY GONE.** The target `raw.deprecated.1778441727` does not exist in `/Users/leofitz/.openclaw/workspace-venus/zeus/`. No action needed. The `raw.deprecated.` directory that does exist contains only `tigge_ecmwf_ens_mn2t6_localday_min/` (active data, do not touch).

### C.2 — zeus_trades.db
**Verdict: KEEP. Active references found.**

`git grep -l zeus_trades src/ scripts/` returns 10 files:
- `scripts/check_data_pipeline_live_e2e.py`
- `scripts/check_live_order_e2e.py`
- `scripts/healthcheck.py`
- `scripts/replay_correctness_gate.py`
- `scripts/state_census.py`
- `scripts/topology_doctor_data_rebuild_checks.py`
- `scripts/venus_sensing_report.py`
- `scripts/verify_truth_surfaces.py`
- `src/execution/command_recovery.py`
- `src/state/connection_pair.py`

`state/zeus_trades.db` is actively referenced — do NOT delete.

### C.3 — logs/zeus-ingest.err (125 MB) Log Rotation
**Verdict: BLOCKED. Live process holds file.**

`lsof` confirms PID 34316 (`com.zeus.data-ingest`, `com.zeus.data-ingest` launchd daemon) holds `zeus-ingest.err` open with FD=2 (stderr). Cannot rotate without stopping the process. Operator action required:

```bash
# Option 1: Stop ingest, rotate, restart
launchctl stop com.zeus.data-ingest
mv logs/zeus-ingest.err logs/zeus-ingest.err.rotated-2026-05-16
gzip logs/zeus-ingest.err.rotated-2026-05-16
touch logs/zeus-ingest.err && chmod 644 logs/zeus-ingest.err
launchctl start com.zeus.data-ingest

# Option 2: copytruncate (if logrotate available) — no process restart needed
# cp logs/zeus-ingest.err logs/zeus-ingest.err.rotated-2026-05-16 && > logs/zeus-ingest.err
```

---

## PHASE D — Deeper Subdir Audit (REPORT ONLY — no actions taken)

### evidence/ old files
`find evidence/ -type f -mtime +30 | wc -l` → **0 files** older than 30 days. No action needed.

### docs/operations/ task packets >7 days old and unarchived
**59 unarchived task directories** from 2026-05-02 through 2026-05-08 exist. The 28 already-archived tasks (`.archived` suffix) serve as precedent.

**Recommended for operator-approved archive batch (WAVE 2 pattern: `git mv` + update INDEX.md):**

High-signal candidates (complete work, closed tasks):
- `task_2026-05-02_live_entry_data_contract` — PLAN v2/v3/v4 + PHASE0 evidence, rollout complete per commits
- `task_2026-05-03_ddd_implementation_plan.md` — single .md file, design doc
- `task_2026-05-04_zeus_may3_review_remediation` — large task, remediation complete per PR merge history
- `task_2026-05-05_object_invariance_wave{5,6,7,8,11-21}` — 14 wave PLAN.md dirs, waves shipped in PR #90
- `task_2026-05-05_topology_noise_repair` — PLAN.md, topology shipped in PR #71
- `task_2026-05-06_calibration_quality_blockers` — PLAN + QUARANTINE_LEDGER
- `task_2026-05-06_hook_redesign` — PLAN.md, hook redesign shipped PR #71
- `task_2026-05-06_topology_redesign` — large with ADR subdirs, topology v2 shipped PR #71/#72
- `task_2026-05-07_*` (6 dirs) — object invariance waves 24-26, hook v2, nav topology v2, recalibration
- `task_2026-05-08_*` (28 dirs) — object invariance waves 27-42 + various run/task docs

**Operator decision required before archiving any of the above.** Recommend single batch `git mv` of completed wave dirs to `docs/operations/archive/2026-Q2/` in next cleanup session.

### src/, tests/, architecture/ — unexpected files
`find src/ tests/ architecture/ -type f \( -name "*.bak" -o -name "*_old*" -o -name "*.copy" \)` → **0 results**. No stale dev artifacts found.

---

## Operator Decisions Surfaced

| Item | Decision needed |
|------|----------------|
| `logs/zeus-ingest.err` rotation (125 MB) | Stop `com.zeus.data-ingest`, rotate, restart — or use copytruncate |
| 59 unarchived task dirs (2026-05-02 to 2026-05-08) | Approve batch archive to `docs/operations/archive/2026-Q2/` |
| `zeus_trades.db` retention | Confirmed KEEP (active references in 10 files) |

---

## Commit Hashes

| Phase | Commit |
|-------|--------|
| A+B (checksums + writer-locks + archive LIVE_TRADING_LOCKED) | `c114f3d6da` |
| Cleanup report | TBD after commit |
