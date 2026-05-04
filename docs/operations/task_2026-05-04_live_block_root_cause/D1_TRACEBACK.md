# D1 — Real ValueError Capture (PR-A acceptance gate output)

**Capture run:** 2026-05-04 (post PR-A daemon restart)
**Daemon:** new PID 4250, started Sun May 3 22:31 CDT 2026
**Boot commit:** `bc429393` (`live-block-traceback-capture-2026-05-04` branch)
**State pre-restart:** tombstone removed, streak file removed, DB pause row=`false`, working tree on PR-A branch with all 12 `exc_info=True` edits

---

## Capture status

`PENDING` — monitor armed at 22:32 CDT (timeout 22:54 CDT). First scheduled `opening_hunt` cycle at 22:47 CDT (~14 min).

---

## Outcome (filled by acceptance gate)

`<TBD: SUCCESS_PATH | TRACEBACK_PATH | BLOCK_AND_ROLLBACK>`

### If SUCCESS_PATH

Cycle line emitted with `candidates >= 1`. D1 was either latent (data shape changed since last fire) or fixed by another commit on parent branch. No code change required for D1.

### If TRACEBACK_PATH

Verbatim stderr block (Python traceback follows). Identify file:line of the ultimate `raise ValueError(...)`.

```
<paste verbatim stderr block here>
```

**Identified raise site:** `<file:line>`
**Exception class + message:** `<class>: <message>`
**Match against ROOT_CAUSE.md candidates:**
- [ ] `evaluator.py:714` (_normalize_temperature_metric — high/low)
- [ ] `evaluator.py:1414` (entry provenance context required)
- [ ] `evaluator.py:3478` (ENS snapshot missing fetch_time)
- [ ] `evaluator.py:3395, 3462, 3493, 3633, 3739-3787` (p_raw_topology schema)
- [ ] `evaluator.py:141, 153` (feature flag bool validation)
- [ ] `evaluator.py:1341` (FDR_SELECTED_EDGE_UNEXECUTABLE)
- [ ] OTHER: `<file:line>`

### Fix scope decision

- [ ] **Surgical (1-2 lines)** → bundle into PR-A same commit (`git commit --amend` or new commit on same branch)
- [ ] **Larger (multi-file, contract change)** → split to `live-block-D1-fix-<scope>-2026-05-04` branch

### If BLOCK_AND_ROLLBACK

Reason: `<no cycle in 22 min | daemon crashloop | partial output | mid-cycle hang | other>`

Rollback executed per EXECUTION_CHECKLIST Step 9. Daemon back to `<state>`. Re-attempt scheduled: `<when/how>`.

---

## Acceptance per FIX_PLAN A0.8 (HARDENED)

> Two acceptable outcomes:
> (a) `Cycle ...: N candidates` with N≥1 → success path verified
> (b) `Evaluation failed for ... <message>` followed by full Traceback in stderr → traceback path verified
> Anything else → BLOCK and rollback.

Status: `<PENDING | OK_a | OK_b | BLOCKED>`

---

## Monitor command

```bash
tail -F logs/zeus-live.err 2>&1 | grep --line-buffered -E \
  'Evaluation failed|Monitor failed|Traceback|ValueError|Entry path raised|Cycle (opening_hunt|update_reaction|day0_capture):|candidates,|auto_pause|degraded|Killed|Booted|Started|FATAL|FAIL'
```

Filter widened beyond checklist to include `auto_pause` (re-pause signal), `degraded` (cycle-degraded marker), `Killed|FATAL` (process-level failures). Coverage check: every terminal state (success cycle / D1 traceback / re-pause / crash) emits at least one matched line.
