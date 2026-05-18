# OPS_FORENSICS — F8/F11/F22/F39 current state

**Authority**: Sonnet ops-forensics 2026-05-17T13:24Z (re-dispatched after haiku failed without Write permission).
**Status**: All 4 findings have current-state evidence + fix-shape OR operator-decision-needed.

---

## F11 — plist KeepAlive (REVISED understanding)

Earlier audit (RUN_8) recommended adding `KeepAlive=true` to `com.zeus.heartbeat-sensor.plist` + `com.zeus.calibration-transfer-eval.plist`. **This recommendation is ARCHITECTURALLY WRONG and must NOT be applied.**

### heartbeat-sensor
- StartCalendarInterval: `:28` and `:58` past every hour (twice-hourly probe, NOT daemon)
- `last exit code = 0`, runs = 650 (cleanly exiting, not crashing)
- `PID = -` between fires is EXPECTED (calendar-fire job, not long-lived process)
- **Real finding**: heartbeat-sensor.err contains `severity=RED root_cause=daemon_dead` ×84 + `severity=ORANGE root_cause=assumption_mismatch` — these are OUTBOUND alerts the sensor emits about the main daemon being dead. The sensor itself is healthy.

### calibration-transfer-eval
- StartCalendarInterval: Sunday 04:00 only (Weekday=0, Hour=4)
- Last 2 runs (2026-05-10 + 2026-05-17): clean exit, 0 rows written (because F41 reads dead K1 source)
- KeepAlive wrong here too (periodic, not daemon)

**F11 verdict**: NO-OP. Don't add KeepAlive. Promote the sensor's RED alerts to a real escalation path (F33 territory) instead.

---

## F8 — sentinel `unknown_entered_at` current count

### Live count
- **3 rows** in `position_events` (grew from 2 since RUN_8)
- All three: `event_type=CHAIN_SYNCED`, `phase_before=pending_entry`, `phase_after=active`, `source_module=src.state.chain_reconciliation`
- **Karachi `c30f28a5-d4e` is one of the 3** — confirmed present

### Two emit points (`src/state/chain_reconciliation.py`)

1. **Line 658** — rescue path: fires when `rescued.entered_at` is falsy. **Fix**: use `now` (already in function scope, line 661 uses it as `_rescue_display_ts`) — drop-in replacement, no behavior change.

2. **Line 808** — quarantine placeholder: deliberate sentinel pattern, fields are all `QUARANTINE_SENTINEL`. Either use `quarantined_at=now` OR add explicit filter `is_quarantine_placeholder=True`.

### Fix shape

```python
# Line 658 BEFORE
occurred_at = rescued.entered_at or "unknown_entered_at"

# Line 658 AFTER
occurred_at = rescued.entered_at or now  # `now` already in scope at line 661
```

### Antibody (CHECK constraint on schema)
```sql
ALTER TABLE position_events ADD CONSTRAINT chk_occurred_at_iso 
    CHECK (occurred_at LIKE '____-__-__T%' OR occurred_at = 'quarantine_sentinel');
```

(Note: SQLite CHECK constraints require table rebuild; do as part of F23 migration runner work.)

### Backfill the 3 existing rows
For each of the 3 sentinel rows: look up the position's `chain_verified_at` or `quarantined_at` from `position_current`, UPDATE `occurred_at`. Or annotate `occurred_at='QUARANTINE'` if it's truly the quarantine path.

**Karachi blast radius**: LOW — the sentinel does not block cascade routing (by position_id + condition_id), but corrupts temporal queries. For the `c30f28a5-d4e` row specifically: lexicographic sort puts `unknown_entered_at` < any 2026 timestamp → it sorts to TOP of position timeline → operator-confusing during post-settlement audit.

---

## F22 — operator script raw-connect triage (TOP-5 dangers)

Total raw read-write `sqlite3.connect` (no `?mode=ro`) sites: **43 scripts**.
Operator-action subset: **12 scripts**.

### Top-5 most dangerous during live trading

| # | Script | Last touched | Lock? | Targets | Hazard |
|---|---|---|---|---|---|
| 1 | `scripts/migrations/202605_add_redeem_operator_required_state.py` | 2026-05-16 | NO (fcntl only) | all 4 DBs | DDL rebuilds settlement_commands during live; races daemon writes |
| 2 | `scripts/migrate_world_observations_to_forecasts.py` | 2026-05-15 | NO | forecasts.observations + market_events_v2 | Docstring says "stop ingest" but no enforcement |
| 3 | `scripts/bridge_oracle_to_calibration.py` | 2026-05-05 | NO | reads zeus-world.db | (F40 root cause itself + no lock) |
| 4 | `scripts/cleanup_ghost_positions.py` | 2026-05-07 | YES | trade_decisions | Read-classify race outside lock window |
| 5 | `scripts/force_cycle_with_healthy_gates.py` | 2026-05-08 | YES | control_overrides_history | Suppresses legitimate bid-safety check |

### Antibody (CI scan)
```python
# tests/test_operator_script_lock_contract.py
def test_no_raw_connect_without_lock_in_operator_scripts():
    """Operator-named scripts touching state/*.db must acquire writer-lock."""
    for script in glob("scripts/{operator_*,arm_*,cleanup_*,force_*,bridge_*,migrate_*}.py"):
        content = Path(script).read_text()
        if "sqlite3.connect(" not in content:
            continue
        if "?mode=ro" in content:
            continue  # read-only acceptable
        assert "db_writer_lock" in content or "register_known_connection" in content, (
            f"{script}: raw read-write connect without writer-lock contract"
        )
```

---

## F39 — calibration-transfer-eval plist intent vs reality

### Plist header (verbatim, lines 3-17)
```
PROPOSED — DO NOT LOAD AUTOMATICALLY.
Authority basis: golden-knitting-wand.md Phase 1 Fix F — OOS staleness antibody
Purpose: weekly re-evaluation of validated_calibration_transfers evidence rows.
...
DO NOT load at initial launch — zero cross-domain rows exist until ECMWF
calibration_pairs_v2 accumulate (~2-4 weeks post-launch).
```

### Reality
- `launchctl print` shows `state = not running`, `runs = 2`, `last exit code = 0`
- Despite "DO NOT LOAD", the plist IS loaded (ran 2026-05-10 + 2026-05-17 04:00)
- Both runs: `active_platt_models_iterated: 0`, `rows_written: 0`, `target domains in calibration_pairs_v2: []` → reads dead K1 source (F41)

### Operator decision matrix

| Option | Command | Cost | Benefit |
|---|---|---|---|
| **(A) Unload now** (RECOMMENDED) | `launchctl bootout gui/$(id -u)/com.zeus.calibration-transfer-eval` | None — script does zero work | Removes invisible weekly precondition-mask; restore when Phase B genuinely triggers |
| (B) Keep loaded + fix F41 | Requires F41 K1 reader repoint to ship | More LOC churn for productive eval | Eval starts producing real rows |

**Recommended**: (A) immediately. Re-evaluate after Phase B trigger conditions in `ecmwf_opendata_tigge_equivalence_2026_05_06.yaml §5` are met.

---

## TOP-3 OPERATOR ACTIONS before next Karachi-class settlement

1. **Unload calibration-transfer-eval plist** — SUPERSEDED 2026-05-17 by F39 structural fix: `scripts/evaluate_calibration_transfer_oos.py` now self-policing (early-exit on `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED != "1"`). Plist may remain loaded; no operator action needed.
2. **3 `unknown_entered_at` sentinel rows — SUPERSEDED 2026-05-17 by structural fix**: `src/engine/lifecycle_events.py::_non_empty` skips the sentinel; F8 CHECK constraint blocks new occurrences. Historical 3 rows are tolerated by readers; do NOT operator-SQL them. If cleanup desired, write a programmatic backfill helper analogous to `src/state/trade_decisions_synthesizer.py` (per `feedback_no_manual_precedent_for_any_structural_defect`). Deferred to WAVE-3.
3. **Do NOT run `migrations/202605_add_redeem_operator_required_state.py` while live daemon active** — script lacks writer-lock + writer-stop enforcement; add hard fcntl LOCK_EX check on zeus-live.db before allowing re-run (tracked under F22 WAVE-3 carry-forward).

## NOT-fixes (audit corrections)

- F11 KeepAlive recommendation = REJECTED (architectural mismatch with calendar-fire plists)
- F11 heartbeat-sensor RED alerts = should be promoted to RiskGuard escalation (F33 territory), not silenced
