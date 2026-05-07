# Wave8 Object Meaning Invariance Plan

## Boundary

Scope: pricing semantics authority cutover F-09 fill authority split.

Selected boundary:
`OrderResult / venue fill observation -> Position entry economics -> settlement/report/learning consumers`.

Invariant restored by this wave:
An entry price or fill price may become settlement/report/learning-grade entry economics only when the value carries confirmed fill authority. Submitted limits, optimistic match observations, and non-final fill-like payloads remain explicit non-fill authorities and must fail closed for settlement P&L.

Non-goals:
- No live venue submission, cancel, redeem, or production DB mutation.
- No schema migration or legacy data rewrite.
- No lifecycle phase grammar change.
- No promotion of optimistic or replay rows into learning authority.

## Findings

W8-F1: `materialize_position()` computed `entry_price` from `result.fill_price` before checking command finality. A non-final fill-like value could persist as `Position.entry_price` while the row claimed `entry_economics_authority=submitted_limit`.

W8-F2: `_apply_entry_fill_economics()` stamped optimistic observations as `entry_economics_authority=avg_fill_price`. Because harvester only fail-closed on the explicit non-fill authority set, optimistic match economics could reach settlement P&L fallback when `has_fill_economics_authority` was false.

W8-F3: `log_execution_report()` persisted `result.fill_price` and derived `fill_quality` without checking fill finality. A non-final `OrderResult` could be rejected by `Position` materialization while still entering `execution_fact` as fill-like execution telemetry.

W8-F4: The order polling path treated order status `CONFIRMED` as full fill authority even when the same venue payload carried explicit trade status `MATCHED`/`MINED`/other non-final state. That collapsed order lifecycle evidence into trade-finality evidence.

W8-F5: Non-final execution telemetry wrote `NULL` fill fields for new `execution_fact` rows, but the upsert helper preserved existing `fill_price`, `shares`, `fill_quality`, `filled_at`, and `latency_seconds` when the caller passed `None`. A previously contaminated row could therefore survive the corrected report path.

W8-F6: `_fill_statuses(deps)` removed known stale optimistic statuses from dependency-provided fill statuses, but still allowed dependencies to add other venue states such as `MINED` to the fill-success set. That left a compatibility path where non-final trade lifecycle evidence could become full fill authority.

W8-F7: `log_execution_report()` cleared stale fill fields for non-final `filled`/`confirmed` reports, but not for `pending` reports that still carried fill-like telemetry. A stale `execution_fact` row could keep old fill economics under `terminal_exec_status='pending'`, contaminating report summaries such as average fill quality.

W8-F8: Exit lifecycle telemetry bypassed `log_execution_report()` and wrote directly through `log_execution_fact()` without a clear-fill mode. A stale exit `execution_fact` row could preserve prior `filled_at`, `fill_price`, `shares`, `fill_quality`, and `latency_seconds` after a new non-final exit attempt/retry/status event reused the same `trade_id:exit` intent.

W8-F9: Clear-fill mode nulled stale fill economics but still allowed `log_execution_fact()` to preserve old `terminal_exec_status='filled'` and `venue_status='CONFIRMED'` when the current non-final report supplied missing/empty status. That left report summaries counting a non-final row as filled even after fill fields were removed.

## Repair Plan

1. In `src/engine/cycle_runtime.py`, determine fill finality before deriving `entry_price` and fallback shares.
2. In `src/state/portfolio.py`, introduce an explicit `optimistic_match_price` entry economics authority that is not fill-grade.
3. In `src/execution/fill_tracker.py`, stamp optimistic exposure with the explicit optimistic authority while leaving confirmed and partial confirmed authorities unchanged.
4. In `src/execution/harvester.py`, add optimistic authority to the settlement fail-closed non-fill set.
5. In `src/state/db.py`, gate execution telemetry fill fields on confirmed fill finality before writing `execution_fact`.
6. In `src/execution/fill_tracker.py`, require explicit trade status to be absent or `CONFIRMED` before order `CONFIRMED` may promote full fill authority; otherwise preserve non-final trade status as optimistic evidence or fail closed.
7. In `src/state/db.py`, add an explicit fill-field clear mode for non-final execution reports so corrected semantics remove stale fill telemetry instead of preserving it.
8. Make the full-fill status set non-extensible from runtime deps: `CONFIRMED` is the only success terminal; `MATCHED`/`MINED`/`FILLED` are optimistic observations even when stale deps list them as fill statuses.
9. Classify any non-final execution report carrying fill-like price as `pending_fill_authority` and clear fill fields for all non-final report writes so stale fill telemetry cannot survive under `pending` or other non-final statuses.
10. Pass clear-fill mode from non-final exit lifecycle execution-fact writes so exit attempt/retry/status telemetry cannot preserve stale terminal fill economics.
11. Make clear-fill mode also clear stale fill terminal/venue authority: missing current statuses become explicit `pending_fill_authority` instead of preserving old filled/confirmed status.
12. Add relationship tests proving non-final `OrderResult.fill_price` cannot materialize into `Position.entry_price`, optimistic match authority cannot settle through fallback P&L, non-final trade status cannot become confirmed fill authority, stale deps cannot add `MINED` to fill success, and non-final execution/exit telemetry cannot persist or preserve fill facts or fill-status authority.

## Verification

Required focused checks:
- `python3 -m py_compile src/engine/cycle_runtime.py src/execution/fill_tracker.py src/state/portfolio.py src/execution/harvester.py tests/test_runtime_guards.py tests/test_live_safety_invariants.py tests/test_harvester_metric_identity.py`
- Focused pytest for materialization, optimistic fill tracking, and harvester metric identity.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave8/PLAN.md`

Completed checks:
- `python3 -m py_compile src/engine/cycle_runtime.py src/execution/fill_tracker.py src/execution/harvester.py src/state/db.py src/state/portfolio.py tests/test_runtime_guards.py tests/test_live_safety_invariants.py tests/test_harvester_metric_identity.py tests/test_db.py`
- `tests/test_runtime_guards.py`: 230 passed, 2 skipped.
- `tests/test_live_safety_invariants.py`: 95 passed, 3 skipped.
- `tests/test_db.py`: 39 passed, 19 skipped.
- `tests/test_harvester_metric_identity.py`: 41 passed.
- Topology/plumbing checks: planning-lock, task-boot-profiles, schema, map-maintenance advisory, and `git diff --check`.

Critic verdict:
- Initial re-review found S0/S1 gaps in trade-status conflict promotion and stale execution-fact fill fields.
- Follow-up reviews found stale non-final pending telemetry, exit direct-write bypass, and missing-status terminal/venue preservation.
- Final critic verdict: APPROVE. No remaining S0/S1 findings for the reviewed boundary.

## Topology Notes

The broad Wave8 selection route was advisory only for high-fanout execution files. The usable edit route was the profile-specific `pricing semantics authority cutover` phrase. `r3 fill finality ledger implementation` admitted fill-tracker tests but not `src/engine/cycle_runtime.py`, even though materialization is the boundary where non-final `OrderResult.fill_price` crosses into `Position`.

The existing Wave8 plan route remains noisy: the `operation planning packet` profile admits `docs/operations/task_*/PLAN.md`, but navigation still emits `operations_task_unregistered` until `docs/operations/AGENTS.md` is maintained. This did not block planning-lock, but it is real topology friction for ad hoc object-invariance waves.

The semantic boot surface still has class mismatch friction for this work: `semantic-bootstrap --task-class pricing` is not a valid class even though the usable navigation route is `pricing semantics authority cutover`. Critic-repair source routing also emitted merge-conflict-first metadata for a non-merge edit slice. Neither issue blocked the safe patch, but both are true compatibility problems for object-invariance waves because they force agents to translate between unrelated topology vocabularies while preserving live-money semantics.
