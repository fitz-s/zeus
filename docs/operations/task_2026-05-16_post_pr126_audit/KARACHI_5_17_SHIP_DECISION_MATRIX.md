# Karachi 2026-05-17 ship-decision matrix

**Position under decision**: `c30f28a5-d4e` — Karachi weather market, $0.59, 1.5873 shares, cost_basis $0.5873, strategy `opening_inertia` / `ens_member_counting`, env=`live`.
**Secondary position in scope**: `bf0a16f5-f95` — $1.0, 100 shares (status unverified; appears to be a separate paper or experimental position).
**Time-of-decision**: 2026-05-17 (write time of this document).
**Cascade reference**: `cascade_liveness_contract.yaml` 5-link chain.

---

## Cascade link status

| Link | Component                  | Health  | Evidence                                                                          | Operator action needed (pre-event)        |
|------|----------------------------|---------|-----------------------------------------------------------------------------------|-------------------------------------------|
| L1   | Forecast (forecast-live)   | GREEN   | plist KeepAlive=true; market_events_v2 zeus-forecasts.db=10,541 rows (lead).      | None.                                     |
| L2   | Selection (cycle_runtime)  | YELLOW  | F25 19,175 rows have NULL decision_snapshot_id (audit-only, not blocking).        | None for ship. Note audit-trace gap.      |
| L3   | Execution (live-trading)   | GREEN   | plist KeepAlive=true; SettlementState path PR-126 wired and tested.               | None.                                     |
| L4   | Settlement                 | YELLOW  | F27: UNIQUE INDEX intentionally blocks REVIEW_REQUIRED. F15: legacy/v2 gap 1583.  | None for THIS position (unique triple).   |
| L5   | Redeem                     | YELLOW  | REDEEM_OPERATOR_REQUIRED is non-terminal-with-operator-action. CLI exists.        | Operator MUST run `operator_record_redeem.py` when state lands in OPERATOR_REQUIRED. |

**Overall verdict**: **GO** for Karachi 5/17 settlement and redeem of `c30f28a5-d4e`. All YELLOW conditions are either audit-only (L2, L4 partial) or require known runbook steps (L5).

---

## GO/NO-GO checklist

### MUST-VERIFY pre-event (operator, within 6 hours of settlement window)

1. **L5 operator CLI dry-run**: `python scripts/operator_record_redeem.py --help` returns 0 and shows expected flags. Time: 30 sec.
2. **L3 plist live**: `launchctl list | grep com.zeus.live-trading` shows PID. Time: 5 sec.
3. **L4 schema check**: `python3 -c "import sqlite3; c=sqlite3.connect('state/zeus_trades.db').cursor(); c.execute('SELECT COUNT(*) FROM settlement_commands').fetchone()"` returns a count (table exists). Time: 5 sec.
4. **Sentinel awareness**: `position_events` for `c30f28a5-d4e` contains 1 row with `occurred_at='unknown_entered_at'` (F8). This is EXPECTED. Do not treat as corruption.
5. **F27 awareness**: if any operator script attempts to insert a 2nd settlement_command for `(condition_id, market_id, payout_asset)` of this position while the first is in REDEEM_OPERATOR_REQUIRED or REDEEM_REVIEW_REQUIRED, sqlite3 will raise `IntegrityError: UNIQUE constraint failed: ux_settlement_commands_active_condition_asset`. This is BY DESIGN (F27). Operator must transition the existing row out via `operator_record_redeem.py` before retrying.

### HARD-STOP triggers (NO-GO if any TRUE within 1 hour of event)

- L1 forecast-live plist not running (KeepAlive should auto-restart; manual: `launchctl kickstart -k gui/$(id -u)/com.zeus.forecast-live`).
- L3 live-trading plist not running (same kickstart pattern).
- `state/zeus_trades.db` lock contention >30 sec (`PRAGMA database_list` + `lsof state/zeus_trades.db` to see who holds it).
- Collateral ledger snapshot stale by >1 hour (check `risk_state.db` `collateral_snapshots.captured_at` MAX).
- ANY change to `src/execution/settlement_commands.py` since main HEAD `9259df3e9c` without operator sign-off.

### SOFT-WARN (proceed with elevated monitoring)

- F8 sentinel row count grows beyond 2 between now and settlement (`SELECT COUNT(*) FROM position_events WHERE occurred_at='unknown_entered_at'`). Indicates new pending-fill-rescue events firing → investigate before next settlement, but does not block THIS one.
- F25 NULL count grows >5% in the next hour (`SELECT COUNT(*) FROM opportunity_fact WHERE snapshot_id IS NULL AND recorded_at > <now>`). Indicates evaluator regression. Audit-only — no GO/NO-GO impact.

---

## Per-link operator runbook (pasted from cascade_liveness_contract.yaml shape)

**L4→L5 happy path**:
1. live-trading writes `settlement_commands(state=REDEEM_INTENT_CREATED)`.
2. Scheduler tick advances to `REDEEM_SUBMITTED`.
3. Adapter returns either:
   - real tx hash → `REDEEM_TX_HASHED` → `REDEEM_CONFIRMED` (autonomous), or
   - `REDEEM_DEFERRED_TO_R1` stub → `REDEEM_OPERATOR_REQUIRED` (STOP, await operator).
4. Operator runs `python scripts/operator_record_redeem.py --command-id=<id> --tx-hash=<actual> --confirm`.
5. State transitions to `REDEEM_TX_HASHED` → `REDEEM_CONFIRMED` on next tick.

**L4→L5 unhappy path (REVIEW_REQUIRED)**:
1. Adapter detects an unrecoverable invariant violation.
2. State moves to `REDEEM_REVIEW_REQUIRED` (terminal, blocking per F27).
3. Operator MUST investigate `error_payload` JSON. No automatic retry.
4. To unblock for a fresh attempt: investigate root cause, then `UPDATE settlement_commands SET state='REDEEM_FAILED', terminal_at=<now> WHERE command_id=<id>` (manual; documented in cascade contract as the only escape hatch).

---

## Stop-line summary

| Question                                                       | Answer for `c30f28a5-d4e` |
|----------------------------------------------------------------|----------------------------|
| Will L1-L3 plist crashes auto-recover?                         | YES (KeepAlive=true)       |
| Will L4 block a 2nd same-triple settlement?                    | YES (F27 by design)        |
| Does L5 require manual operator action?                        | LIKELY (adapter stubbed)   |
| Are audit-trace gaps blocking?                                 | NO (F25 audit-only)        |
| Is the F8 sentinel safe to ignore in operator dashboards?      | YES (timestamp-only oddity)|

**Final**: **GO**. Operator on call for L5. No code changes between now and event window.
