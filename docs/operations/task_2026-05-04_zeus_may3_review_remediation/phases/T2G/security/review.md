# T2G B1 Security Review (K0 LIVE BOUNDARY) — 10 adversarial probes

## Threat model recap

T2G converts a fail-loud → fail-open transition for one specific exception
class: `sqlite3.OperationalError("database is locked")`. Pre-T2G, this crashed
the daemon (oncall-visible). Post-T2G, it degrades the cycle to read-only and
emits a typed counter (dashboard-visible). The security correctness depends on:
exception narrowness, observability of degrade, no write-after-degrade leak,
no masking of RED states, and operator awareness of the silent-skip behavior.

## Probe table

| # | Probe | Verdict | Evidence (file:line) | Severity |
|---|-------|---------|----------------------|----------|
| 1 | Exception class narrowness — only the lock case degrades; everything else propagates | PASS — `connect_or_degrade` at `src/state/db.py:138-144` matches `str(exc).startswith("database is locked")` and re-raises any other `OperationalError`. Tests `test_non_lock_operational_error_propagates` (line 134) and `test_non_lock_operational_error_does_not_increment_counter` confirm "no such table: foo" and "disk I/O error" both propagate without counter increment. SQLITE_BUSY ("database is locked") and SQLITE_LOCKED ("database table is locked") are correctly distinguished by prefix — confirmed via Python prefix check on canonical SQLite messages. | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/state/db.py:131-144`; `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/tests/test_cycle_runner_db_lock_degrade.py:134-150` | N/A |
| 2 | Case sensitivity / cross-platform SQLite message stability | PASS — Python `sqlite3` lowercases canonical messages identically across macOS and Linux (CPython binds same SQLite source). The startswith handles trailing-text variants (`(5)`, error codes). Counter test uses raw `sqlite3.OperationalError("database is locked")` without code suffix, matching production fixture. | Probe-derived: `'database is locked (5)'.startswith('database is locked')` → True; `'database table is locked'.startswith(...)` → False | N/A |
| 3 | Degrade ≠ silent skip — counter AND signal both fire | PASS — `_handle_db_write_lock` at `src/state/db.py:111-128` increments `db_write_lock_timeout_total` AND emits BOTH `logger.warning(telemetry_counter event=...)` AND `logger.error(ALERT db_write_lock_timeout: ...)`. Cycle_runner at `src/engine/cycle_runner.py:541-549` adds `summary["db_write_lock_degraded"]=True`, `summary["skipped"]=True`, `summary["skip_reason"]="db_write_lock_degraded"` — three independent observability signals. Counter is read-back verified in `test_db_lock_increments_typed_counter_via_sink`. | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/state/db.py:118-128`; `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/engine/cycle_runner.py:541-549` | N/A |
| 4 | No write attempt on degrade path | PASS — `run_cycle` at line 536 acquires conn; line 541 checks `if conn is None: ... return summary`. The early `return` precedes ALL downstream code (line 551 `load_portfolio()`, line 599 `PolymarketClient()`, line 605 `_reconcile_pending_positions`, line 612 `_run_chain_sync`). No write or SDK call can execute. The separate `_execute_force_exit_sweep` function at line 156 takes `conn` as a parameter and guards `if conn is not None:` before any write — also safe. | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/engine/cycle_runner.py:536-549`; force-exit guard at `:170,192` | N/A |
| 5 | Race vs concurrent writer — half-done state from prior cycle | LOW — `_connect` at `src/state/db.py:67-76` opens a fresh connection per `get_connection()` call; no transaction is held across cycles. `database is locked` fires at PRAGMA execute step (statement-level), so prior cycle's writes were either fully committed or fully rolled back via Python's implicit transaction management. WAL journal mode (line 74) ensures crash-safety: prior writer's commit is durable before lock is released. **However**, if the prior cycle was interrupted MID-cycle (e.g., daemon restart) and the next cycle hits a stale `.db-shm` lock, that's an OS-level race, not a T2G regression. T2G's behavior is correct: it observes lock, degrades, retries next cycle. | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/state/db.py:67-76,131-144` | LOW (existing WAL semantics, not T2G regression) |
| 6 | Daemon crash vs degrade for oncall visibility | PASS-with-MEDIUM-CAVEAT — `_handle_db_write_lock` at `src/state/db.py:124-128` emits `logger.error("ALERT db_write_lock_timeout: ...")` — this is `ERROR` level (not WARNING), so any oncall log-grep alerting on ERROR will fire. PLUS counter emit. PLUS cycle-summary fields. **MEDIUM caveat for oncall**: pre-T2G a crash produced launchd respawn churn (one specific signature). Post-T2G the daemon stays up indefinitely on persistent lock contention — if oncall alerting is wired ONLY to launchd-respawn-counts and NOT to the new `db_write_lock_timeout_total` counter or the ALERT log line, persistent lock contention becomes invisible. T2G's invariant is correct; the operator alert-rule update is what's missing. | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/state/db.py:120-128` (logger.warning + logger.error both) | MEDIUM (alert-rule migration; out of T2G code scope but in operator-disclosure scope) |
| 7 | Partial-commit on degrade mid-cycle | PASS-with-NOTE — `connect_or_degrade` is called at connection-OPEN time only. The lock failure fires at `sqlite3.connect(...)` or PRAGMA execute, BEFORE any cycle work begins. No transaction has started → no partial commit possible. **However**, if the cycle acquires a connection successfully and a downstream write hits "database is locked" mid-cycle, that exception flows through existing per-write `except sqlite3.OperationalError` handlers (`src/state/db.py:1475-1589`) which were T1E territory, NOT T2G's degrade-at-acquisition path. T2G's scope is precisely connection acquisition. Mid-cycle write-lock semantics are unchanged from pre-T2G (existing handlers, not silently swallowed by T2G). | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/state/db.py:67-76,131-144`; mid-cycle handlers at db.py:1475-1589 | LOW (boundary clarification, not regression) |
| 8 | Settlement command enqueue path threat — wrapper validation surface | PASS — `enqueue_redeem_command` at `src/execution/harvester.py:479-518` passes through `condition_id`, `payout_asset`, `market_id or condition_id`, `pusd_amount_micro`, `token_amounts or {}` to `request_redeem` without modification. Default-empty fallbacks `(market_id or condition_id)` and `(token_amounts or {})` mirror the inline block's behavior at the pre-T2G line 2006-2025. The try/except + `logger.warning` "Redeem deferred for %s" pattern is preserved verbatim. Behavioral parity confirmed. The wrapper does NOT add new validation but also does NOT remove any — exact-equivalent forwarding. | `/Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main/src/execution/harvester.py:479-518`; pre-T2G inline block from git diff lines 2006-2025 | N/A |
| 9 | Wrapper bypass — other `request_redeem` call sites? | PASS — `git grep -nE "request_redeem\\(\|from .* import .*request_redeem" src/` returns: `harvester.py:499` (import inside enqueue_redeem_command body), `harvester.py:501` (the call), `settlement_commands.py:199` (definition). Zero other src/ call sites. The `harvester.py:2019,2020` matches in the diff are comments, not calls. Wrapper is load-bearing — single-entry-point invariant `T2G-REDEEM-STATE-TRANSITION-AUDITABLE` holds. | `git grep` output: 1 import + 1 call + 1 def in src/; AST test `test_no_inline_request_redeem_in_src` enforces this | N/A |
| 10 | Operator-visible behavior change disclosure quality | NEEDS-MORE — disclosure exists in 4 packet docs (`phases/T2G/phase.json` `_planner_notes.operator_awareness`, `phases/T2G/scope.yaml:46`, `planner_output.md`, `MASTER_PLAN_v2.md`, `T0_SQLITE_POLICY.md`, `critic_round5_response.md`). NO top-level operator runbook (`docs/runbooks/`, `docs/operations/RUNBOOK.md`) was found via `find` to surface this for an oncall reading runtime docs. The semantic change is non-trivial (daemon stops crashing → oncall must learn new alert-counter), and a one-line entry in a daemon ops runbook would make the migration mechanical. Currently it requires reading deep into packet docs. | `find docs/operations/task_2026-05-04_zeus_may3_review_remediation -name "*.md" | xargs grep -l "database is locked\|db_write_lock\|degrade"` → 4 hits; no `docs/runbooks/` or top-level operator-facing docs found | MEDIUM (out of T2G code scope; carry-forward to operator-disclosure packet) |

## Cross-cutting findings

### Finding A — Hidden ATTACH partial-degrade in cycle_runner wrapper (LOW)

The new `get_connection` wrapper at `src/engine/cycle_runner.py:69-85` (per the
diff, function body lines 69-85 of the post-T2G file) does:

```
try:
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" not in attached:
        conn.execute("ATTACH DATABASE ? AS world", (str(_world_path),))
except Exception:
    pass
return conn
```

Adversarial probe: if the trade DB connect succeeds (lock released) but the
ATTACH against `ZEUS_WORLD_DB_PATH` fails because the world DB is locked
or unreachable, the bare `except Exception: pass` SILENTLY returns a
trade-only connection. Downstream code accesses `world.ensemble_snapshots`,
`world.settlements`, etc. (verified at `src/engine/evaluator.py:3484,3494`,
`src/engine/replay.py:2313`) and would raise `OperationalError("no such
table: world.X")` — which then propagates uncaught (T2G doesn't degrade on
that). The cycle would then crash on the FIRST query touching world schema,
NOT degrade. This is NOT a write-after-degrade leak (no false success), but
it IS a CONFUSING failure mode: the lock-degrade signal is suppressed and
replaced by a "no such table" error that looks like schema corruption.

**Severity LOW** because (a) the failure is loud (raises), (b) it occurs
later not in a write path, and (c) the alternative — calling `connect_or_degrade`
on the world DB too — would require widening T2G scope. Recommend either
narrowing the bare-except to `sqlite3.OperationalError` and re-raising
on lock-class errors, OR adding a `summary["world_attach_failed"]=True`
signal. Carry-forward to a future hardening packet.

### Finding B — `summary["skipped"]=True` is ambiguous (LOW)

The degrade path sets `summary["skipped"]=True` (line 543). The same key
is used elsewhere in cycle_runner for non-lock skip reasons. Downstream
analyzers reading `summary["skipped"]` cannot distinguish lock-degrade
from other skip causes without also reading `skip_reason`. This is
correct (skip_reason disambiguates) but observability tooling that grep's
on `skipped=True` alone could produce false-positive lock counts. LOW.

## Cross-phase regression check

`git diff --stat HEAD` confirms only the in-scope files (`cycle_runner.py`,
`harvester.py`) and 2 new tests are modified. T1A/T1F/T1G surfaces clean.
T1E primitive at `src/state/db.py:111-144` is unmodified (consumed only).
T2F sink consumed via `src.observability.counters.read/increment` — no
modification. T1C `request_redeem` at `settlement_commands.py:199` unmodified.

## T2G Security Verdict

SECURITY_DONE_T2G
verdict: APPROVE_WITH_MITIGATIONS
exception_narrowness: strict_lock_only
silent_degrade_risk: mitigated_by_three_signals_counter_alert_log_summary_field
write_after_degrade_risk: none
race_vs_concurrent_writer_risk: low_documented
partial_commit_on_degrade_risk: none
wrapper_bypass_risk: none
operator_disclosure_quality: needs_more
critical_high_medium_count: 0/0/2
mitigations_required:
  - "MEDIUM: oncall alert-rule migration from launchd-respawn-counts to db_write_lock_timeout_total counter and ALERT log line (Probe 6 caveat)"
  - "MEDIUM: surface operator-visible behavior change in a top-level operator runbook (docs/runbooks/ or equivalent), not just deep packet docs (Probe 10)"
fix_required: []
