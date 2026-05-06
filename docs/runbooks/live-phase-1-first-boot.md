# Zeus Live Phase 1: First Boot Runbook

Authority: `docs/authority/zeus_current_delivery.md`
Applies to: First live daemon boot after pre-live validation.

---

## Pre-flight checklist

Before starting, verify all of the following:

- [ ] Daemon uses the code-authoritative live runtime state; `ZEUS_MODE` is not a selector
- [ ] macOS Keychain entries present: `openclaw-metamask-private-key`, `openclaw-polymarket-funder-address`, `zeus_discord_webhook`
- [ ] `state/` directory exists and is writable
- [ ] Shadow/backtest validation produced no blocking safety issues
- [ ] Blocking live-safety gates pass: `tests/test_live_safety_invariants.py`, `tests/test_runtime_guards.py`, `tests/test_config.py`, and `scripts/check_advisory_gates.py`

---

## Step 1: Verify wallet balance

```bash
python - <<'EOF'
from src.data.polymarket_client import PolymarketClient
c = PolymarketClient()
bal = c.get_balance()
print(f"Wallet: ${bal:.2f} USDC")
EOF
```

**Expected**: balance prints without error. If you see `FATAL: Cannot start`, credentials are missing or the Polymarket API is unreachable.

---

## Step 2: Verify wallet bankroll and sizing policy

```bash
python - <<'EOF'
from src.runtime import bankroll_provider
from src.config import settings
record = bankroll_provider.current()
print(f"wallet_bankroll_usd: {record.value_usd if record else 'UNAVAILABLE'}")
print(f"kelly_multiplier: {settings['sizing']['kelly_multiplier']}")
print(f"loss_limit_usd:   {settings['riskguard']['loss_limit_usd']}")
EOF
```

**Sanity thresholds for Phase 1:**
- `wallet_bankroll_usd`: matches the read-only wallet balance checked in Step 1
- `kelly_multiplier`: 0.10-0.25 (Phase 1 is fractional Kelly, not full)
- `loss_limit_usd`: within the operator-approved Phase 1 loss budget

If the wallet value is unavailable or stale, do not start the daemon. If sizing or risk policy is wrong, edit `config/settings.json` and re-check.

---

## Step 3: Manual single-cycle trigger

```bash
python -m src.main --once 2>&1 | tee /tmp/zeus_first_boot.log
```

This runs ONE full cycle and exits. Do NOT use the daemon loop for first boot.

---

## Step 4: Verify each subsystem

After the `--once` run, check the log for these entries:

```
# Wallet gate passed
Startup wallet check: $NN.NN USDC available

# Schema initialized
INFO: Zeus starting in live mode (single cycle)

# Discovery completed (may find 0 opportunities — that is OK)
INFO: [...] cycle complete

# Harvester ran
INFO: harvester cycle

# No unhandled exceptions
# (no CRITICAL or FATAL lines)
```

If any subsystem logged `CRITICAL` or `FATAL`, stop and investigate before proceeding.

---

## Step 5: Confirm no orders placed

After `--once`, verify:

```bash
# Check trade DB for any new trade_decisions rows
python - <<'EOF'
from src.state.db import get_trade_connection
conn = get_trade_connection()
rows = conn.execute("SELECT COUNT(*) FROM trade_decisions").fetchone()[0]
print(f"trade_decisions rows: {rows}")
conn.close()
EOF
```

For a first boot with no open markets matching current criteria, expect 0 rows. If rows exist, review them with:

```bash
python -m src.analysis dashboard
# or
python - <<'EOF'
from src.state.db import get_trade_connection
conn = get_trade_connection()
rows = conn.execute("SELECT * FROM trade_decisions ORDER BY created_at DESC LIMIT 10").fetchall()
for row in rows:
    print(dict(row))
conn.close()
EOF
```

**If any order was placed unexpectedly**: use the kill-switch immediately (see below).

---

## Kill-switch

```bash
# Stop the daemon immediately (if running as scheduler)
pkill -f 'python -m src.main'

# Or if running via launchd:
launchctl unload ~/Library/LaunchAgents/com.openclaw.zeus-live.plist

# Confirm stopped:
ps aux | grep 'src.main'
```

After kill, Zeus holds all open positions as-is. No orders are sent while the daemon is stopped.

---

## Starting the live daemon loop

Once single-cycle verified:

```bash
nohup python -m src.main >> logs/zeus-live.log 2>&1 &
echo $! > state/zeus-live.pid
```

Or via launchd (preferred for persistent operation).

---

## What to expect on Phase 1 boot

- `insufficient_history` warnings in logs: normal. Markets with <N prior samples don't meet the FDR threshold yet.
- No per-trade cap log should appear; per-trade hard caps were removed. Entry throttling should surface through RiskGuard, posture, executable-price, or max-exposure reasons.
- Discord alert `RISKGUARD HALT`: if triggered on first cycle, check the failed_rules detail in the alert.
- 0 trades placed is a valid outcome if no edges exceed the FDR/Kelly floor.
