# R0-f / R0-g operator runbook — plist secrets migration + log/DB hygiene

Authority: `docs/rebuild/EXECUTION_MASTER_2026-07-07.md` §E R0-f/R0-g, §C
constraint 8 ("secrets NEVER enter source/reports/git"). This is a
**PREPARE-only** artifact — Claude prepared and dry-ran everything below;
the operator executes every `--apply` / `launchctl` / `deploy_live.py`
step by hand. Nothing in this packet ran `--apply` on the live tree.

## 1. Plist inventory (2026-07-08)

`ls ~/Library/LaunchAgents/com.zeus.*.plist` found **10** live daemon
plists, not the 9 recorded in EXECUTION_MASTER §B7/§E R0-f — that count is
stale by one; verify with the same `ls` glob before trusting either number
again. Of the 10, **5 are tracked in git** (`rg -l "com.zeus" --glob
"*.plist" /Users/leofitz/zeus` under `deploy/launchd/`), matching the
"5/9" figure's numerator but not its denominator. The 5 previously
untracked ones now have sanitized templates added by this packet (§3).

| plist | in git before this packet | secret env keys present (names only) |
|---|---|---|
| com.zeus.calibration-transfer-eval | no → template added | none |
| com.zeus.data-ingest | no → template added | WU_API_KEY |
| com.zeus.forecast-live | no → template added | WU_API_KEY |
| com.zeus.heartbeat-sensor | no → template added | none |
| com.zeus.live-trading | no → template added | POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE, WU_API_KEY |
| com.zeus.post-trade-capital | yes (`deploy/launchd/`) | POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE, WU_API_KEY |
| com.zeus.price-channel-ingest | yes (`deploy/launchd/`) | POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE |
| com.zeus.riskguard-live | yes (`deploy/launchd/`) | none |
| com.zeus.substrate-observer | yes (`deploy/launchd/`) | POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE, WU_API_KEY |
| com.zeus.venue-heartbeat | yes (`deploy/launchd/`) | POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE |

launchd state at inventory time (`launchctl list | grep com.zeus`): 8 of
10 have a live PID, 2 (`calibration-transfer-eval`, `heartbeat-sensor`)
show `-` — they are `StartCalendarInterval` jobs, not `KeepAlive`
daemons, so no PID between runs is expected, not a health problem.

No secret **values** are recorded anywhere in this doc, this packet's
commit, or any file it wrote — only key names, per constraint 8.

## 2. Git-history caveat

**Clean — no history rewrite needed.** `git log --all -p` over all 6
tracked plist paths under `deploy/launchd/` (the 5 secret-bearing/
no-secret ones already in git, checked back to their first commit) was
searched for any `POLYMARKET_API_*` / `WU_API_KEY` value that isn't the
`REPLACE_ME_*` placeholder string; none found. The already-tracked 5 use
a `REPLACE_ME_*`-placeholder-then-operator-substitutes-at-install pattern
today — that pattern is different from (and does not itself violate
constraint 8 the way) the wrapper-exec pattern this packet introduces for
the 5 newly-tracked templates. Bringing the original 5 templates onto the
same wrapper-exec pattern for consistency is a reasonable follow-up but
is **out of this packet's scope** (not requested, and their current form
is already clean) — flagging it here for the operator to decide.

## 3. What this packet added

- `scripts/ops/migrate_plist_secrets.sh` — dry-run by default, `--apply`
  extracts secret values from the **currently-installed live** plists
  under `~/Library/LaunchAgents/` into `$HOME/.zeus/secrets.env`
  (chmod 600, created only on `--apply`, never inside the repo), backs
  up every plist it touches to
  `~/Library/LaunchAgents/backup_<YYYY-MM-DD>/` first, then rewrites
  `ProgramArguments` to the wrapper-exec form:
  `["/bin/bash", "-lc", "set -a; source $HOME/.zeus/secrets.env; set +a; exec <original argv>"]`
  and deletes the literal secret keys from `EnvironmentVariables`.
  Idempotent (a second run sees "no secret env keys present" and is a
  no-op). Never prints a secret value in either mode — dry-run doesn't
  even read one into a variable, only checks existence.
- `deploy/launchd/com.zeus.{live-trading,data-ingest,forecast-live,
  calibration-transfer-eval,heartbeat-sensor}.plist` — sanitized git
  templates for the 5 previously-untracked plists, built from the live
  plists with secret values stripped and (where secrets were present)
  the wrapper-exec pattern already wired in.
- `scripts/ops/rotate_zeus_logs.sh` — dry-run by default, `--apply`
  rotates any `logs/*.log`/`logs/*.err` at/over a size threshold
  (default 50MB, `ZEUS_LOG_ROTATE_MB` to override) using **copytruncate**
  (copy → gzip the copy → truncate the original file in place) because
  the daemons hold the log fds open for their whole lifetime; renaming
  the file out from under them would orphan future writes. Keeps 5
  gzip'd generations by default (`ZEUS_LOG_ROTATE_KEEP`), oldest pruned.
- `scripts/ops/db_hygiene.sh` — report-only by default, `--apply` deletes
  only files that are (a) reported as a 0-byte decoy under the
  root-directory-DB rule or the state/ underscore-vs-hyphen
  naming-duplicate rule, AND (b) still exactly 0 bytes at delete time,
  AND (c) not held open (`lsof`). Anything failing (b) or (c) is a hard
  refusal — reported, not deleted, non-zero exit.

## 4. Dry-run evidence captured against the live tree (2026-07-08)

### 4a. `migrate_plist_secrets.sh` (no `--apply`, no file written, no value read)

Ran against the real `~/Library/LaunchAgents/`. Output showed, per plist,
only which secret key **names** are present and the planned
`ProgramArguments` rewrite (paths/module names only, e.g. `-m src.main`)
— ­consistent with the table in §1. `com.zeus.riskguard-live` (and, in the
untracked set, `calibration-transfer-eval`/`heartbeat-sensor`) reported
"no secret env keys present — nothing to migrate". No secret value ever
appeared in the output.

Mechanics were separately verified end-to-end against a synthetic fixture
directory with **fake** placeholder secret values (`FAKE_PM_KEY_abc123`
etc., never real values): `--apply` correctly extracted+deduped keys into
a chmod-600 `secrets.env`, backed up originals, rewrote `ProgramArguments`
to the wrapper-exec form, passed the post-rewrite self-check (no secret
key survives), and was idempotent on a second `--apply` run.

### 4b. `rotate_zeus_logs.sh` (no `--apply`)

```
== rotate_zeus_logs.sh DRY-RUN ==
log dir  : /Users/leofitz/zeus/logs
threshold: 50MB (52428800 bytes)
keep     : 5 generations

DRY-RUN complete: 0 file(s) over threshold, 22 under threshold. Total logs/ bytes scanned: 50655564. Nothing written.
```

**Discrepancy note:** EXECUTION_MASTER §E R0-g says "3.4GB unrotated
logs". The live `logs/` directory measured **~50.6MB total** (`du -sh
/Users/leofitz/zeus/logs` → 61M including non-log files) at inventory
time, with the largest single file `zeus-live.log` at 13MB — nowhere near
3.4GB and nowhere near the default 50MB rotation threshold either. Either
the 3.4GB figure was already resolved by an earlier manual cleanup, or it
referred to a different location/point in time. The script is
threshold-based, not tied to that number, so it is correct regardless;
flagging so the operator doesn't act on a stale premise. Lower
`ZEUS_LOG_ROTATE_MB` if you want rotation to trigger sooner given the
current small sizes.

Copytruncate mechanics (gzip generation shift, cap at `KEEP`, truncate
in place) were verified end-to-end on a synthetic fixture, including
running 7 rotations in a row and confirming generations cap at 5.

### 4c. `db_hygiene.sh` (no `--apply`)

```
== db_hygiene.sh REPORT-ONLY ==
--- rule 1: root-directory *.db* files (K1 canon = state/ only) ---
DECOY  /Users/leofitz/zeus/risk_state.db  size=0B
DECOY  /Users/leofitz/zeus/zeus_trades.db  size=0B
REVIEW /Users/leofitz/zeus/zeus-forecasts.db  size=4096B   (non-zero, NOT auto-deletable)
DECOY  /Users/leofitz/zeus/zeus-world.db  size=0B
DECOY  /Users/leofitz/zeus/zeus-forecasts.db-wal  size=0B
REVIEW /Users/leofitz/zeus/zeus-forecasts.db-shm  size=32768B  (non-zero, NOT auto-deletable)

--- rule 2: state/ underscore-vs-hyphen naming duplicates ---
GROUP normalized=zeus_forecasts.db: DECOY state/zeus_forecasts.db (0B) vs KEEP state/zeus-forecasts.db (~40GB, canonical per db_table_ownership.yaml)
GROUP normalized=zeus_live.db:      DECOY state/zeus_live.db (0B)      vs KEEP state/zeus-live.db (4096B)
GROUP normalized=zeus_trades.db:    KEEP  state/zeus_trades.db (~84GB, canonical) vs DECOY state/zeus-trades.db (0B)
GROUP normalized=zeus_world.db:     DECOY state/zeus_world.db (0B)     vs KEEP state/zeus-world.db (~84GB, canonical)

--- delete-candidate resolution ---
WOULD DELETE risk_state.db, zeus_trades.db (root), zeus-world.db (root),
             zeus-forecasts.db-wal (root), state/zeus_forecasts.db,
             state/zeus_live.db, state/zeus-trades.db, state/zeus_world.db
             (all 0 bytes, none currently open)
```

Exact byte sizes fluctuate run to run because these are live,
continuously-written DBs; the report-only run always re-derives sizes
fresh, and `--apply` re-checks size + `lsof` again immediately before
each delete (hard refusal on any drift). `--apply` deletion logic (and
its hard refusal on non-zero/open files) was separately verified on an
isolated fixture, including a live-held-open-fd case that correctly
refused (exit 1, file untouched).

Canonical names cross-checked against `architecture/db_table_ownership.yaml`
lines 26-28: world=`state/zeus-world.db`, forecasts=`state/zeus-forecasts.db`
(both hyphen), trade=`state/zeus_trades.db` (underscore) — the split
itself already mixes separator conventions; this script does not try to
normalize that, only to remove the 0-byte stray variant of each pair.

## 5. Exact operator command sequence

Run in order. Every step below is something **you** run — none of it was
executed by this packet.

```bash
cd /Users/leofitz/zeus

# --- R0-f: plist secrets ---
# 1. Re-confirm dry-run still matches this doc (system may have changed):
scripts/ops/migrate_plist_secrets.sh

# 2. Apply (backs up originals, writes $HOME/.zeus/secrets.env chmod 600,
#    rewrites the plists in place under ~/Library/LaunchAgents/):
scripts/ops/migrate_plist_secrets.sh --apply

# 3. Verify no plist contains a literal secret anymore:
for f in ~/Library/LaunchAgents/com.zeus.*.plist; do
  echo "-- $f --"
  plutil -p "$f" | grep -iE "KEY|SECRET|PASSPHRASE|TOKEN"
done
# Expect: either nothing, or only the wrapper-exec bash -lc string
# referencing $HOME/.zeus/secrets.env — never a raw credential value.

# 4. Also verify the extracted secrets file:
ls -la ~/.zeus/secrets.env   # expect -rw------- (600)

# 5. git-add the 5 new sanitized templates this packet already staged
#    for the previously-untracked plists (already committed by this
#    packet under deploy/launchd/ — nothing further to add here unless
#    you also choose to migrate the existing 5 tracked templates to the
#    wrapper-exec pattern, see §2).

# --- Restart the mesh (NEVER a bare launchctl kickstart — split-brain risk) ---
python3 scripts/deploy_live.py restart all
# wait for preflight GREEN, then:
# <resume_entries per the live-daemon-deploy runbook, once preflight is GREEN>

# --- R0-g: log + DB hygiene ---
# 6. Rotate any log currently over threshold (safe with daemons running —
#    copytruncate never breaks the open fd):
scripts/ops/rotate_zeus_logs.sh
scripts/ops/rotate_zeus_logs.sh --apply     # only if step 6 showed candidates

# 7. Root/DB decoy hygiene — review the report first:
scripts/ops/db_hygiene.sh
# Read the DECOY / REVIEW / GROUP output carefully. REVIEW lines are
# non-zero and were NOT included as delete candidates — those need your
# own judgement, this script will never touch them even under --apply.
scripts/ops/db_hygiene.sh --apply           # only after you've reviewed the report
```

## 6. Rollback

- **Plists:** originals are in
  `~/Library/LaunchAgents/backup_<YYYY-MM-DD>/`. Restore with:
  ```bash
  cp ~/Library/LaunchAgents/backup_<date>/com.zeus.<name>.plist ~/Library/LaunchAgents/
  python3 scripts/deploy_live.py restart all
  ```
- **secrets.env:** delete `~/.zeus/secrets.env` and restore the
  pre-migration plists (which still carry the literal values) from the
  same backup dir if you need to fully revert.
- **Logs:** rotated files are gzip'd, not deleted (`logs/*.log.N.gz`);
  `zcat` and `cat >>` back onto the live file if you truly need to
  reassemble history — normally unnecessary.
- **DB hygiene:** every deleted file was independently verified 0 bytes
  and not open immediately before deletion; there is nothing to restore
  because a 0-byte SQLite file carries no rows. If in doubt, don't run
  `--apply` on rule 1's `REVIEW`-classified files — they are excluded by
  design.

## 7. Registry coverage

`docs/rebuild/` is already registered under `parent_coverage_allowed_patterns`
in `architecture/docs_registry.yaml` (descendant coverage via the
`docs/rebuild/` path entry at line 1619, `coverage_scope: descendants`) —
this doc needs no new registry row. Verified: `topology_doctor.py --docs`
shows the same pre-existing error set with and without this packet's
changes (baseline drift from the uncommitted B3 cleanup batch on the main
tree, not present in this worktree's base commit — unrelated to R0-f/g);
zero new `--docs` errors from this packet.

**`script_manifest.yaml`: rows deliberately NOT added for the 3 new
scripts.** `scripts/ops/` is a subdirectory; `topology_doctor`'s
`top_level_scripts()` (`scripts/topology_doctor_script_checks.py`) only
iterates direct children of `scripts/`, and its `run_scripts()` check
flags any manifest key without a matching top-level file as
`script_manifest_stale`. Adding `ops/migrate_plist_secrets.sh`-style keys
was tried and reverted after confirming it introduces exactly 3 new
`script_manifest_stale` errors under `--scripts` (verified: 0 new errors
with the entries removed). There is currently no supported manifest key
form for a `scripts/<subdir>/` script — `scripts/ops/health_probe.py` and
`scripts/ops/orderable_bias_pass_candidates.py` (pre-existing, unrelated
to this packet) are in the same unregistered state today. A `# NOTE` left
in `architecture/script_manifest.yaml` right after the last top-level
entry documents this gap and points here. The operator/registry-law owner
should decide whether to extend the manifest schema to cover
subdirectories or relocate `scripts/ops/*` to top-level `scripts/`; this
packet does not make that call.
