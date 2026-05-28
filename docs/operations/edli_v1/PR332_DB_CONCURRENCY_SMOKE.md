# PR332 DB Concurrency Smoke

Status: PASS for forecast no-submit scope.

PR head: `127ca1d49ca67c5bfe947ff2dfa7872a58d81024`

## Command

```bash
python -m pytest -q tests/events/test_reactor.py::test_pr332_db_concurrency_smoke_reactor_world_writes -q
```

Result: PASS.

## Fixture

The smoke creates a temp world DB with current schema, seeds:

- 6 pending `FORECAST_SNAPSHOT_READY` opportunity events
- 1 market-channel book event, expected to reject into no-trade regret
- 1 concurrent future forecast event inserted from a separate SQLite connection while the reactor processes the current batch

The reactor runs with `decision_time=2026-05-24T18:10:00+00:00` and `limit=10`.

## Counts

- input current events: 7
- processed count: 7
- verified no-submit certificate count: 6
- receipt projection count: 6
- compile failure count: 1
- regret count: 1
- dead-letter count: 0
- DB lock count: 0
- future concurrent event status: pending

## Terminal-State Query

The smoke asserts every processed current event has one durable terminal surface:

- forecast events: verified `NoSubmitDecisionCertificate` plus `edli_no_submit_receipts` projection
- book event: `decision_compile_failures` plus `no_trade_regret_events`

The companion terminal completeness proof is:

```bash
python -m pytest -q tests/events/test_reactor.py::test_processed_event_has_verified_certificate_or_failure_or_regret_or_dead_letter -q
```

Result: PASS.

## PASS / FAIL

PASS for PR332 scoped DB concurrency smoke.
