# Live launch handoff — 2026-05-05

**Created**: 2026-05-05 (post bug-fix landing on PR #64)
**Purpose**: forward-looking state. What is alive, what to watch, what to do on each trigger. Past commit history is in `git log`; this doc only carries what surviving sessions need.

## Active monitors (this session)

| ID | Watch | Cadence | Trigger → Action |
|---|---|---|---|
| `bwcyv9ihr` | Phase 1 watcher (`local_post_extract_chain.sh`) verdict + stage progress | 15 min | `PHASE1_VERDICT_LANDED` → §A; `PHASE1_STALL_WARNING` (no log growth 2h) → §C |
| `b9gzbg2b2` | Phase 2 cloud download (10 lanes × 5 accounts × 2 metrics) | 6 hours | `PHASE2_DONE` → §B; `PHASE2_STALL_SUSPECT` → §C; `PHASE2_ERROR_DETECTED` → §C |

If the session compacts and monitors die, restart them from this doc. They are session-bound, not durable.

## §A — On `PHASE1_VERDICT_LANDED`

**Phase 1 status as of 2026-05-05 17:31Z**:
- Cloud extract + tar-pipe transfer: ✅ DONE (679MB tarball, 379k JSONs × 2 tracks).
- Snapshot ingest: ✅ DONE (71,460 NEW rows in `ensemble_snapshots_v2`).
- Pair build: ⚠️ PARTIAL — only Amsterdam committed (71,400 cycle='12' pairs, 1 of 49 cities). Pair-builder process aborted mid-run before reaching other cities.
- Refit cycle='12': ❌ NO MODELS LANDED. The 71,400 Amsterdam-only pairs are insufficient to produce stratified models across the 49-city × 4-season × 2-track bucket grid. Refit run at 17:31Z attempted only 206 cycle='00' buckets (all UNIQUE-collided with existing) and 0 cycle='12' buckets — discovery limitation under investigation.

**Recovery recipe to complete Phase 1**:
1. Pause `com.zeus.data-ingest` daemon.
2. Run `scripts/rebuild_calibration_pairs_v2.py` with NO `--city` filter (all 49 cities). Date range 2026-02-01..2026-05-02. Should produce ~3.5M cycle='12' pairs (49 cities × ~70k each).
3. Restart daemon.
4. Verify pair distribution: `SELECT cycle, COUNT(DISTINCT city), COUNT(*) FROM calibration_pairs_v2 GROUP BY cycle;` — expect cycle='12' city count = 49.
5. Investigate refit discovery: read `scripts/refit_platt_v2.py:240-260` for any WHERE filter excluding cycle='12'. Fix if present.
6. Re-run refit `--no-dry-run --force --temperature-metric all`.
7. Verify: `SELECT cycle, COUNT(*) FROM platt_models_v2 GROUP BY cycle` — expect cycle='12' > 0.
8. Run `scripts/evaluate_calibration_transfer_oos.py --no-dry-run` to populate `validated_calibration_transfers` rows.

Verdict file: `state/post_extract_pipeline_<ts>.json`. Read it after recovery.

**`verdict: ready_for_operator_promotion_review`** (success path):
1. Confirm `platt_models_v2` has new rows for `cycle='12', source_id='tigge_mars'` (or whatever the Phase 1 ingest produced):
   ```sql
   SELECT cycle, source_id, horizon_profile, COUNT(*)
   FROM platt_models_v2
   WHERE updated_at > '<watcher_start_ts>'
   GROUP BY cycle, source_id, horizon_profile;
   ```
2. Confirm `ensemble_snapshots_v2` got 12z rows:
   ```sql
   SELECT cycle, COUNT(*) FROM ensemble_snapshots_v2
   WHERE data_version LIKE 'tigge_%' GROUP BY cycle;
   ```
3. Run `scripts/evaluate_calibration_transfer_oos.py --no-dry-run` to populate `validated_calibration_transfers` rows for the 90-day window. Until this runs, cross-domain transfers (e.g. `ecmwf_open_data ← tigge_mars`) stay SHADOW_ONLY.
4. Report bucket coverage delta to operator + recommend lock-release timing per §D.

**`verdict: aborted_*`** (failure path):
- `aborted_pull_failed` — scp retry exhausted; SSH cloud, check disk space + auth; manually scp + restart from stage5
- `aborted_ingest_errors` — read `logs/post_extract_chain_<ts>.log` for the failing track; usually schema mismatch or duplicate row; fix + re-run `scripts/ingest_grib_to_snapshots.py --track <track>`
- `aborted_preflight_blocked` — Phase 2 schema migration drift OR precedence-200 lock dropped; verify both, fix, restart from stage6
- `aborted_refit_partial` — Platt fit refusal for some bucket; read `refit_platt_v2.py` log; usually n_pairs < 200; either lower threshold per-bucket or wait for more data
- `aborted_phase2_migration_unapplied` — should not fire (migration was applied 2026-05-05); if it does, re-run `scripts/migrate_phase2_cycle_stratification.py` first

## §B — On `PHASE2_DONE` (all 10 lanes complete; ~7-8 days from 2026-05-05)

Phase 2 covers `2024-01-01..2026-01-31` (760-day backfill). When download lanes all hit `complete`:

1. Kick cloud-side extract for the 760-day window. Re-use the sharded ×12 pattern from Phase 1 (12 sessions = 6 shards × 2 tracks). Manual launch on tigge-runner via tmux:
   ```bash
   for SHARD in 1 2 3 4 5 6; do
     for TRACK in mn2t6_low mx2t6_high; do
       SCRIPT="extract_tigge_${TRACK%_*}_localday_${TRACK##*_}"
       tmux new-session -d -s "extract-phase2-${TRACK}-${SHARD}" \
         "$PYTHON_BIN scripts/${SCRIPT}.py --track $TRACK \
            --manifest-path '<cloud manifest path>' \
            --cycle 12 --date-from 2024-01-01 --date-to 2026-01-31 \
            --shard-of-6 $SHARD \
            --raw-root '$ROOT/raw' --output-root '$ROOT/raw' 2>&1 | tee -a logs/extract_phase2_${TRACK}_${SHARD}.log"
     done
   done
   ```
   (Verify exact `--manifest-path` and `--shard-of-6` flag names against the actual extract script before launching — Phase 1 sharding pattern is the reference.)
2. Wait extract done (will take many hours; same monitoring pattern as Phase 1 — sessions disappear when complete).
3. scp results back. Then re-run local watcher with the Phase 2 date window:
   ```sh
   EXTRACT_DATE_FROM=2024-01-01 EXTRACT_DATE_TO=2026-01-31 \
     tmux new-session -d -s post-phase2-chain \
     "bash scripts/local_post_extract_chain.sh"
   ```
   (Or kick the equivalent ingest/preflight/refit sequence directly without the watcher's stage1/stage2 cloud-poll prefix.)
4. Refit will produce a much larger Platt v2 (760+90 = 850 days of 12z TIGGE pairs). Expect bucket coverage to multiply ~8×, and the `n_pairs ≥ 200` threshold to be passed for many more (cluster, season) buckets that were SHADOW_ONLY after Phase 1.
5. Run `scripts/evaluate_calibration_transfer_oos.py --no-dry-run` again to populate validated_transfers across the full window.
6. Report delta to operator: how many new buckets crossed n_pairs threshold, brier_diff distribution by route.

## §C — On stall / error events

- **`PHASE1_STALL_WARNING`** — log size unchanged 2h. SSH cloud, run `du -sh raw/` to check if scp is still copying bytes. If truly hung, `tmux kill-session -t post-extract-chain` locally + re-run from the failed stage.
- **`PHASE2_STALL_SUSPECT`** — same complete-count across two 6h polls. SSH cloud, list tmux sessions; check `tmp/tigge_*_status_*.json` for last `progress_at`. Common causes: MARS rate-limit pause, account auth expiry, disk full. Diagnose specific lane before broad action.
- **`PHASE2_ERROR_DETECTED`** — read each error-status JSON; classify auth / rate-limit / data; rotate account or wait + retry the failing lane only.

## §D — Pre-launch unlock checklist (operator-only)

Full procedure: `docs/operations/PLIST_UPDATE_FOR_RELOCK.md`. Summary of the **4 surfaces** that all must align:

1. **Plist `EnvironmentVariables`** — add `ZEUS_ENTRY_FORECAST_READINESS_WRITER=1`. **DO NOT** set `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` at initial launch — see `docs/operations/PLIST_UPDATE_FOR_RELOCK.md` §1.5 and `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` §4. The legacy static-mapping path (flag-OFF) is the correct calibration route at launch; flipping the OOS flag prematurely fails closed to SHADOW_ONLY because no `validated_calibration_transfers` rows exist for ECMWF target_source_id yet (Phase B uplift, ~2-4 weeks post-launch).
2. **`config/settings.json::entry_forecast.rollout_mode`** — change from `"blocked"` to one of `shadow|canary|live` (enum in `src/config.py:160-164`; "active" is NOT a valid value).
3. **`state/entry_forecast_promotion_evidence.json`** — must exist and parse via `read_promotion_evidence`. Operator-attestation fields (`operator_approval_id`, `g1_evidence_id`, `canary_success_evidence_id`) require operator audit before live; `status_snapshot.status` must equal `LIVE_ELIGIBLE` for the gate to permit live orders.
4. **`ensemble_snapshots_v2` row coverage** — populated by §A. The `validated_calibration_transfers` rows are NOT required at launch (legacy mapping path); they become required at Phase B when the OOS flag is flipped.

After 1–4, clear the precedence-200 lock:
```sql
UPDATE control_overrides
   SET effective_until = datetime('now')
 WHERE override_id = 'operator:tigge_12z_gap:LIVE_UNSAFE_2026_05_04';
```

## §E — Deferred items (not blocking launch)

| Item | Status | Resolution path |
|---|---|---|
| #134 100 BLOCKED `readiness_state` rows | direction ambiguous from agent audit (33 with LIVE_ELIGIBLE counterpart vs 67 singleton) | Operator confirms whether singleton-BLOCKED for future-target dates is "current pending coverage" (KEEP) or "stale dead-letter" (PURGE). SQL drafted in task #179 description. |
| `transfer_logit_sigma_scale` tuning | default 4.0 ships in `config/settings.json` | Tune post-Phase-1 if OOS empirics warrant |
| LOW oracle bridge (Issue 2.3 carryover) | fail-closed at `oracle_penalty.py:472` (LOW Kelly mult = 0.0) | LOW listener (HKO CLMMINT) is a separate roadmap item, not a math fix |
| PR #64 merge | OPEN, all Copilot comments resolved | Awaits operator review/merge approval |

## Cross-references

- Architecture: `architecture/calibration_transfer_oos_design_2026-05-05.md`, `architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md`
- Operator procedures: `docs/operations/PLIST_UPDATE_FOR_RELOCK.md`
- Code: `src/data/calibration_transfer_policy.py`, `src/data/entry_forecast_shadow.py:175-198`, `src/engine/evaluator.py:907`
- Watcher: `scripts/local_post_extract_chain.sh`, `scripts/cloud_tigge_autochain.sh`
- Daemon lock: `state/zeus-world.db` `control_overrides` row `operator:tigge_12z_gap:LIVE_UNSAFE_2026_05_04`
