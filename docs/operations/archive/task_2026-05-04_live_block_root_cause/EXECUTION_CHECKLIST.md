# PR-A Execution Checklist (post-compact resume)

**Self-contained.** This document survives compaction. Read this + `ROOT_CAUSE.md` + `FIX_PLAN.md` (same dir). Do not rely on chat history.

**Critic verdict:** APPROVE-WITH-CHANGES, all must-fixes integrated into FIX_PLAN.md v2.
**Authority basis:** `ROOT_CAUSE.md` empirical evidence (DB query, live introspection, stderr line 4051), `FIX_PLAN.md` post-critic v2.

---

## Context (one paragraph)

Zeus daemon (PID 26019, started Sun May 3 20:46:53 CDT 2026) has been silently failing live trading for 16 days. Every cycle prints `0 candidates` because `entries_paused: True` is locked by `state/auto_pause_failclosed.tombstone` (content `heartbeat_cancel_suspected`). DB pause row already self-expired (effective_until 02:19:09 UTC). The real `ValueError` raise file:line is unknown (call it D1) because `src/engine/cycle_runtime.py:2988` `logger.error(...)` lacks `exc_info=True` and silently eats the traceback. PR-A fixes the logger, removes the tombstone, restarts daemon, captures the traceback. PR-B (next week) installs structural antibodies.

---

## Pre-flight checks (do before any modification)

```bash
# 1. Confirm daemon is the one we think it is
ps -p 26019 -o pid,lstart,etime,command
# Expected: started Sun May 3 20:46:53 2026, command python -m src.main

# 2. Confirm tombstone still locked
ls -la state/auto_pause_failclosed.tombstone
cat state/auto_pause_failclosed.tombstone
# Expected: file exists, content "heartbeat_cancel_suspected"

# 3. Confirm DB pause row still latest
sqlite3 -header state/zeus-world.db "SELECT issued_at,effective_until,reason FROM control_overrides WHERE override_id='control_plane:global:entries_paused';"
# Expected: latest issued_at=02:04:09, effective_until=02:19:09 (already expired)

# 4. Confirm cycle_runtime.py:2988 still has the bug
sed -n '2987,2989p' src/engine/cycle_runtime.py
# Expected: line 2988 contains `deps.logger.error(...)` with bare `e`, no `exc_info=True`

# 5. Confirm git is clean and on main / right branch
git status -sb
git rev-parse HEAD
# Expected: clean working tree on main or known branch; HEAD is reasonable
```

If any pre-flight fails → STOP, re-read ROOT_CAUSE.md and reassess. Do not proceed on stale assumptions.

---

## Step 1: A2 audit (find all silent except-loggers)

```bash
# Find candidate offenders in src/engine + src/control
grep -nA5 'except Exception' src/engine/*.py src/control/*.py 2>/dev/null \
  | grep -B1 'logger\.\(error\|warning\)' \
  | grep -v 'exc_info=True\|logger.exception' \
  | head -50
```

Write findings to `docs/operations/task_2026-05-04_live_block_root_cause/A2_AUDIT.md`. List every match as `file:line — context snippet`. Even if the only offender is `cycle_runtime.py:2988`, document it.

**Acceptance:** A2_AUDIT.md exists; explicitly lists every site needing `exc_info=True`.

---

## Step 2: Create branch + apply fix

```bash
git checkout -b live-block-traceback-capture-2026-05-04
```

Edit `src/engine/cycle_runtime.py:2988` (and any other A2 offenders):

```python
# BEFORE:
except Exception as e:
    deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e)

# AFTER:
except Exception as e:
    deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e, exc_info=True)
```

Use the `Edit` tool, NOT sed/awk.

**Acceptance:** `git diff` shows only the targeted line(s) changed; `exc_info=True` added.

---

## Step 3: Commit and push

```bash
git add src/engine/cycle_runtime.py docs/operations/task_2026-05-04_live_block_root_cause/A2_AUDIT.md
# Add any other files modified in step 2
git status  # verify nothing unexpected
git commit -m "$(cat <<'EOF'
fix(observability): add exc_info=True to silent except-Exception loggers

Per ROOT_CAUSE.md SF1: cycle_runtime.py:2988 (and audited siblings) log
exception via logger.error without exc_info=True. This has hidden every
ValueError traceback for 16 days while the daemon silently auto-paused
in a 15-min loop. PR-A surfaces the next traceback so D1 (real ValueError
file:line) can be identified.

Authority: docs/operations/task_2026-05-04_live_block_root_cause/
  - ROOT_CAUSE.md (5 structural failures, empirical evidence)
  - FIX_PLAN.md v2 (post-critic, APPROVE-WITH-CHANGES)
  - A2_AUDIT.md (all sites needing fix)
  - EXECUTION_CHECKLIST.md (this PR's playbook)

Companion plan PR-B will install structural antibodies for SF1-SF4.

[skip-invariant] dynamic_sql_baseline drift pre-existing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push -u origin live-block-traceback-capture-2026-05-04
```

**Acceptance:** commit lands; remote branch exists; commit message references all four task docs.

---

## Step 4: DB unpause

```bash
NOW=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")
sqlite3 state/zeus-world.db "INSERT INTO control_overrides_history (override_id, target_type, target_key, action_type, value, issued_by, issued_at, effective_until, reason, precedence, operation, recorded_at) VALUES ('control_plane:global:entries_paused', 'global', 'entries', 'gate', 'false', 'control_plane', '$NOW', NULL, 'manual_unblock_for_traceback_capture_PR_A', 100, 'upsert', '$NOW');"

# Verify
sqlite3 -header state/zeus-world.db "SELECT issued_at,value,reason FROM control_overrides WHERE override_id='control_plane:global:entries_paused';"
# Expected: latest row has value='false'
```

**Acceptance:** SELECT returns the just-inserted row with `value='false'`.

---

## Step 5: Remove tombstone + streak file

```bash
rm state/auto_pause_failclosed.tombstone
rm -f state/auto_pause_streak.json

# Verify
ls -la state/auto_pause_failclosed.tombstone state/auto_pause_streak.json 2>&1 | grep -E 'No such|cannot access' | wc -l
# Expected: 2 (both files reported missing)
```

**Acceptance:** both files absent.

---

## Step 6: Restart daemon

```bash
launchctl kickstart -k gui/501/com.zeus.live-trading

# Verify daemon restarted
sleep 5
ps -p 26019 -o pid,lstart,etime 2>&1 | tail -1  # may show old PID gone
ps aux | grep '[s]rc.main' | head  # should show new PID
```

Note: launchctl kickstart -k preserves the label binding. The PID will change. Note the new PID.

**Acceptance:** A python process running `-m src.main` exists; etime is small (just started).

---

## Step 7: HARDENED ACCEPTANCE GATE — capture traceback

This is the critical step. "Daemon didn't crash" is INSUFFICIENT.

```bash
# Tail stderr filtered for cycle outcomes + traceback signals
# Wait up to 15 min for first cycle (opening_hunt fires every :02 :17 :32 :47)
tail -F logs/zeus-live.err | grep --line-buffered -E 'Evaluation failed|Traceback|ValueError|Entry path raised|Cycle (opening_hunt|update_reaction|day0_capture):|candidates,'
```

**Two acceptable outcomes:**

(a) **SUCCESS PATH** — cycle log shows `Cycle opening_hunt: N monitors, M exits, K candidates, J trades` with `K ≥ 1`. This means tombstone removal succeeded and discovery is producing candidates. Daemon is healthy. D1 may have been latent (data-shape changed) or fixed upstream. Document outcome in `docs/operations/task_2026-05-04_live_block_root_cause/D1_TRACEBACK.md` as "no traceback captured; cycle now produces candidates".

(b) **TRACEBACK PATH** — stderr emits `[src.engine.cycle_runner] ERROR: Evaluation failed for <city> <date>: <exception text>` followed by full multi-line `Traceback (most recent call last):` block ending with the actual `raise ValueError(...)` line. Capture verbatim to `D1_TRACEBACK.md`.

**Anything else** (no cycle in 15 min, daemon crashloop, partial output, mid-cycle hang) → BLOCK and rollback (Step 9).

**Acceptance:** D1_TRACEBACK.md exists with one of the two outcome types documented.

---

## Step 8: Decide D1 fix scope

Based on Step 7 outcome:

- If (a) success: PR-A's job is done. Note in PR description that D1 was latent / cleared by tombstone removal. No code change for D1.
- If (b) traceback captured:
  - If raise is at `evaluator.py:714` (`_normalize_temperature_metric`): the upstream market scanner is producing a candidate without a temperature_metric field. Fix at scanner / dict-builder seam.
  - If raise is at `evaluator.py:3478` (`ENS snapshot missing fetch_time`): ensemble fetcher returned dict without expected key. Fix at fetch boundary.
  - If raise is at `evaluator.py:1414` (`entry provenance context required`): provenance pipeline regression.
  - If raise is somewhere else: read the frame, identify the contract violation, fix at the smallest safe seam.
  - **Surgical (1-2 line) fix → bundle into PR-A same commit.**
  - **Larger fix (multi-file, contract change) → split into PR-A-followup branch.**

Document decision in `D1_TRACEBACK.md`. Apply fix on appropriate branch.

**Acceptance:** D1 either resolved (Step 7 outcome a), or surgical fix applied + tested + pushed, or split-PR ticket created with clear scope.

---

## Step 9: Rollback (if Step 7 fails)

```bash
# Revert PR-A code on branch (don't reset main)
git revert HEAD --no-edit
git push

# Re-pause daemon to safe state
NOW=$(python3 -c "from datetime import datetime,timezone,timedelta; print((datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat())")
ISSUED=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")
sqlite3 state/zeus-world.db "INSERT INTO control_overrides_history (override_id, target_type, target_key, action_type, value, issued_by, issued_at, effective_until, reason, precedence, operation, recorded_at) VALUES ('control_plane:global:entries_paused', 'global', 'entries', 'gate', 'true', 'control_plane', '$ISSUED', '$NOW', 'manual_repause_after_PR_A_rollback', 100, 'upsert', '$ISSUED');"

# Optionally rewrite tombstone if heartbeat tombstone behavior is unstable
# (skip unless investigation says we need to re-block)

# Restart daemon on reverted code
launchctl kickstart -k gui/501/com.zeus.live-trading
```

Document rollback reason in `D1_TRACEBACK.md`. Open ticket for retry.

---

## Step 10: PR-A merge

Once Step 7 acceptance + Step 8 D1 disposition are met:

```bash
# Sanity-run targeted tests on PR-A branch
cd /Users/leofitz/.openclaw/workspace-venus/zeus
.venv/bin/python -m pytest tests/test_runtime_guards.py -k 'pause or tombstone or auto_pause' -x 2>&1 | tail -30

# Open PR
gh pr create --title "PR-A: Live-block traceback capture (cycle_runtime.py:2988 exc_info)" --body "$(cat <<'EOF'
## Summary
- Adds `exc_info=True` to silent `except Exception` loggers so future tracebacks reach stderr
- Removes stale `auto_pause_failclosed.tombstone` blocking 16-day daemon lock
- Captures D1 (real ValueError file:line) for follow-up

## Authority
- `docs/operations/task_2026-05-04_live_block_root_cause/ROOT_CAUSE.md` (5 SF analysis)
- `docs/operations/task_2026-05-04_live_block_root_cause/FIX_PLAN.md` v2 (post-critic)
- `docs/operations/task_2026-05-04_live_block_root_cause/A2_AUDIT.md` (audit)
- `docs/operations/task_2026-05-04_live_block_root_cause/D1_TRACEBACK.md` (capture outcome)

## Test plan
- [x] Pre-flight checks passed
- [x] A2 audit complete; only `cycle_runtime.py:2988` flagged (or list any others)
- [x] DB unpause row written
- [x] Tombstone + streak file removed
- [x] Daemon restarted, new PID confirmed
- [x] Hardened acceptance gate met: candidates>0 OR full traceback captured
- [x] D1 disposition documented

## Follow-up
PR-B (next week) installs structural antibodies — owner-tagged tombstone JSON, exc_info AST invariant, DB-first is_entries_paused. See FIX_PLAN.md PR-B section.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Acceptance summary (gates between steps)

| Step | Gate |
|---|---|
| Pre-flight | All 5 checks PASS |
| 1 | A2_AUDIT.md exists with full list |
| 2 | git diff shows only exc_info=True additions |
| 3 | commit + push successful, message links all docs |
| 4 | SQL SELECT returns value='false' |
| 5 | both files absent |
| 6 | new daemon process running |
| **7** | **candidates>0 OR full traceback in D1_TRACEBACK.md (HARDENED)** |
| 8 | D1 disposition documented and applied |
| 9 (if needed) | rollback complete, daemon back to safe state |
| 10 | PR opened with all artifacts referenced |

---

## What this checklist intentionally does NOT do

- Touch root `AGENTS.md` (constraint lives in `src/control/AGENTS.md:18` — preserved)
- Modify HeartbeatSupervisor (PR-B scope)
- Add owner-tagged tombstone (PR-B scope)
- Add `tests/test_logger_exc_info_invariant.py` (PR-B scope)
- Run history cleanup script (PR-B scope)

PR-B is the structural antibody PR. PR-A is just enough to surface D1.
