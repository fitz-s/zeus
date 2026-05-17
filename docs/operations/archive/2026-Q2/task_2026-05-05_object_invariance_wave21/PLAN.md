# Object Meaning Invariance Wave 21

## Boundary

Selected boundary: venue read evidence -> M5 exchange reconciliation absence finding -> local command recovery / exit unblock authority.

This boundary can affect whether Zeus treats a local open/unknown/cancel-unknown order as absent from the venue, emits a heartbeat/cutover/local-orphan finding, and later allows operator or recovery workflows to unblock or resolve local command state.

## Candidate Boundary Selection

| Candidate | Live-money risk | Values crossing | Downstream consumers | Bypass / stale path | Repair scope |
|---|---|---|---|---|---|
| venue read freshness -> absence findings | S1/S0-adjacent recovery risk | open-order list, trade list, position list, freshness metadata, observed_at | exchange_reconcile findings, operator resolution, exit/cancel recovery | missing `read_freshness` currently means "accept" instead of "unknown" | Safely scoped to M5-admitted exchange_reconcile/test_exchange_reconcile |
| exchange trade fact -> command fill state | Direct lifecycle/economics risk | venue trade id, state, filled size, fill price | venue_trade_facts, command events, fill/replay/report | existing tests cover missing/nonfinite economics and nonconfirmed finality | Defer unless critic finds bypass |
| local cancel-unknown -> M5 unblock policy | Direct duplicate exit risk | CANCEL_REPLACE_BLOCKED, finding resolution, exchange absence | exit_safety replacement gate, operator resolution | M5 profile blocks `tests/test_exit_safety.py`; route mismatch for cross-boundary test | Defer / topology compatibility note |

## Topology Compatibility Notes

- `r3 exchange reconciliation sweep implementation` admitted `exchange_reconcile.py`, `venue_command_repo.py`, `exit_safety.py`, `exit_lifecycle.py`, and `test_exchange_reconcile.py`.
- Adding `tests/test_exit_safety.py` to the M5 route was rejected even though cancel-unknown unblock is a downstream M5 consumer. This is a real cross-boundary test-route gap; Wave21 will keep the patch inside admitted M5 files.
- `semantic-bootstrap --task-class exchange_reconciliation` failed with `semantic_bootstrap_unknown_task_class`; M5 exists as a navigation profile but not as a semantic boot class.

## Material Value Lineage

| Value | Real object denoted | Origin | Authority / evidence class | Unit / side / time | Transform | Persistence / consumers | Status |
|---|---|---|---|---|---|---|---|
| `adapter.get_open_orders()` result | Venue's currently open order set | `run_reconcile_sweep()` adapter | venue read evidence | open-order ids at observed/read time | set difference with local command order ids | findings, local-orphan/heartbeat/cutover classification | Broken without freshness authority |
| `adapter.get_trades()` result | Venue trade facts | optional adapter method | venue read evidence | trade state, size, price, venue timestamp | linkable facts append or finding | `venue_trade_facts`, command events, findings | Must require fresh/successful read when used |
| `adapter.get_positions()` result | Venue token exposure | optional adapter method | venue read evidence | CTF token size at observed/read time | compare with journal positions | position-drift findings | Must require fresh/successful read when used |
| `adapter.read_freshness` | Authority that read output is current enough to prove absence | fake/live adapter metadata | freshness/evidence authority | per-surface `fresh`/`ok` and captured time | `_assert_adapter_read_fresh()` gate | gates absence findings | Broken if absent accepted |
| `exchange_reconcile_findings` row | Review/recovery finding, not command truth | `record_finding()` | canonical finding persistence | context periodic/ws_gap/heartbeat/cutover/operator | idempotent unresolved finding | operator/recovery/report surfaces | Must not be based on stale/unknown absence |

## Findings

- W21-F1 (S1): `_assert_adapter_read_fresh()` returns success when `adapter.read_freshness` is absent. A successful method call that returns `[]` can therefore be promoted to venue absence proof with no freshness/evidence object.
- W21-F2 (S1): a freshness mapping with only `{"ok": True}` and no `fresh=True` also passes. Transport success can therefore be treated as semantic freshness, collapsing source availability with current absence proof.

## Repair Plan

- Require `read_freshness` mapping for every absence-relevant venue surface that M5 consumes.
- Require mapping entries to provide explicit freshness, not only transport success.
- Preserve `True` as the compact explicit-fresh sentinel used by current tests.
- Add focused tests proving missing freshness metadata and `ok=True` without `fresh=True` fail closed without findings or local command mutation.

## Repair Implemented

- `src/execution/exchange_reconcile.py`
  - `_assert_adapter_read_fresh()` now rejects missing `read_freshness` metadata instead of accepting it as fresh.
  - Freshness dictionaries now require `fresh=True`; `ok=True` alone is only transport success and cannot prove absence.
- `tests/test_exchange_reconcile.py`
  - Added `FakeAdapterWithoutFreshness`.
  - Added missing-freshness and transport-ok-without-freshness fail-closed tests.
  - Updated the real-adapter missing-read-surface test to fail closed on missing freshness before absence can be inferred.

## Verification Results

- `python3 -m py_compile src/execution/exchange_reconcile.py tests/test_exchange_reconcile.py`: passed.
- `pytest -q -p no:cacheprovider tests/test_exchange_reconcile.py -k 'freshness or absence_proof or missing_read_surface'`: 5 passed, 23 deselected.
- Full `tests/test_exchange_reconcile.py`: 28 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_m5_exchange_reconcile_routes_to_m5_profile_not_heartbeat`: 1 passed.
- `pytest -q -p no:cacheprovider tests/test_venue_command_repo.py tests/test_user_channel_ingest.py`: 89 passed, 40 warnings.
- Full `tests/test_exit_safety.py`: 43 passed.
- Blocked/environment verification:
  - `pytest -q -p no:cacheprovider tests/test_venue_command_repo.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py` reached 108 passed / 8 skipped but failed two collection/runtime imports: missing `apscheduler` in `src/main.py` and missing `sklearn` in `src/calibration/platt.py`.
  - `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M5` failed because the routed script path does not exist.
  - `python3 scripts/r3_drift_check.py --phase M5` also failed because the wrapper expects the same missing routed path.

## Downstream Contamination Sweep

- Local-orphan / heartbeat / cutover findings: no finding is emitted unless the relevant venue read surface has explicit fresh evidence.
- Linkable trade facts: still require explicit trade id, filled size, and fill price before command fill events advance.
- Position drift: positions surface now also requires explicit freshness before exchange-vs-journal size differences can become findings.
- Legacy/fallback: adapters without `read_freshness` fail closed instead of treating an empty response as absence authority.

## Critic Loop

- Initial critic verdict: APPROVE.

## Verification Plan

- `python3 -m py_compile src/execution/exchange_reconcile.py tests/test_exchange_reconcile.py`
- focused M5 freshness tests.
- full `tests/test_exchange_reconcile.py`
- M5 digest profile test and topology/planning-lock/map checks.
- Critic review before advancing.
