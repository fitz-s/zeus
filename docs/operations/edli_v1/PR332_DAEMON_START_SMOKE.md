# PR332 Daemon Start Smoke

Status: PASS for sandboxed forecast no-submit start-smoke equivalent.

PR head: `127ca1d49ca67c5bfe947ff2dfa7872a58d81024`

## Command

```bash
python -m pytest -q tests/money_path/test_edli_online_invariants.py::test_pr332_scoped_daemon_restart_smoke_registers_forecast_no_submit_only -q
```

Result: PASS.

## Safety Boundary

This smoke did not unload or reload the production launchd service. It ran the daemon entrypoint wiring under pytest with in-memory DB handles and monkeypatched external startup side effects. That is the safe equivalent for this Codex run; production `launchctl` reload remains an operator action.

Operator production command, when approved:

```bash
launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist
launchctl list com.zeus.live-trading
```

## Observed Config Flags

- `edli_v1.enabled=true`
- `reactor_mode=live_no_submit`
- `forecast_snapshot_trigger_enabled=true`
- `day0_extreme_trigger_enabled=false`
- `day0_hard_fact_live_enabled=false`
- `market_channel_ingestor_enabled=false`
- `real_order_submit_enabled=false`
- `taker_fok_fak_live_enabled=false`

## Scheduler Assertions

Observed by `FakeScheduler` inside the smoke:

- daemon entrypoint reached scheduler start
- scheduler shutdown path executed after controlled `KeyboardInterrupt`
- `edli_event_reactor` job registered
- `heartbeat` job retained
- `harvester` job retained
- `edli_market_channel_ingestor` job absent

## Negative Assertions

- market-channel thread absent: PASS
- Day0 trigger disabled: PASS
- real submit disabled: PASS
- taker FOK/FAK live disabled: PASS
- no venue submit path invoked: PASS
- no broad `run_cycle()` inside `_edli_event_reactor_cycle`: PASS, covered by `tests/money_path/test_edli_online_invariants.py`
- no database locked during startup smoke: PASS

## PASS / FAIL

PASS for PR332 scoped forecast no-submit daemon/start smoke equivalent.
