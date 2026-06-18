<!--
Created: 2026-06-08
Last reused or audited: 2026-06-08
Authority basis: architecture/cascade_liveness_contract.yaml
  (terminal_states_with_operator_action: settlement_commands.REDEEM_OPERATOR_REQUIRED,
   wrap_unwrap_commands.WRAP_FAILED); system_decomposition_plan.md §4.3 / §8 Step 2 (P4
   post-trade-capital lift — these state machines now run in the P4 daemon).
-->

# Manual Fallback Runbook — stuck REDEEM / WRAP commands

Operator-completion runbook referenced by `architecture/cascade_liveness_contract.yaml`
for the two terminal states that require a human action:

- `settlement_commands.REDEEM_OPERATOR_REQUIRED` (`max_age_hours: 24`)
- `wrap_unwrap_commands.WRAP_FAILED` (`max_age_hours: 24`)

This file exists so the cascade-liveness antibody
(`tests/test_cascade_liveness_contract.py::test_operator_runbook_files_exist`) resolves the
documented manual path — a dangling reference means an operator hit a stuck command and the
runbook the contract promised did not exist.

## 0. Who drives these state machines now (post-P4)

After the process-topology refactor (`system_decomposition_plan.md` §8 Step 2), the
post-trade capital pollers — `harvester`, `redeem_submitter`, `redeem_reconciler`,
`wrap_intent_creator`, `wrap_submitter`, `wrap_reconciler` — run in the **P4
post-trade-capital daemon** (`com.zeus.post-trade-capital` → `src.ingest.post_trade_capital_daemon`),
not the order daemon. Before touching a row by hand, confirm that daemon is alive — a stuck
command is far more often "the poller process is down" than "the command genuinely needs an
operator".

```
launchctl print gui/$(id -u)/com.zeus.post-trade-capital | grep -E 'state|pid'
tail -n 50 logs/post_trade_capital*.log
```

If the daemon is down, restart it (per the live runbook) and re-check the command after one
poll interval — most rows clear themselves once the poller resumes.

## 1. `REDEEM_OPERATOR_REQUIRED` (settlement_commands, trades.db)

A redeem command lands here when the automated `redeem_submitter` / `redeem_reconciler`
path could not confirm the on-chain redeem and escalated to a human (e.g. the Karachi
2026-05-17 case: a settled-but-unredeemed position that the automated path could not close).

**Diagnose**

```sql
-- trades.db
SELECT command_id, condition_id, asset, state, requested_at,
       (julianday('now') - julianday(requested_at)) * 24 AS age_hours
  FROM settlement_commands
 WHERE state = 'REDEEM_OPERATOR_REQUIRED'
 ORDER BY requested_at;
```

Investigate the on-chain truth for that `condition_id` (block explorer / positions API):
has the redeem actually landed on chain, or is the position genuinely unredeemed?

**Resolve (redeem already on chain, only the DB record is missing)**

Record the confirmed redeem tx so the state machine advances to `REDEEM_CONFIRMED`:

```
python -m scripts.operator_record_redeem <condition_id> <tx_hash>
```

**Escalate (redeem cannot be completed within `max_age_hours: 24`)**

Per the contract `escalation_action`: trigger Path C (per the deep-alignment-audit
SCAFFOLD §I.4) and arm the manual fallback. Do NOT leave the row aging silently — the
`test_no_operator_required_row_exceeds_max_age` data check fires once a row exceeds 24h, and
an un-redeemed settled position is locked capital.

## 2. `WRAP_FAILED` (wrap_unwrap_commands, world DB)

A wrap/unwrap command lands here when `wrap_submitter` / `wrap_reconciler` could not confirm
the on-chain wrap.

**Diagnose**

```sql
-- world DB
SELECT command_id, state, requested_at,
       (julianday('now') - julianday(requested_at)) * 24 AS age_hours
  FROM wrap_unwrap_commands
 WHERE state = 'WRAP_FAILED'
 ORDER BY requested_at;
```

Find the root cause (gas, allowance, RPC, balance) from the P4 daemon logs around
`requested_at`.

**Resolve**

Record the resolution note (per contract `cli_invocation`):

```
python -m scripts.operator_record_wrap <command_id> --resolution "<note>"
```

After the root cause is fixed, the normal path re-queues a fresh wrap via
`enqueue_wrap_if_balance_above_threshold` (`src/execution/wrap_unwrap_commands.py`) on the
next P4 poll — do NOT hand-insert a duplicate `WRAP_REQUESTED` row; the enqueue is the
sanctioned producer.

## 3. Invariants to preserve while operating by hand

- All writes go through the sanctioned helpers / scripts above — never an independent
  cross-DB connection. INV-37 (ATTACH+SAVEPOINT) still governs any cross-DB write; the P4
  split relocated which process owns the transaction, it did not relax the law.
- The redeem enqueue is idempotent (the `ux_settlement_commands_active_condition_asset`
  UNIQUE index): a re-run cannot double-enqueue an active redeem. Prefer re-running the
  sanctioned path over manual row surgery.
