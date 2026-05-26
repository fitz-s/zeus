# PR332 live-cron execution mode gate

## Objective

Make PR332 inert by default on the existing live cron daemon, and require an
explicit mutually exclusive live execution mode before EDLI event-driven workers
can be scheduled.

## Scope

- Add `live_execution_mode` with explicit allowed values `legacy_cron`,
  `edli_shadow_no_submit`, `edli_submit_disabled_bridge`,
  `edli_live_canary`, `edli_live`, and `disabled`.
- Default EDLI runtime scheduler surfaces off in `config/settings.json`.
- Enforce scheduler mutual exclusion:
  - `legacy_cron` schedules legacy run-cycle cron jobs and never schedules EDLI
    workers.
  - EDLI event-driven stages schedule only explicitly allowed EDLI workers and
    skip legacy run-cycle/market-discovery cron jobs.
  - `disabled` schedules neither legacy run-cycle jobs nor EDLI workers.
- Fail boot when EDLI runtime flags conflict with `legacy_cron` mode or when
  an EDLI event-driven stage is requested without its required runtime
  authorities.
- Add focused daemon-smoke tests for inert default, legacy-vs-EDLI exclusion,
  and conflict fail-closed behavior.

## Non-goals

- Do not enable live submit, live canary, market-channel websocket, Day0 hard
  facts, or user-channel/reconcile runtime.
- Do not implement full-live user-channel/reconcile lifecycle or Day0 DAG in
  this gate patch.

## Verification

- `python3 -m pytest -q tests/money_path/test_edli_online_invariants.py`
- `python3 -m pytest -q tests/money_path --maxfail=5`
- `python3 scripts/topology_doctor.py --planning-lock ...`
- `python3 scripts/topology_doctor.py --map-maintenance ...`
