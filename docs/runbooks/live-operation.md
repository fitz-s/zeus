# Zeus Live Operation Runbook

Authority: `docs/authority/zeus_current_delivery.md`
Applies to: Day-to-day live daemon operation, Phase 1.

---

## Healthy signature

A healthy Zeus live cycle produces this log pattern (abridged):

```
INFO [src.main] Startup wallet check: $NN.NN USDC available
INFO [src.engine.cycle_runner] === OPENING_HUNT cycle start ===
INFO [src.engine.cycle_runner] chain reconciliation: {synced: N, voided: 0, quarantined: 0}
INFO [src.engine.cycle_runner] evaluator: N candidates, M edges, K entries gated
INFO [src.engine.cycle_runner] === OPENING_HUNT cycle complete in N.Ns ===
INFO [src.execution.harvester] harvester cycle start
INFO [src.execution.harvester] harvester cycle complete
INFO [zeus.riskguard] tick OK: all rules GREEN
```

Healthy = no `CRITICAL`, no `FATAL`, riskguard tick reports GREEN.

### Expected Phase 1 warnings (not errors)

These appear in logs and are **normal** during Phase 1:

| Warning | Meaning |
|---------|--------|
| `insufficient_history` | Market has <N prior samples; FDR filter conservatively rejects. Normal while bootstrapping. |
| `calibration not mature` | Platt model trained on <min_samples. Uses prior until mature. |
| `DATA GAPS: ...` | Some ETL tables empty or missing. Run ETL scripts to populate. |
| `DEFERRED ACTION: bias_correction_enabled=false` | Bias correction not yet activated. Intentional during Phase 1. |
| `INCOMPLETE CHAIN RESPONSE` | Chain API returned 0 positions; reconciliation void skipped to protect positions. Monitor frequency. |

### Operator-visible metadata (NOT decision inputs)

- `capped_by_safety_cap`: logged when a position size was clipped by `live_safety_cap_usd`. This is an audit field for the operator. It does NOT feed back into any signal or decision. Do not treat it as a strategy output.
- `reason_code` in control state: operational metadata. Describes why a gate was set or cleared. It is not a truth surface and does not govern trading. See `docs/authority/zeus_current_delivery.md` and `docs/authority/zeus_current_architecture.md` for control state authority rules.

### Config key note

There are no `paper_*` counterparts to `live_*` config keys. Zeus is live-only; backtest evaluates and shadow observes, but neither is a peer execution mode. A key named `live_safety_cap_usd` is live execution policy, not a paper-mode setting.

---

## Kill-switch

One command to stop the daemon immediately:

```bash
pkill -f 'python -m src.main'
```

Verify stopped:

```bash
ps aux | grep 'src.main' | grep -v grep
# Should return nothing
```

If running via launchd:

```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.zeus-live.plist
```

**After kill**: all open positions are held as-is. No orders sent while daemon is stopped. Positions remain on-chain and in state files.

---

## Resume procedure

1. **Identify why it stopped** (check logs first).
2. If stopped due to RiskGuard halt: resolve the failing rule, then resume.
3. If stopped due to unhandled exception: read `CRITICAL`/`FATAL` log line, fix root cause.
4. If stopped manually: confirm positions are intact:

```bash
ZEUS_MODE=live python - <<'EOF'
from src.state.db import get_trade_connection, load_portfolio
from src.config import state_path
conn = get_trade_connection()
rows = conn.execute("SELECT COUNT(*) FROM position_current WHERE phase NOT IN ('settled','voided','admin_closed')").fetchone()[0]
print(f"Active positions in DB: {rows}")
conn.close()
EOF
```

5. Once confirmed safe, restart:

```bash
ZEUS_MODE=live nohup python -m src.main >> logs/zeus-live.log 2>&1 &
echo $! > state/zeus-live.pid
```

---

## Monitoring

### Daemon heartbeat

The daemon writes `state/daemon-heartbeat.json` every 60 seconds.

Check staleness:

```bash
python scripts/check_daemon_heartbeat.py
```

If output shows `STALE (>5 min)`, the daemon may have silently died. Check process and logs.

### Discord alerts

Key alert types and their meanings:

| Alert | Action |
|-------|--------|
| `RISKGUARD HALT` | Trading halted. Resolve failed_rules and wait for auto-resume or force-resume. |
| `RISKGUARD RESUMED` | Normal operation resumed. No action needed. |
| `WARNING: <rule>` | Approaching threshold. Monitor but continue. |
| `TOKEN REDEEMED` | Winning shares claimed on-chain. Informational. |
| `Daily Report` | Daily summary. Review PnL and calibration metrics. |
| `FIRST LIVE FILL` | First live trade executed. Verify size and position in DB. |
| `FIRST LIVE SETTLEMENT` | First live settlement. Verify PnL. |
| `WALLET DROP >X%` | Wallet balance dropped sharply. Investigate immediately. |
| `CHAIN SYNC FAILURE` | Chain reconciliation failing repeatedly. Investigate API connectivity. |
| `HEARTBEAT MISSED` | Daemon may be down. Check process. |

### Log tailing

```bash
tail -f logs/zeus-live.log | grep -E 'ERROR|CRITICAL|FATAL|HALT|QUARANTINE|PHANTOM'
```

---

## Common recovery scenarios

### PHANTOM positions (voided)

If reconciliation voids a position (Rule 2: local but not on chain), it appears in logs as:
```
WARNING PHANTOM: <trade_id> not on chain u2192 voiding
```
The position is removed from the active portfolio. Review whether the original order actually filled on-chain. If it did, the chain API response was incomplete u2014 the position should reappear next cycle via Rule 3 (QUARANTINE).

### QUARANTINE positions

Positions on-chain but not in local portfolio are quarantined (Rule 3). They expire after 48h and become eligible for exit evaluation. Review via:

```bash
ZEUS_MODE=live python - <<'EOF'
from src.state.db import get_trade_connection
conn = get_trade_connection()
rows = conn.execute("SELECT position_id, phase FROM position_current WHERE phase='quarantined'").fetchall()
for r in rows: print(dict(r))
conn.close()
EOF
```

### Pending exit stuck

If a position is stuck in `pending_exit` or `sell_placed`, check exit_lifecycle logs for the trade_id. The retry/backoff mechanism handles transient failures automatically.

## Phase C: live entry-forecast activation flags

Phase C of `docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md`
wires the previously-orphan rollout/calibration/readiness/healthcheck
machinery into the daemon hot-path behind four env flags. **All flags
default OFF**; daemon behavior at flag-default is byte-equal to
pre-Phase-C.

### Flags

| Env var | Effect when `=1` |
|---|---|
| `ZEUS_ENTRY_FORECAST_ROLLOUT_GATE` | `evaluate_entry_forecast_rollout_gate` enforces operator approval / G1 / calibration approval / canary success evidence at the rollout-blocker site (legacy rollout-mode-only check is bypassed). Reads `state/entry_forecast_promotion_evidence.json` per cycle. |
| `ZEUS_ENTRY_FORECAST_READINESS_WRITER` | `_write_entry_readiness_for_candidate` writes a `readiness_state` row with `strategy_key='entry_forecast'` per candidate per cycle. Required for `read_executable_forecast` to find a row instead of returning `READINESS_MISSING`. |
| `ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS` | `entry_forecast_blockers` participates in `result["healthy"]` predicate in `scripts/healthcheck.py`. Surfaces entry-forecast block in launchctl/dashboards. |
| `ZEUS_ENTRY_FORECAST_CALIBRATION_GATE` | RESERVED — Phase C-3 subsumed C-2's calibration check via writer-side enforcement; this flag is currently unused. |

### Flip authorization (evidence-gated)

**Authority**: `docs/operations/activation/UNLOCK_CRITERIA.md`. Each
flip must produce an evidence bundle under `evidence/activation/`
AND pass the relationship tests in
`tests/test_activation_flag_combinations.py` BEFORE the flag is set
in the running daemon. The flip commit body cites the evidence
bundle path; flips without evidence are unauthorized.

Run the producer:

```bash
python scripts/produce_activation_evidence.py --all \
  --out-dir evidence/activation/ \
  --evidence state/entry_forecast_promotion_evidence.json
```

Inspect `evidence/activation/<date>_summary.md` for per-flag
`ready_to_flip` verdicts. Stale (>7 day) bundles do not authorize.

### Recommended flip order

1. **`ZEUS_ENTRY_FORECAST_READINESS_WRITER=1` first.** Without it, the
   reader returns `ENTRY_READINESS_MISSING` regardless of other flags
   because no daemon path writes the rows. The writer enforces all
   three gates (rollout / calibration / promotion-evidence) at write
   time, so flipping it first is fail-closed: missing evidence ⇒
   BLOCKED row ⇒ reader emits typed blocker.
2. **`ZEUS_ENTRY_FORECAST_ROLLOUT_GATE=1` second.** Adds the
   rollout-gate enforcement at the upstream blocker site for
   defense-in-depth. Required if `rollout_mode='live'` so the gate
   actually checks evidence.
3. **`ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS=1` last.** Surfaces
   any blockers in `result["healthy"]` so the operator dashboard
   reflects entry-forecast state. Flip last so the dashboard signal
   matches the system's actual gating posture; UNLOCK_CRITERIA §Flag 3
   requires ≥24h of flags 1+2 stable before this flip.

**Out-of-order safety**: relationship tests INV-A through INV-E in
`tests/test_activation_flag_combinations.py` pin that no flag-subset
opens an unsafe path; all out-of-order combinations stay fail-closed.
Operationally:
- `_HEALTHCHECK_BLOCKERS=1` before `_READINESS_WRITER=1` pulls
  `result["healthy"]` False with `ENTRY_READINESS_MISSING` everywhere
  — alarms with no actionable underlying state.
- `_ROLLOUT_GATE=1` without `_READINESS_WRITER=1` and
  `rollout_mode='live'`: the gate checks pass (if evidence is
  populated) but the reader still finds no row — silent recurrent
  BLOCKED on `ENTRY_READINESS_MISSING`.

### Required prerequisite: populate promotion evidence

Before flipping `_ROLLOUT_GATE=1`, write the operator-attested evidence
to `state/entry_forecast_promotion_evidence.json`:

```python
from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
from src.data.live_entry_status import LiveEntryForecastStatus

write_promotion_evidence(EntryForecastPromotionEvidence(
    operator_approval_id="op-YYYY-MM-DD",
    g1_evidence_id="g1-YYYY-MM-DD",
    status_snapshot=LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE", blockers=(),
        executable_row_count=N, producer_readiness_count=N,
        producer_live_eligible_count=N,
    ),
    calibration_promotion_approved=True,
    canary_success_evidence_id="canary-YYYY-MM-DD",
))
```

The writer holds `fcntl.flock(LOCK_EX)` on a sidecar
`state/entry_forecast_promotion_evidence.json.lock` file (Phase C-flock).
The lock file persists between writes — that is intentional and not a
leak. Atomic JSON via `tempfile.mkstemp` + `os.replace` already
prevents readers from observing partial files.

### Day0 candidates do not produce entry_readiness rows

Phase C-6 routes Day0 candidates around the executable-forecast cutover
and back to the legacy `fetch_ensemble` path. As a side effect, the
`_write_entry_readiness_for_candidate` helper at `src/engine/evaluator.py`
sits inside the cutover branch — it is **never invoked for Day0
candidates** under any flag combination. Day0 markets will therefore
have producer_readiness rows (written by ingest) but **no
`strategy_key='entry_forecast'` rows in `readiness_state`**. This is
intentional: Day0 has its own observed-so-far signal pipeline (Day0Router
+ remaining_member_extrema_for_day0) and does not need an entry-forecast
gate row. Operator dashboards / healthchecks that assume "every live
market has an entry_readiness row" need to scope to OPENING_HUNT mode
only.

### Auditing readiness rows

`readiness_state.status='LIVE_ELIGIBLE'` for `strategy_key='entry_forecast'`
rows is **necessary but not sufficient** for live submission.
`read_executable_forecast` further validates producer-readiness
alignment (`source_run_id`, `expires_at`) downstream of the readiness
row. Operators inspecting the `readiness_state` table directly should
treat `LIVE_ELIGIBLE` rows as "passed the gate combinator at write
time" — actual live submission requires the read-side validation to
also pass.

### Performance note

When both `_ROLLOUT_GATE=1` and `_READINESS_WRITER=1`, the daemon reads
`entry_forecast_promotion_evidence.json` 2× per candidate per cycle.
The Phase C-perf-cache commit (`734012fa` on `main`) wraps
`read_promotion_evidence` with `functools.lru_cache(maxsize=4)` keyed
by `(path, mtime_ns, size, st_ino, st_ctime_ns)`, so re-reads
collapse to a stat() call plus a cached parse. The inode and
metadata-change time fields ensure atomic overwrites via `os.replace`
(which rotates `st_ino`) invalidate the cache even when `mtime` and
file size do not change. INV-C tests in
`tests/test_activation_flag_combinations.py` pin the rotation
visibility contract.
