# EDLI v1 Daemon Reboot Runbook

Status: implementation branch runbook. This file does not authorize live restart.

## Config State

`config/settings.json` contains `edli_v1.enabled=true`,
`reactor_mode=live_no_submit`, event writer/forecast/Day0/market-channel/
no-trade-regret/reports enabled,
`day0_authority_catchup_scanner_enabled=false`, and
`stale_book_directional_trading_enabled=false`, `real_order_submit_enabled=false`,
and `taker_fok_fak_live_enabled=false`. In this PR, EDLI ignores any attempted
real-order config flip and always stops at an event-bound no-submit receipt.

## Restart Command

Current repo runbooks name the persistent live daemon launchd label:

```bash
launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist
```

REVIEW_REQUIRED: do not execute in Codex. Operator must verify the active plist,
environment, DB backups, and launch window first.

## Pre-Reboot Checks

1. Back up `state/zeus-world.db`, `state/zeus-forecasts.db`, and `state/zeus_trades.db`.
2. Run schema initialization/migration on a DB copy.
3. Run focused EDLI tests and money-path tests.
4. Confirm `python scripts/check_schema_version.py` passes.
5. Confirm no production module named `shadow_*` was added for EDLI.

## Post-Reboot Verification

1. EDLI event writer job exists in scheduler.
2. Existing scheduler jobs remain present.
3. Market-channel service is data/quote/evidence only.
   - Scheduler health should report `edli_market_channel_ingestor.thread` as
     `started` or `alive` when active weather tokens exist.
   - REST seed uses `PolymarketClient.get_orderbook_snapshot()`.
   - Websocket endpoint is the public market channel
     `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
4. Forecast COMPLETE events become eligible only through source/FDR/Kelly/RiskGuard/final-intent gates.
5. Day0 hard facts require source/station/date/DST/metric/rounding gates.
   - The trade-DB `settlement_day_observation_authority` scanner is disabled by
     default as catch-up/evidence only; it is not the online live authority.
6. No public market-channel fill truth writes.
7. No direct venue adapter imports from `src/events/reactor.py`.
8. `python3 scripts/replay_correctness_gate.py` must be run in an environment
   with the canonical trade DB present; the isolated implementation worktree
   did not contain `state/zeus_trades.db`.
   - Verified workaround before restart:
     `python3 scripts/replay_correctness_gate.py --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db`
     after a local baseline bootstrap matched projection hash
     `5c1a1cb0075c157109941f7ff748acc3617b4b116d6bd9d56968fd5c121127e8`.
