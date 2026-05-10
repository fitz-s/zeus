# TASK — Post-merge full chain (4 phases)

Created: 2026-05-08
Authority: operator directive 2026-05-08 — explicit authorization for `launchctl` + plist load + production DB writes by this dispatch
Phase: implementation (sonnet)

## GOAL

Execute the full chain enabled by today's 4 merged PRs (#94/#95/#96/#98):
- **Phase A**: Restart data daemon + load calibration plist + trigger D+10 download
- **Phase B**: Verify 100 BLOCKED resolves
- **Phase C**: Build #263 SOURCE_DISAGREEMENT isolation layer
- **Phase D**: Backfill 317 already-quarantined London °F rows

Operator explicitly authorized: launchctl operations, plist load, ECMWF download trigger, production DB writes for backfill. Normal operator-only constraints suspended for this dispatch.

## PHASE A — Operator actions (mechanical)

1. `git checkout main && git pull origin main` (pick up the 4 merged PRs)
2. Restart data-ingest daemon:
   - `launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist`
   - `launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist`
3. Verify daemon alive:
   - `launchctl list | grep zeus`
   - Check `state/daemon-heartbeat-ingest.json` mtime in last 5 min
   - Tail `logs/zeus-ingest.err` for clean start (no Python tracebacks)
4. Load calibration-transfer-eval plist:
   - `launchctl load ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist`
   - Verify in `launchctl list`
5. Trigger ECMWF D+10 download covering steps 228-252 for 2026-05-13 + 2026-05-14:
   - Find the right invocation (likely `python3 scripts/download_ecmwf_open_ens.py --target-date 2026-05-13 --target-date 2026-05-14` or via daemon-managed schedule). If daemon now picks up automatically with new STEP_HOURS=282, no manual trigger needed; document.
   - If unsure of args, STOP and report — do NOT guess.

Acceptance: daemon running with new code, plist loaded, download invoked or scheduled.

## PHASE B — Verify 100 BLOCKED resolution

1. Wait for download to complete (may be 10-30 min). Tail `logs/zeus-ingest.err` for completion signal or check `state/source_health.json`.
2. Run readiness re-evaluation: `python3 scripts/reevaluate_readiness_2026_05_07.py --apply`
3. SQL: `SELECT eligibility, blocked_reason, COUNT(*) FROM readiness_state GROUP BY 1,2 ORDER BY 3 DESC`
4. Acceptance: `BLOCKED + SOURCE_RUN_HORIZON_OUT_OF_RANGE` count drops 100 → close to 0. Document any remaining (likely cycle-06/18 short-horizon edge cases).

If download still running when you reach this, proceed to Phase C in parallel and circle back.

## PHASE C — Build #263 SOURCE_DISAGREEMENT isolation layer

Authority: haiku audit verdict ISOLATION_MISSING. Currently settlement-vs-observation source disagreement is treated as generic `harvester_live_obs_outside_bin`. Build a structured `SOURCE_DISAGREEMENT` quarantine reason at the Harvester layer.

### Branch
`fix/263-source-disagreement-isolation-2026-05-08` off main

### EDIT_ALLOWED_PATHS
- `src/contracts/settlement_semantics.py`
- `src/ingest/harvester_truth_writer.py`
- `src/contracts/semantic_types.py` (if quarantine_reason enum lives there)
- `migrations/` (if quarantine_reason needs new value in CHECK constraint — verify if SQLite enforces)
- `tests/test_settlement_semantics*.py`
- `tests/test_harvester*.py`
- `architecture/test_topology.yaml` (register new tests)

### OUT_OF_SCOPE_PATHS
- `src/data/**` (forecast feed)
- `src/calibration/**`
- `src/state/db_writer_lock.py` (no ALLOWLIST adds)

### Implementation
1. Detect at harvester ingestion: when both `settlement_value` (UMA-resolved) and `observation_value` (WU-derived) are present, AND they differ by ≤1°C tolerance, AND the bin check passes for one but fails for the other → emit `harvester_source_disagreement_within_tolerance`.
2. Distinguish from genuine outside-bin: if BOTH sources fall outside the bin, keep current `obs_outside_bin`.
3. Distinguish from null-bin: existing `harvester_live_no_bin_info` (PR #95) takes precedence when bin is null.
4. Tolerance default: ±1°C. Make config-driven (read from `config/settings.json::settlement.disagreement_tolerance_celsius` with default 1.0). If config missing, use default.
5. New tests:
   - both agree + bin contains → no quarantine
   - both within ±1°C, only one passes bin → SOURCE_DISAGREEMENT
   - both outside bin → obs_outside_bin
   - both null bin → no_bin_info

### Open question
Whether to retroactively re-classify existing `obs_outside_bin` rows that fit the SOURCE_DISAGREEMENT pattern. **Default: NO retroactive** — separate operator decision. Document in RUN.md.

## PHASE D — Backfill 317 London °F rows

Per PR #98 RUN.md backfill spec: existing 317 rows quarantined under old logic need to be re-resolved with the now-deployed °F→°C conversion.

### Branch
Either bundle into Phase C's PR (preferred for atomicity if scope allows) OR new branch `fix/317-london-backfill-2026-05-08` off main.

### EDIT_ALLOWED_PATHS (extends Phase C)
- `scripts/backfill_london_f_to_c_2026_05_08.py` (new file)

### Implementation
1. Find affected rows: `SELECT * FROM settlements_v2 WHERE city='London' AND quarantine_reason='harvester_live_obs_outside_bin'` (additional filter if needed: target_date in pre-2026 range or specific market patterns).
2. For each row: re-derive bin_unit from market metadata (use the `_detect_bin_unit` helper from PR #98), apply °F→°C conversion if applicable, re-run containment check. If now contained, update row: `validation_status='RESOLVED'`, `quarantine_reason=NULL`, add provenance field `bin_unit_converted=True` + `backfilled_via='backfill_london_f_to_c_2026_05_08'`.
3. Idempotent: re-running on already-resolved rows is a no-op.
4. Flags: `--dry-run` (default) prints proposed changes; `--apply` writes.
5. Single-writer: do NOT run while daemon is mid-write. Coordinate via existing `db_writer_lock` helper.
6. Run `--dry-run` first, verify count, share output, then `--apply`.

Acceptance: London quarantined count drops 317 → close to 0.

## DELIVERABLE_PATHS
- `docs/operations/task_2026-05-08_post_merge_full_chain/RUN.md` (master report covering all 4 phases)
- 1 PR for Phase C (or Phase C+D bundled)
- (Optional separate PR for Phase D if not bundled)

## VERDICT_TOKENS

Pick one:
- `ALL_PHASES_DONE` — all 4 phases complete
- `PHASE_A_DONE_REST_BLOCKED_<reason>` — daemon up but later phase failed
- `PHASES_AC_DONE_BD_PENDING_DOWNLOAD` — daemon up + #263 PR open, but download not yet complete
- `PARTIAL_<phases>_<reason>` — partial completion

## CONSTRAINTS

- **Pull main BEFORE any work** — 4 PRs merged in last hour
- **Operator explicitly authorized** launchctl + plist load + DB writes for THIS dispatch only
- If daemon fails clean restart OR plist load errors, STOP immediately and report
- Single-writer doctrine: no parallel write scripts (especially during daemon active)
- No `--no-verify`, no force-push, no `--amend` on others' commits
- If any phase fails non-trivially, document state in RUN.md and STOP — operator decides recovery
- Phase D backfill ONLY after Phase C ships (so the helper code is on main / accessible)
- For tolerance config (Phase C), if `config/settings.json` doesn't have `settlement.disagreement_tolerance_celsius`, ADD it with default 1.0 and document in RUN.md

## FINAL_REPLY_FORMAT

Single line: `<VERDICT> docs/operations/task_2026-05-08_post_merge_full_chain/RUN.md`
