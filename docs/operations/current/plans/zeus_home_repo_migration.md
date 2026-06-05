# Zeus Home Repo Migration Plan

Date: 2026-06-05
Status: active plan
Target new repo: `/Users/leofitz/zeus`
Current live checkout: `/Users/leofitz/.openclaw/workspace-venus/zeus`

## Purpose

Move Zeus from the current nested checkout to a standalone home-directory repo
without breaking source control, live runtime, state DB authority, launchd jobs,
cron jobs, daily tools, worktrees, or AI/session continuity.

This is not a simple directory move. Zeus currently co-locates source, local
runtime state, canonical SQLite DBs, launch wrappers, logs, virtualenv, local AI
runtime state, and historical operation records under one path. The migration
must split those surfaces deliberately.

## Required Outcome

`/Users/leofitz/zeus` becomes the normal Zeus checkout for source work and,
after gated cutover, the runtime checkout for shadow/no-submit daemons. The old
checkout remains intact as a hot rollback point until all runtime-ready shadow
gates pass.

## Non-Goals

- Do not arm real order submission as part of repo migration.
- Do not delete the old checkout during migration.
- Do not migrate all ignored artifacts into git.
- Do not treat old operation docs as present-tense runtime authority.
- Do not run old and new checkouts as concurrent live writers.
- Do not externalize state into a separate runtime root in the first pass unless
  that is explicitly selected as a later architectural step.

## Current State Snapshot Procedure

The exact HEAD and dirty state can change during active work. Before execution,
freeze current facts with:

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
date -u
git status --short --branch
git rev-parse HEAD
git remote -v
git worktree list --porcelain
cat state/loaded_sha.json 2>/dev/null || true
launchctl list | rg 'zeus|openclaw' || true
crontab -l | nl -ba | rg 'zeus|workspace-venus|openclaw' || true
pgrep -af 'python -m src|forecast_live|riskguard|heartbeat|zeus' || true
```

Latest observed during planning:

- Branch: `main`
- HEAD: `90111adc24f45dc9ea1f869f56dcaee32e166e57`
- Relationship: `main...origin/main [ahead 77]`
- Untracked:
  - `docs/operations/HANDOFF_2026-06-04_live_restart_arm.md`
  - `scripts/emos_settled_diag.py`
  - `scripts/ops/candidate_fill_monitor.py`
- Active linked worktrees include repo-local `.claude/worktrees/wf_71a29a11-132-*`.

Treat this snapshot as stale after any new commit, daemon restart, or live-path
operation. Re-freeze before execution.

## Pre-Cutover Preparation Status

Executed on 2026-06-05 with live/runtime cutover intentionally out of scope.

Current target source mirror:

- Destination repo: `/Users/leofitz/zeus`
- Destination-relative root: `.`
- Destination state directory: `state/`
- Destination logs directory: `logs/`
- Destination config path: `config/settings.json`
- Destination plan path: `docs/operations/current/plans/zeus_home_repo_migration.md`

Completed pre-cutover actions:

- Created `/Users/leofitz/zeus` as a standalone git clone from the current
  checkout using `git clone --no-hardlinks`.
- Synced this plan and scope packet into the destination repo.
- Synced `config/settings.json` into the destination repo as an unstaged local
  runtime config file; it remains outside the planned commit scope unless the
  operator explicitly stages it.
- Left canonical runtime DBs uncopied. Destination `state/` currently contains
  only tracked placeholders / shadow placeholders, not production DB authority.
- Replaced old-checkout defaults in the following admitted scripts with
  destination-relative repo discovery and/or environment overrides:
  - `scripts/live_health_probe.py`
  - `scripts/live_health_monitor.sh`
  - `scripts/data_chain_monitor.sh`
  - `scripts/run_redeem_reconcile_with_onchain_proof.py`
  - `scripts/check_full_transport_ship_readiness.py`
  - `scripts/generate_monthly_bounds.py`
  - `scripts/pipeline_empirical_detail.py`
  - `scripts/audit_matched_date_proper_scores.py`
  - `scripts/build_ens_residual_evidence.py`

Verification already run:

```bash
python3 -m py_compile scripts/live_health_probe.py scripts/run_redeem_reconcile_with_onchain_proof.py scripts/check_full_transport_ship_readiness.py scripts/generate_monthly_bounds.py scripts/pipeline_empirical_detail.py scripts/audit_matched_date_proper_scores.py scripts/build_ens_residual_evidence.py
bash -n scripts/live_health_monitor.sh scripts/data_chain_monitor.sh
git diff --check
python3 scripts/topology_doctor.py --planning-lock --changed-files docs/operations/current/plans/zeus_home_repo_migration.md docs/operations/current/plans/zeus_home_repo_migration/scope.yaml scripts/live_health_probe.py scripts/live_health_monitor.sh scripts/data_chain_monitor.sh scripts/run_redeem_reconcile_with_onchain_proof.py scripts/check_full_transport_ship_readiness.py scripts/generate_monthly_bounds.py scripts/pipeline_empirical_detail.py scripts/audit_matched_date_proper_scores.py scripts/build_ens_residual_evidence.py --plan-evidence docs/operations/current/plans/zeus_home_repo_migration.md
```

The same py_compile, bash syntax, and `git diff --check` checks also passed in
`/Users/leofitz/zeus`.

Expected current destination probe behavior before DB/runtime cutover:

```text
ALERT ... flags=LIVE_CODE_PLANE_DRIFT,...,SETTLEMENT_TRUTH_DB_MISSING
```

That alert is expected while `/Users/leofitz/zeus/state` has no canonical DBs
and active daemons still belong to the old checkout.

Residual old-checkout path blockers found by repo scan:

| Path | Current classification | Required next route |
|---|---|---|
| `src/state/db_paths.py` | state authority default; already supports `ZEUS_PRIMARY_ROOT` override but still defaults to old root | state/DB authority planning route before changing default |
| `scripts/arm_live_mode.sh` | live operation script | cutover/live-shadow route only; out of scope while live is ignored |
| `scripts/expire_auto_pause.sh` | live operation script | cutover/live-shadow route only; out of scope while live is ignored |
| `scripts/purge_partial_fsr_events.py` | registered dangerous repair script | dedicated repair-script route; do not edit as generic hygiene |
| `scripts/score_raw_vs_sd3_bins.py` | unregistered historical/scoring script | script-manifest/provenance decision before edit |
| `scripts/install_hooks.sh` | unregistered installer helper | script-manifest/provenance decision before edit |
| `scripts/product_lineage_transfer.py` | unregistered historical transfer script | script-manifest/provenance decision before edit |
| `scripts/wrap_usdce_to_pusd_via_onramp.py` | unregistered one-off worktree script | script-manifest/provenance decision before edit |
| `tests/test_settlements_physical_quantity_invariant.py` | topology did not admit under generic hygiene route | test-route admission before edit |
| `tests/test_no_synthetic_provenance_marker.py` | topology did not admit under generic hygiene route | test-route admission before edit |
| `tests/test_pnl_flow_and_audit.py` | topology did not admit under generic hygiene route | test-route admission before edit |
| `tests/fixtures/dispatch_payloads/worktree_create_advisor.enter_worktree.json` | fixture path literal | fixture/test-route admission before edit |
| `scripts/authority_inventory.py` | comment-only reference | no runtime blocker; can be left until docs/comment hygiene route |

These paths mean `/Users/leofitz/zeus` is source-prepared but not yet cutover
clean. Runtime cutover must not proceed until the active-path subset above is
either patched under a valid route, explicitly retired, or proven non-runtime.

## Authority And Surface Classes

### Source Repo

Migrated by local git clone. Includes tracked source, tests, docs, architecture
manifests, `.claude` tracked governance/tooling, `.codex` tracked hooks, and
tracked placeholders in `state/`.

### Local Runtime State

Ignored by git. Must be copied or reinitialized by explicit policy. Includes:

- `state/*` except tracked placeholders/hash sidecars
- `logs/`
- `.venv/`
- `.omx/`
- `.omc/`
- `.claude/logs/`
- `.claude/worktrees/`
- `.claude/settings.local.json`
- `.pytest_cache/`, `.mypy_cache/`, `__pycache__/`, `.DS_Store`

### Canonical DB Authority

Must be handled as a live-data migration, not ordinary repo content:

- `state/zeus-world.db`
- `state/zeus-forecasts.db`
- `state/zeus_trades.db`
- all corresponding `-wal` and `-shm` sidecars

Observed sizes during planning:

```text
state/zeus-world.db         54G
state/zeus-world.db-wal     4.9M
state/zeus-world.db-shm     6.4M
state/zeus-forecasts.db     35G
state/zeus-forecasts.db-wal 8.1K
state/zeus-forecasts.db-shm 32K
state/zeus_trades.db        2.5G
state/zeus_trades.db-wal    6.0M
state/zeus_trades.db-shm    32K
```

### Global AI And Tooling State

Do not copy into the repo. Preserve in place:

- `~/.codex/sessions/`
- `~/.codex/archived_sessions/`
- `~/.codex/memories/`
- `~/.codex/logs_*.sqlite*`
- `~/.codex/state_*.sqlite*`
- `~/.openclaw/workspace/state/sessions/`
- `~/.openclaw/workspace*/memory/`

Repo-local `.omx/.omc` may be archived for context continuity but is not
authority and should not be copied into git.

## Migration Architecture Decision

Use staged local clone plus gated runtime retargeting.

Chosen option:

1. Create `/Users/leofitz/zeus` by cloning the current local repo, not by
   moving the current live checkout.
2. Keep `/Users/leofitz/.openclaw/workspace-venus/zeus` intact as hot standby.
3. Rebuild `.venv` in the new repo.
4. Make code paths root-relative or env-driven.
5. Copy local-only config and runtime state intentionally.
6. Stop writers before DB copy.
7. Retarget launchd/cron only after code-ready and DB integrity gates pass.
8. Start with shadow/no-submit runtime readiness.

Rejected options:

- `mv /Users/leofitz/.openclaw/workspace-venus/zeus /Users/leofitz/zeus`:
  breaks launchd, cron, worktree metadata, logs, venv, loaded SHA assumptions,
  and rollback simplicity.
- `rsync` as primary migration: can copy active DB/WAL/log/runtime state while
  writers are running and produce an inconsistent runtime clone.
- clone from `origin` only: local `main` is ahead of origin, so this can drop
  current local code authority.
- run old and new checkouts concurrently against separate live DB copies:
  creates split-brain runtime truth.

## Complete Replacement Map

Replacement target:

```text
OLD_REPO=/Users/leofitz/.openclaw/workspace-venus/zeus
NEW_REPO=/Users/leofitz/zeus
```

### Source And Runtime Code Paths

These must be changed before the new checkout can be runtime-ready.

| File | Old value | Required replacement |
|---|---|---|
| `src/state/db_paths.py` | default `/Users/leofitz/.openclaw/workspace-venus/zeus` | repo-relative fallback with `ZEUS_PRIMARY_ROOT` override |
| `scripts/live_health_probe.py` | `ROOT = "/Users/leofitz/.openclaw/workspace-venus/zeus"` | `ZEUS_PRIMARY_ROOT` or script-derived repo root |
| `scripts/live_health_monitor.sh` | `cd /Users/leofitz/.openclaw/workspace-venus/zeus` | `cd "${ZEUS_DIR:-/Users/leofitz/zeus}"` |
| `scripts/data_chain_monitor.sh` | `cd /Users/leofitz/.openclaw/workspace-venus/zeus` | `cd "${ZEUS_DIR:-/Users/leofitz/zeus}"` |
| `scripts/arm_live_mode.sh` | `ZEUS_DIR="${ZEUS_DIR:-/Users/leofitz/.openclaw/workspace-venus/zeus}"` | default `/Users/leofitz/zeus` or require explicit `ZEUS_DIR` |
| `scripts/expire_auto_pause.sh` | same old `ZEUS_DIR` default | default `/Users/leofitz/zeus` or require explicit `ZEUS_DIR` |
| `scripts/run_redeem_reconcile_with_onchain_proof.py` | `ZEUS_ROOT = "/Users/leofitz/.openclaw/workspace-venus/zeus"` | env/repo-root derived |
| `scripts/check_full_transport_ship_readiness.py` | `_STATE = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/state")` | `ROOT / "state"` |
| `scripts/generate_monthly_bounds.py` | `state/zeus-world.db` under old root | `STATE_DIR / "zeus-world.db"` |
| `scripts/pipeline_empirical_detail.py` | old `state/zeus-forecasts.db` | `ZEUS_FORECASTS_DB` or `STATE_DIR / "zeus-forecasts.db"` |
| `scripts/audit_matched_date_proper_scores.py` | default old `state/backups/` | `STATE_DIR / "backups"` |
| `scripts/build_ens_residual_evidence.py` | default old `config/cities.json` | `ROOT / "config/cities.json"` |
| `scripts/score_raw_vs_sd3_bins.py` | old `.claude/worktrees/...`, old forecast DB | configurable worktree/output path; DB from `STATE_DIR` |
| `scripts/product_lineage_transfer.py` | old `.claude/worktrees/.../ENS_RESIDUAL...csv` | explicit CLI arg or repo-relative checked input |
| `scripts/wrap_usdce_to_pusd_via_onramp.py` | old `.claude/worktrees/auto-wrap-rebase` in `sys.path` | remove worktree dependency; import from repo/package path |

Preferred Python pattern:

```python
from pathlib import Path
import os

ROOT = Path(os.environ.get("ZEUS_PRIMARY_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE_DIR = ROOT / "state"
```

Preferred shell pattern:

```bash
ZEUS_DIR="${ZEUS_DIR:-/Users/leofitz/zeus}"
cd "$ZEUS_DIR"
```

For high-risk arming scripts, stricter is acceptable:

```bash
: "${ZEUS_DIR:?Set ZEUS_DIR explicitly before arming live mode}"
cd "$ZEUS_DIR"
```

### External Source Data Paths

These paths point to external source-data roots, not the Zeus repo. Do not
blindly replace with `/Users/leofitz/zeus`.

| File | Existing external path | Policy |
|---|---|---|
| `scripts/diagnose_opendata_tigge_equivalence.py` | `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw` | introduce `ZEUS_51_SOURCE_ROOT` |
| `scripts/etl_solar_times.py` | `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/solar/` | introduce `ZEUS_51_SOURCE_ROOT` |
| `scripts/backfill_solar_openmeteo.py` | `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/solar/` | introduce `ZEUS_51_SOURCE_ROOT` |
| `raw/README.md` | `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/` | document external mount |

### Test And Fixture Paths

These do not block runtime cutover but should be fixed for portable test runs.

| File | Old path class | Required replacement |
|---|---|---|
| `tests/test_no_synthetic_provenance_marker.py` | old `state/zeus-world.db` | repo root/env fixture |
| `tests/test_settlements_physical_quantity_invariant.py` | old `state/zeus-world.db` | repo root/env fixture |
| `tests/test_pnl_flow_and_audit.py` | old `config/settings.json`, old `src` | repo root fixture |
| `tests/fixtures/dispatch_payloads/worktree_create_advisor.enter_worktree.json` | old `.claude/worktrees/test-fixture` | fixture-relative synthetic path |

Tests that use `ZEUS_STORAGE_ROOT` are already portable and should remain.

### Active LaunchAgents

All active Zeus launchd plists are valid XML (`plutil -lint` passed) but point
to the old repo. Update only after code-ready and DB-copy gates pass.

Current active files:

```text
/Users/leofitz/Library/LaunchAgents/com.zeus.live-trading.plist
/Users/leofitz/Library/LaunchAgents/com.zeus.forecast-live.plist
/Users/leofitz/Library/LaunchAgents/com.zeus.data-ingest.plist
/Users/leofitz/Library/LaunchAgents/com.zeus.riskguard-live.plist
/Users/leofitz/Library/LaunchAgents/com.zeus.venue-heartbeat.plist
/Users/leofitz/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist
/Users/leofitz/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist
```

Replace in each active plist:

| Old | New |
|---|---|
| `/Users/leofitz/.openclaw/workspace-venus/zeus` | `/Users/leofitz/zeus` |
| `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python` | `/Users/leofitz/zeus/.venv/bin/python` |
| `/Users/leofitz/.openclaw/workspace-venus/zeus/logs/...` | `/Users/leofitz/zeus/logs/...` |
| old `PYTHONPATH` value | `/Users/leofitz/zeus` |
| old `PATH` prefix | `/Users/leofitz/zeus/.venv/bin` |
| old `WorkingDirectory` | `/Users/leofitz/zeus` |

For each plist, verify all of these keys:

- `EnvironmentVariables.PYTHONPATH`
- `EnvironmentVariables.PATH`
- `ProgramArguments`
- `WorkingDirectory`
- `StandardOutPath`
- `StandardErrorPath`
- any explicit `ZEUS_*` root/config variables

Backups under `~/Library/LaunchAgents/*.bak*` do not need replacement for
runtime readiness, but should be labeled as old-path backups if retained.

### Active Cron Entries

Current active Zeus cron entries:

```text
line 48:
*/30 * * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && .venv/bin/python scripts/heartbeat_dispatcher.py >> /Users/leofitz/.openclaw/logs/zeus-heartbeat-dispatch.log 2>&1

line 67:
0 10 * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && WU_API_KEY=... .venv/bin/python scripts/oracle_snapshot_listener.py >> /Users/leofitz/.openclaw/logs/oracle-snapshot.log 2>&1
```

Required replacements:

```text
cd /Users/leofitz/.openclaw/workspace-venus/zeus
→
cd /Users/leofitz/zeus
```

Do not write secrets into any repo file. Preserve secret sourcing behavior in
cron or move it to a non-repo secrets manager.

### Shell/Profile Paths

No matching Zeus repo path was found in:

- `~/.zshrc`
- `~/.zprofile`
- `~/.bashrc`
- `~/.bash_profile`
- `~/.profile`
- scanned files under `~/.config` up to depth 3

Re-scan before cutover:

```bash
for f in ~/.zshrc ~/.zprofile ~/.bashrc ~/.bash_profile ~/.profile; do
  [ -f "$f" ] && rg -n 'workspace-venus/zeus|ZEUS_DIR|ZEUS_PRIMARY_ROOT|ZEUS_STORAGE_ROOT' "$f"
done
find ~/.config -maxdepth 3 -type f 2>/dev/null |
  while read -r f; do rg -n 'workspace-venus/zeus|ZEUS_DIR|ZEUS_PRIMARY_ROOT|ZEUS_STORAGE_ROOT' "$f"; done
```

### Historical Docs

Many docs contain old absolute paths as historical evidence. Do not mass-edit
them unless they are active runbooks or current plans. Historical docs include
old handoffs, logs, EDLi reviews, FT ship ledgers, and validation reports.

Active runbooks that should be reviewed for operator correctness after code
cutover:

- `docs/runbooks/live-operation.md`
- `docs/runbooks/forecast-live-daemon.md`
- `docs/runbooks/live-phase-1-first-boot.md`
- `docs/operations/com.zeus.d7-bias.plist`
- `docs/operations/PLIST_UPDATE_FOR_RELOCK.md`
- `docs/operations/current_state.md`
- `docs/operations/current/task.md`
- `docs/operations/current/package.yaml`

## Copy / Exclude / Reinitialize Policy

### Copy From Git Clone

The local clone should bring:

- tracked source and tests
- tracked docs and architecture manifests
- `.claude/CLAUDE.md`
- `.claude/hooks/*`
- `.claude/skills/*`
- `.codex/hooks.json`
- `.codex/hooks/zeus-router.mjs`
- `config/settings.example.json`
- `state/.gitkeep`
- `state/README.md`
- `state/topology_v_next_shadow/.gitkeep`

### Copy Manually And Intentionally

- `config/settings.json`
- any local, intentionally active handoff file
- selected runtime DB files and sidecars after writer quiescence
- selected logs if needed for continuity

### Reinitialize

- `.venv/`
- `.codegraph/codegraph.db*`
- `.code-review-graph/*` except tracked README/gitignore
- launchd loaded state
- cron entries
- `.omx` active runtime state unless a current OMX session must be preserved

### Archive Only

- `.omx/`
- `.omc/`
- `.claude/logs/`
- `.claude/worktrees/`
- `logs/`
- old packet/runtime scratch not needed for active work

### Do Not Copy Into Repo

- `~/.codex/sessions`
- `~/.codex/archived_sessions`
- `~/.codex/memories`
- `~/.openclaw/workspace/state/sessions`
- secrets, tokens, credentials, local private settings

## Phase Plan

### Phase 0: Freeze And Preflight

Objective: establish exact current truth and prevent split-brain writes.

Commands:

```bash
OLD=/Users/leofitz/.openclaw/workspace-venus/zeus
NEW=/Users/leofitz/zeus

cd "$OLD"
date -u | tee /tmp/zeus-migration-freeze.txt
git status --short --branch | tee -a /tmp/zeus-migration-freeze.txt
git rev-parse HEAD | tee -a /tmp/zeus-migration-freeze.txt
git remote -v | tee -a /tmp/zeus-migration-freeze.txt
git worktree list --porcelain > /tmp/zeus-worktrees-before.txt
launchctl list | rg 'zeus|openclaw' > /tmp/zeus-launchctl-before.txt || true
crontab -l > /tmp/zeus-crontab-before.txt
pgrep -af 'python -m src|forecast_live|riskguard|heartbeat|zeus' > /tmp/zeus-processes-before.txt || true
```

Stop condition:

- If current branch/HEAD is not the intended source authority, stop.
- If untracked files are active implementation work, decide whether to commit,
  copy separately, or leave behind before cloning.

### Phase 1: Create Standalone Source Repo

Objective: create source-only standalone checkout without moving live runtime.

Commands:

```bash
OLD=/Users/leofitz/.openclaw/workspace-venus/zeus
NEW=/Users/leofitz/zeus

test ! -e "$NEW"
git clone --no-hardlinks "$OLD" "$NEW"
cd "$NEW"
git status --short --branch
git rev-parse HEAD
git remote -v
```

Expected:

- `HEAD` matches frozen old checkout HEAD.
- Local branches are available as needed.
- No `state/*.db`, `logs/`, `.venv/`, `.omx/`, `.omc` were copied by git.

### Phase 2: Rebuild Local Environment

Objective: make `/Users/leofitz/zeus` code-ready before any runtime retarget.

Commands depend on the current environment manager. Minimum:

```bash
cd /Users/leofitz/zeus
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
# Install project requirements using the repo's established dependency command.
```

Verification:

```bash
cd /Users/leofitz/zeus
.venv/bin/python -m compileall src scripts
python3 scripts/topology_doctor.py --navigation --task "post-clone code-ready smoke" --intent audit --write-intent read_only
```

Do not point launchd or cron to the new repo in this phase.

### Phase 3: Apply Path-Portability Code Changes

Objective: remove active old-root assumptions from source and daily scripts.

Allowed first slice:

- `src/state/db_paths.py`
- `scripts/live_health_probe.py`
- `scripts/live_health_monitor.sh`
- `scripts/data_chain_monitor.sh`
- `scripts/arm_live_mode.sh`
- `scripts/expire_auto_pause.sh`

Second slice:

- `scripts/run_redeem_reconcile_with_onchain_proof.py`
- `scripts/check_full_transport_ship_readiness.py`
- `scripts/generate_monthly_bounds.py`
- `scripts/pipeline_empirical_detail.py`
- `scripts/audit_matched_date_proper_scores.py`
- `scripts/build_ens_residual_evidence.py`

Third slice:

- data-work scripts with old worktree/source-data assumptions
- portable tests/fixtures
- active runbook docs

Each slice requires its own topology/admission check before editing.

Acceptance for this phase:

```bash
cd /Users/leofitz/zeus
rg -n '/Users/leofitz/.openclaw/workspace-venus/zeus|workspace-venus/zeus' src scripts tests
```

Only approved historical/test fixtures may remain after the relevant slice.

### Phase 4: Local Config Migration

Objective: bring operator-local config across intentionally.

Commands:

```bash
OLD=/Users/leofitz/.openclaw/workspace-venus/zeus
NEW=/Users/leofitz/zeus

diff -u "$OLD/config/settings.example.json" "$NEW/config/settings.example.json" || true
cp "$OLD/config/settings.json" "$NEW/config/settings.json"
chmod 600 "$NEW/config/settings.json"
```

Policy:

- Preserve shadow/no-submit posture during migration.
- Do not infer live-ready from settings.
- Do not commit `config/settings.json`.

### Phase 5: DB And Runtime State Copy

Objective: copy canonical runtime state without DB split-brain.

Preconditions:

- Old checkout remains rollback authority.
- New checkout is code-ready.
- Writers are stopped or confirmed quiescent.
- No old/new duplicate writer will run during copy.

Stop writers:

```bash
launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.forecast-live.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.riskguard-live.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.venue-heartbeat.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist 2>/dev/null || true
```

Confirm no writers:

```bash
pgrep -af 'python -m src|forecast_live|riskguard|heartbeat|zeus' || true
lsof /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db 2>/dev/null || true
lsof /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-forecasts.db 2>/dev/null || true
lsof /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db 2>/dev/null || true
```

Copy:

```bash
OLD=/Users/leofitz/.openclaw/workspace-venus/zeus
NEW=/Users/leofitz/zeus
mkdir -p "$NEW/state" "$NEW/logs"

rsync -a --progress \
  "$OLD/state/zeus-world.db" "$OLD/state/zeus-world.db-wal" "$OLD/state/zeus-world.db-shm" \
  "$OLD/state/zeus-forecasts.db" "$OLD/state/zeus-forecasts.db-wal" "$OLD/state/zeus-forecasts.db-shm" \
  "$OLD/state/zeus_trades.db" "$OLD/state/zeus_trades.db-wal" "$OLD/state/zeus_trades.db-shm" \
  "$NEW/state/"

rsync -a --progress "$OLD/state/"*.sha256 "$OLD/state/"*.md5 "$NEW/state/" 2>/dev/null || true
rsync -a "$OLD/state/"*.json "$OLD/state/"*.jsonl "$NEW/state/" 2>/dev/null || true
```

Verify DBs:

```bash
NEW=/Users/leofitz/zeus
sqlite3 "file:$NEW/state/zeus-world.db?mode=ro" "PRAGMA query_only=ON; PRAGMA quick_check;"
sqlite3 "file:$NEW/state/zeus-forecasts.db?mode=ro" "PRAGMA query_only=ON; PRAGMA quick_check;"
sqlite3 "file:$NEW/state/zeus_trades.db?mode=ro" "PRAGMA query_only=ON; PRAGMA quick_check;"
```

If checksum sidecars exist, verify them or regenerate new migration-specific
checksums using the repo checksum script.

### Phase 6: Retarget Cron

Objective: move scheduled low-level jobs after new repo is DB-valid.

Edit crontab entries:

```text
cd /Users/leofitz/.openclaw/workspace-venus/zeus
→
cd /Users/leofitz/zeus
```

Affected active Zeus entries:

- heartbeat dispatcher
- oracle snapshot listener

Verification:

```bash
crontab -l | nl -ba | rg 'zeus|workspace-venus'
```

No active Zeus cron should point to old repo unless intentionally paused or
part of rollback.

### Phase 7: Retarget LaunchAgents Shadow-Only

Objective: move host services to new repo without arming real fills.

Backup first:

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p ~/Library/LaunchAgents/zeus-migration-backups-$TS
cp ~/Library/LaunchAgents/com.zeus.*.plist ~/Library/LaunchAgents/zeus-migration-backups-$TS/
```

Edit active plists listed above. Replace old root with new root. Keep
shadow/no-submit config unchanged.

Validate:

```bash
plutil -lint ~/Library/LaunchAgents/com.zeus.live-trading.plist
plutil -lint ~/Library/LaunchAgents/com.zeus.forecast-live.plist
plutil -lint ~/Library/LaunchAgents/com.zeus.data-ingest.plist
plutil -lint ~/Library/LaunchAgents/com.zeus.riskguard-live.plist
plutil -lint ~/Library/LaunchAgents/com.zeus.venue-heartbeat.plist
plutil -lint ~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist
plutil -lint ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist

rg '/Users/leofitz/.openclaw/workspace-venus/zeus' ~/Library/LaunchAgents/com.zeus.*.plist
```

Expected:

- No active plist points to old repo.
- Backup plists may still contain old paths.

### Phase 8: Start Services In Order

Start order:

1. heartbeat/monitoring only
2. data ingest / forecast live
3. riskguard
4. live daemon in no-submit mode
5. venue heartbeat / ancillary jobs

After each start:

```bash
launchctl list | rg 'zeus'
tail -n 80 /Users/leofitz/zeus/logs/<service>.log
tail -n 80 /Users/leofitz/zeus/logs/<service>.err
pgrep -af '/Users/leofitz/zeus|python -m src'
```

Hard gate:

```bash
pgrep -af '/Users/leofitz/.openclaw/workspace-venus/zeus|/Users/leofitz/zeus'
```

There must not be simultaneous old and new writer processes.

### Phase 9: Runtime-Ready Shadow Verification

Required checks:

```bash
cd /Users/leofitz/zeus
python3 scripts/healthcheck.py --mode live --json
python3 scripts/check_forecast_live_ready.py --claim-mode post-launch --json
python3 scripts/topology_doctor.py --navigation --task "post-migration runtime shadow verification" --intent audit --write-intent read_only
```

Manual evidence to inspect:

- `state/loaded_sha.json` matches `/Users/leofitz/zeus` HEAD
- heartbeats advance under `/Users/leofitz/zeus/state`
- logs write under `/Users/leofitz/zeus/logs`
- DB writes, if expected, occur in `/Users/leofitz/zeus/state`
- `real_order_submit_enabled` remains false unless a separate live-arm plan is
  explicitly approved
- no duplicate writers
- riskguard healthy
- source freshness not assumed from stale docs

### Phase 10: Worktree And AI Runtime Follow-Up

Worktrees:

```bash
cd /Users/leofitz/zeus
git worktree list --porcelain
```

Policy:

- Do not copy old worktrees wholesale.
- Recreate only active worktrees from `/Users/leofitz/zeus`.
- Archive stale `.claude/worktrees` rather than treating them as current source.

AI/session records:

- Leave global Codex/Claude/OpenClaw sessions in their global locations.
- Keep tracked `.claude` and `.codex` repo tooling from git.
- Archive old repo-local `.omx/.omc` only if needed for human recall.
- Do not commit transcripts or session DBs.

## Verification Matrix

| Gate | Claim | Required evidence |
|---|---|---|
| Source clone | `/Users/leofitz/zeus` has correct source authority | matching frozen HEAD, `git status`, remote check |
| Code-ready | code imports and tooling run from new repo | compile smoke, topology orientation, targeted tests |
| Path-ready | active source/scripts no longer require old root | `rg` over `src scripts tests` |
| DB-ready | destination DBs are readable and coherent | `PRAGMA quick_check`, checksum/size comparison |
| Cron-ready | active Zeus cron points to new repo | `crontab -l` scan |
| Launchd-ready | active plists point to new repo and lint | `plutil -lint`, `rg` scan |
| Runtime shadow | daemons run from new repo in no-submit mode | launchctl, logs, heartbeats, loaded SHA |
| No split brain | only one writer set exists | process/lsof checks |
| Rollback-ready | old repo and plist backups remain usable | old checkout intact, backups present |

## Final Active Path Audit

Run after migration:

```bash
OLD=/Users/leofitz/.openclaw/workspace-venus/zeus
NEW=/Users/leofitz/zeus

rg -n "$OLD|workspace-venus/zeus" "$NEW/src" "$NEW/scripts" "$NEW/tests" || true
rg -n "$OLD|workspace-venus/zeus" ~/Library/LaunchAgents/com.zeus.*.plist || true
crontab -l | rg "$OLD|workspace-venus/zeus" || true
pgrep -af "$OLD|$NEW|python -m src" || true
```

Acceptable remaining matches:

- historical docs
- launchd backup files
- archived old worktree evidence
- external source-data paths not representing the Zeus repo

Unacceptable remaining matches:

- active `src/` runtime paths
- active `scripts/` daily/runtime paths
- active launchd plists
- active cron entries
- active process command lines for old checkout after cutover

## Rollback Plan

Rollback before DB copy:

- Delete or ignore `/Users/leofitz/zeus`.
- Keep old checkout and launchd unchanged.

Rollback after DB copy but before launchd retarget:

- Stop using `/Users/leofitz/zeus`.
- Old launchd still points to old checkout.
- Restart old services from old checkout if they were unloaded.

Rollback after launchd retarget:

```bash
launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.forecast-live.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.riskguard-live.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.venue-heartbeat.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist 2>/dev/null || true
```

Restore backed-up plists from the migration backup directory, then:

```bash
plutil -lint ~/Library/LaunchAgents/com.zeus.*.plist
launchctl load ~/Library/LaunchAgents/com.zeus.forecast-live.plist
launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist
launchctl load ~/Library/LaunchAgents/com.zeus.riskguard-live.plist
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist
```

Post-rollback evidence:

- old checkout process command lines
- old checkout logs advance
- old checkout heartbeats advance
- no new checkout writers remain
- `real_order_submit_enabled` still false unless separately authorized

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| hidden absolute old path remains | final `rg` over repo, launchd, cron, profiles |
| DB snapshot inconsistent | stop writers, include WAL/SHM, run SQLite checks |
| split-brain writers | hard process/lsof gate before and after launch |
| local settings copied into git | preserve `.gitignore`, do not stage `config/settings.json` |
| transcript/session loss | leave global Codex/OpenClaw state in place; archive repo-local scratch only |
| stale worktrees confuse source authority | recreate active worktrees from new repo only |
| live-ready overclaim | keep code-ready, runtime-ready shadow, and live-arm separate |
| launchd typo | `plutil -lint`, staged service start, log check per service |

## Implementation Slices

1. Plan-only closeout: this document.
2. Source path abstraction patch.
3. Daily script path patch.
4. Test portability patch.
5. New repo clone and venv build.
6. DB copy rehearsal with writers stopped.
7. Cron retarget.
8. Launchd retarget shadow-only.
9. Runtime shadow verification.
10. Old checkout retirement decision after sustained stability.

Each slice must run its own topology/admission command and verification before
moving to the next slice.

## Stop Conditions

Stop and do not proceed to runtime retarget if:

- current HEAD is not agreed as source authority
- untracked files are active and not handled
- DB checks fail
- any writer still has old DB files open during copy
- new repo path scan still finds active old-root runtime paths
- launchd lint fails
- any process starts from the wrong checkout
- `real_order_submit_enabled` changes unexpectedly
- there is uncertainty about which checkout owns live writers

## Completion Definition

Migration is complete only when:

- `/Users/leofitz/zeus` is the source checkout used for daily work.
- Active launchd and cron Zeus entries point to `/Users/leofitz/zeus`.
- Canonical DB files in `/Users/leofitz/zeus/state` pass integrity checks.
- Runtime logs and heartbeats advance from `/Users/leofitz/zeus`.
- `state/loaded_sha.json` matches the new checkout HEAD.
- No active process writes from the old checkout.
- Old checkout remains available for rollback until an explicit retirement step.
- Live arm/fill remains a separate approved gate.
