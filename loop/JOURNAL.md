# Loop Journal (append-only)

Who writes: `loop/tick.sh` (v3 single tick, cadence = `loop/INTERVAL` hours)
— each invocation appends exactly one block after it finishes, plus
mechanical `VIOLATION:`/`ESCALATION:`/`FALLBACK` lines from
`scripts/ops/loop_guard.py` when the wrapper's own safety checks fire.
Entries are never edited or rewritten after being appended.

Who reads: the next tick (orients from the tail before doing anything —
see `loop/prompts/l1.md` ORIENT step) and the operator (the report is this
file's tail; there is no separate digest file). The last `cursor:` line is
the settlement-join resume point.

**If this file silently stops growing, the loop is dead.** Check
`launchctl list | grep com.zeus.loop`, then `loop/HALT`, then
`loop/INTERVAL` (a big value = long silence is expected), then
`loop/logs/tick-*.log` for the most recent run — in that order.

## Format spec

One block per tick, oldest first:

```
## <ISO8601 UTC timestamp> L1 tick
queue_item: <ledger id | "none">
action: <one-line what happened>
verifier: PASS | FAIL | N/A
commit: wrapper
notes: <optional free text>
```

An empty tick (no new evidence, nothing queued) still gets one line so a gap
in this file is diagnostic, not ambiguous:

```
## <timestamp> L1 tick — empty (no new evidence, queue empty)
```

Grep-able marker lines (appear standalone, inside or after a tick block):

- `VIOLATION: <detail>` — the post-run allowlist diff check
  (`scripts/ops/loop_guard.py enforce`) found a change outside
  `loop/allowlist_auto.txt` (or a guard-immutable/cadence file); the
  offending path(s) were hard-restored and are not part of the tick's
  real output.
- `ESCALATION: <detail>` — the diff circuit breaker tripped (>20 files or
  >600 changed lines — ALL new-this-tick changes hard-restored), a DB
  sentinel delta self-halted the loop, or a queue item failed 3
  consecutive ticks. Needs operator eyes.
- `DEVIATION: <detail>` — the tick took an action outside brief
  expectations but within tier rules; logged for audit.
- `cursor: <value>` — settlement-join resume point (last processed
  settlement rowid/timestamp), read by the next tick's grading step.

---

## 2026-07-09T12:25:27Z L1 tick
queue_item: settlement-decision-join-probe-2026-07-09
action: Preregistered the settlement standing probe as a terminal-position x VERIFIED-settlement x ActionableTradeCertificate join, and opened the ledger item that makes unresolved certificate rows INSUFFICIENT rather than skill evidence.
verifier: PASS (GIT_OPTIONAL_LOCKS=0 git -C .. diff --check -- loop/queries/pending/settlements_recent.sql loop/LEDGER.yaml; rg single read-only SELECT in loop/queries/pending/settlements_recent.sql)
commit: wrapper
notes: Settlement grading not performed this tick: the available result file contained VERIFIED settlements only, without frozen decision certificate fields, so updating the scoreboard would violate decision-certificate x settlement ground truth. No cursor advanced.
