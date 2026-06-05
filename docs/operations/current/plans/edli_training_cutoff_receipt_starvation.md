# EDLI Training Cutoff Receipt Starvation Hotfix

Date: 2026-06-05

## Goal

Restore post-migration shadow-live EDLI no-submit receipt liveness without relaxing
trade-score, FDR, Kelly, RiskGuard, or submit gates.

## Problem

After the `~/zeus` cutover, live reactor cycles reached the no-submit proof path
but repeatedly rejected otherwise candidate-shaped events with
`NO_SUBMIT_CERTIFICATE_REJECTED` details:

- `calibration.training_cutoff after decision_time`
- `max_parent_source_available_at after decision_time`

Inspection showed legacy/read-time `platt_models` rows have no
`training_cutoff` column, so the adapter used `fitted_at` as
`training_cutoff`. Read-time Platt cache writes materialize during the reactor
cycle, making `fitted_at` later than the cycle `decision_time` and causing
immediate retry loops that consumed the proof window before fresh candidates.

After adding and backfilling `training_cutoff`, shadow live restarted on
`4bfc857b8b3c12d6723fefcd3350fa58992be53c`, but post-restart receipts still did
not advance. A SIGUSR1 live stack dump at 2026-06-05 11:41Z showed the active
EDLI reactor thread blocked in:

`event_reactor_adapter._calibration_authority_payload_and_clock ->
CalibrationManager.get_calibrator -> CalibrationManager._fit_from_pairs ->
Platt.fit`.

That means the receipt authority seam still permits runtime calibration
training in the scheduler. This is a liveness bug: a no-submit receipt should
compile against already-persisted calibration authority or fail closed when that
authority is missing. It must not train Platt models inside the live reactor
proof window.

After replacing runtime Platt fitting with persisted-model reads, shadow live
restarted on `8b6f23d194a29bb824164af4a23a6ab44fc054b7`, and the next SIGUSR1
stack showed the reactor moved forward but then pinned in:

`main._edli_event_reactor_cycle ->
EventStore.archive_superseded_channel_events`.

The existing channel sweep was batch-bounded for candidate rows, but its keeper
CTE still performed a large expression join / grouping over active channel
backlog. The sweep must remain semantics-preserving while making keeper lookup
per-key and index-backed so receipt emission is not starved before
`process_pending`.

## Change

- Add `platt_models.training_cutoff` to canonical schema and schema fingerprint.
- In `event_reactor_adapter`, treat `training_cutoff` as training-data cutoff.
  For legacy rows without the column, derive a UTC-midnight date-level cutoff
  from `fitted_at`/`recorded_at` and preserve materialization as
  `model_materialized_at`.
- Keep explicit future `training_cutoff` fail-closed.
- In `event_reactor_adapter`, replace the receipt authority seam's
  `get_calibrator()` call with a persisted-model lookup that cannot invoke
  `_fit_from_pairs`. Missing persisted authority remains fail-closed.
- Add regression coverage proving the no-submit receipt path does not call
  `get_calibrator()` and therefore cannot perform runtime Platt fitting.
- In `EventStore.archive_superseded_channel_events`, replace the keeper CTE with
  per-key indexed probes on `idx_opportunity_events_channel_token`, preserving
  all rows tied at max `available_at` while avoiding a large active-backlog
  GROUP BY inside the reactor worker.
- Add channel-sweep regression coverage for tied max timestamps and the actual
  emitted keeper probes' query plans.

## Verification

- `pytest tests/events/test_fetch_pending_timeliness.py tests/events/test_archive_channel_events_superseded.py tests/money_path/test_edli_market_substrate_warm_cycle.py tests/engine/test_event_reactor_no_bypass.py::test_runtime_receipt_uses_event_bound_final_intent_contract tests/engine/test_event_reactor_no_bypass.py::test_legacy_calibration_materialization_time_is_not_training_cutoff tests/engine/test_event_reactor_no_bypass.py::test_certificate_rejects_explicit_calibration_training_cutoff_after_decision`
- `python scripts/check_schema_fingerprint.py`
- `git diff --check`
- Restart shadow live and require a fresh post-restart `edli_no_submit_receipts`
  row before considering the cutover healthy.
