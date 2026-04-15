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

The daemon writes `state/daemon-heartbeat-live.json` every 60 seconds.

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
