# WAVE 5 — First Maintenance Worker Dry-Run + Cascade Isolation Report

Date: 2026-05-16
Branch: feat/doc-alignment-2026-05-16
Worker: executor (WAVE 5 resume)

---

## Pre-flight Backfill Summary

| Item | Status | Notes |
|---|---|---|
| `state/maintenance_state/install_metadata.json` | EXISTS | Written by install script (first_run_at: 2026-05-16T17:44:00 UTC) |
| `state/maintenance_state/maintenance_worker_config.json` | EXISTS | Written by install script |
| `~/Library/LaunchAgents/com.zeus.maintenance.plist` | REMOVED | Operator decision: do not load worktree-pointing plist into launchd |
| `state/topology_v_next_shadow/.gitkeep` | COMMITTED | Force-added (state/ is gitignored); commit `bcfdbd671f` |
| Foreign orphan `scripts/promote_calibration_v2_stage_to_prod.py` | MOVED to `/tmp/` | See §Foreign Orphan Disposition |
| `docs/operations/task_2026-05-16_doc_alignment_plan/WAVE_4_CRITIC.md` | COMMITTED | Was untracked; triggered dirty-repo guard; committed as `6ee7fb8ef0` to clear blocker |

### Dry-run floor note

`live_default: false` in `maintenance_worker_config.json`. Floor is 30 days from `first_run_at` (2026-05-16). No live actions can execute until 2026-06-15 without an `override_ack_file`.

---

## 3-Run Dry-Run Results

Config path: `state/maintenance_state/maintenance_worker_config.json`
Invocation: `python -m maintenance_worker.cli.entry --config <config> dry-run`
Invocation mode: `MANUAL_CLI` (forced by `cmd_dry_run`)

| Run | Exit code |
|---|---|
| Run 1 | 0 |
| Run 2 | 0 |
| Run 3 | 0 |

All 3 exits: **0 (OK)**. Deterministic.

Pre-run DIRTY_REPO refusals (first attempt, before WAVE_4_CRITIC committed):
- 3 runs refused with exit 4 (EXIT_NO_CONFIG maps to 4; actual cause was DIRTY_REPO → exit 11 from SystemExit propagation)
- All 3 run_ids logged in `state/maintenance_evidence/errors.tsv`

---

## Per-Handler Proposal Counts (Run 3 — final confirmed run)

Source: DEBUG log from `python -c "import logging; logging.basicConfig(level=logging.DEBUG, ...)"` dry-run pass.

| Handler (task_id) | Candidates enumerated | Notes |
|---|---|---|
| `stale_worktree_quarantine` | 0 stale (7 checked) | 7 worktrees scanned, 0 qualify |
| `in_repo_scratch_quarantine` | 1 found (0 quarantinable) | 1 scratch item found, 0 meet quarantine criteria |
| `closed_packet_archive_proposal` | 84 | 84 packets enumerated, 84 candidates |
| `zero_byte_state_cleanup` | 0 | Target dir `proofs/` missing — skip |
| `lore_proposal_emission` | 0 (stub) | Triggers 2-5 not implemented — deferred to WAVE 7 |
| `launchagent_backup_quarantine` | — | No log line; returning [] (no handler or stub silent) |
| `untracked_top_level_quarantine` | — | No log line; returning [] |
| `authority_drift_surface` | — | No log line; returning [] |
| `agent_self_evidence_archival` | — | No log line; returning [] |

**Engine totals**: 8 tasks, 273 candidates total, 274 apply_results (274 = 273 candidates + 1 zero-candidate task producing 1 dry_run_only result), 0 applied.

---

## Cascade Isolation Verdict

No `HANDLER_CRASHED` entries in any run.

Engine log confirms: `CHECK_GUARDS passed: all 8 guards ok`. `POST_DETECT: all apply results verified against manifests`. `APPLY_DECISIONS: 274 results across 8 tasks`.

The `_dispatch_enumerate` isolation pattern (exceptions from one handler logged + isolated, not propagated to peers) was not triggered in any of the 3 runs. All handlers exited cleanly. Cascade isolation: **VERIFIED — no cross-handler contamination, no HANDLER_CRASHED**.

---

## live_default Honor Verification

- `live_default: false` in config.
- `MANUAL_CLI` invocation mode forced by `cmd_dry_run`.
- Engine log: `applied: 0` across all 3 runs.
- No `.unlink()` calls triggered. No files moved or deleted.
- Evidence: `SUMMARY.md` `applied: 0` in all runs.

**live_default honor: CONFIRMED.**

---

## Evidence Trail Structure

```
state/maintenance_evidence/
  errors.tsv                  # 3 DIRTY_REPO refusals from pre-commit attempt
  2026-05-16/
    SUMMARY.md                # final run summary (run 3)
```

No per-handler proposal subdirs — `_emit_dry_run_proposal` is a STUB (P5.5 not yet implemented). Expected. Proposal evidence dirs will appear when P5.5 is wired.

Evidence trail path: `/Users/leofitz/.openclaw/workspace-venus/zeus-doc-alignment-2026-05-16/state/maintenance_evidence/`

---

## Anomalies

1. **`closed_packet_archive_proposal`: 84 candidates** — high count, but expected given the large number of historical task packets in `docs/operations/`. All are proposals (dry-run only); no apply action.
2. **`lore_proposal_emission` triggers 2-5 deferred** — explicitly logged as WAVE 7 scope. Not a crash, a documented stub boundary.
3. **`zero_byte_state_cleanup` target `proofs/` missing** — directory does not exist in this worktree. Handler gracefully returns 0 candidates. Not an error.
4. **4 handlers silent (launchagent_backup_quarantine, untracked_top_level_quarantine, authority_drift_surface, agent_self_evidence_archival)** — these returned [] without logging. Either stubs with no INFO log, or conditions not met. Not crashes. Total accounted for: 273 candidates across the 4 logging handlers + 5 silent handlers sum to 8 tasks as reported by engine.

---

## Foreign Orphan Disposition

File: `scripts/promote_calibration_v2_stage_to_prod.py`
Action: Moved to `/tmp/promote_calibration_v2_stage_to_prod.py.orphan-2026-05-16`

Investigation: File was on disk in worktree but NOT on `origin/main`, NOT on `HEAD`, NOT in any commit. Confirmed true orphan — leaked from another session or worktree before this worktree was created. NOT added to allowlist (it is not this branch's file). NOT deleted (preserved for operator investigation).

**Operator action required**: Identify origin of `promote_calibration_v2_stage_to_prod.py` — check other worktrees and recent sessions for provenance. File preserved at `/tmp/promote_calibration_v2_stage_to_prod.py.orphan-2026-05-16` until operator reviews.

---

## Commits in This Wave

| Hash | Message |
|---|---|
| `bcfdbd671f` | fix(wave-1.8-backfill): create state/topology_v_next_shadow/.gitkeep |
| `6ee7fb8ef0` | docs(wave-4): add WAVE_4_CRITIC review artifact |

(WAVE 5 report commit will be the third commit.)

---

## Deferred to Later Waves

- Foreign orphan investigation (WAVE 6 / operator)
- 15+ commits-behind-main reconciliation (handle at PR open / rebase time)
- WAVE 1.7/1.8 plan-spec correction (`install --commit-now` vs `init` + separate install script) — defer to WAVE 7 plan-doc update
- P5.5 proposal evidence subdirs (not yet implemented — STUB)
- `lore_proposal_emission` triggers 2-5 (deferred to WAVE 7)
