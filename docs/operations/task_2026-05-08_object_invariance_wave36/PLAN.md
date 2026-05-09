# Object Invariance Wave 36 — Pending Entry Economics Authority

Status: APPROVED
Date: 2026-05-08
Scope: D3 pending-position economics residual.

## Invariant

A submitted order target is not a venue-confirmed economic position until Zeus
has fill-grade venue evidence. Pending entry rows may preserve target and
submitted economics for reconciliation, but downstream cost, PnL, settlement,
replay, report, and learning consumers must not be able to read submitted/model
price as fill-derived cost basis.

This wave restores:

- object identity: submitted order target vs filled position lot
- authority: submitted limit/model edge vs venue-confirmed fill
- lifecycle: pending entry vs active filled/open position
- persistence: projection fields vs fill-authoritative economics
- eligibility: pending rows are not settlement/report/learning cost authority

## Selected Boundary

Boundary: order intent / submitted order -> position projection before fill
authority.

Material values crossing the boundary:

| Value | Submitted object | Fill-authoritative object |
|---|---|---|
| `entry_price_submitted` | limit price submitted to venue | not fill authority |
| `submitted_notional_usd` | submitted order notional | not cost basis |
| `target_notional_usd` | decision sizing target | not cost basis |
| `entry_price` | legacy/current open projection field | must not carry submitted/model price when no fill evidence |
| `cost_basis_usd` | legacy/current open projection cost field | must not carry target notional when no fill evidence |
| `entry_price_avg_fill` | zero until fill evidence | venue average fill price |
| `filled_cost_basis_usd` | zero until fill evidence | fill shares times fill price |
| `fill_authority` | `none` | `venue_confirmed_*` |

## Repair Plan

1. Add relationship tests proving pending materialization preserves
   submitted/target fields but leaves fill-grade open economics zero.
2. Change `cycle_runtime.materialize_position()` so non-final/non-filled entry
   rows do not populate `entry_price` or `cost_basis_usd` from submitted/model
   economics.
3. Keep full-fill rows unchanged: `entry_price`, `entry_price_avg_fill`,
   `shares_filled`, and `filled_cost_basis_usd` come from command-final fill
   evidence.
4. Sweep downstream monitor, exit, settlement, replay/report, learning, and
   legacy/fallback consumers for paths that could still materialize the old
   meaning.

## Data-Layer Stop Rule

If a finding requires historical refetch, DB rebuild, settlement relabel,
calibration promotion, migration, or source-authority re-certification, it must
stay in `docs/to-do-list/known_gaps.md` with enough detail to drive a future
offline/operator-approved packet. This wave does not mutate canonical live DBs
or promote reconstructed data.

## Verification

Focused gates:

- `tests/test_runtime_guards.py::test_materialize_position_splits_submitted_target_from_fill_authority`
- `tests/test_runtime_guards.py::test_materialize_position_rejects_reported_fill_price_without_command_finality`
- `tests/test_runtime_guards.py::test_materialize_position_accepts_fill_price_only_with_command_finality`
- focused DB/settlement tests if the downstream sweep changes persistence

Results:

- Relationship red phase before implementation:
  - `tests/test_runtime_guards.py::test_materialize_position_splits_submitted_target_from_fill_authority`
  - `tests/test_runtime_guards.py::test_materialize_position_rejects_reported_fill_price_without_command_finality`
  - `tests/test_runtime_guards.py::test_pending_submitted_only_position_does_not_gain_open_economics_in_portfolio`
  - `tests/test_db.py::test_position_current_pending_entry_without_fill_authority_is_not_open_exposure`
  - result: `4 failed`, proving the pre-patch boundary drift.
- Focused relationship gates after implementation:
  - the 4 tests above plus final-fill, partial-fill, settlement, DB fill-view,
    non-final execution, and execution-report authority tests
  - result: `12 passed`.
- File-level focused gates:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py tests/test_db.py -q --tb=short`
  - result: `291 passed, 19 skipped`.
- Downstream authority sweep:
  - `tests/test_live_safety_invariants.py tests/test_harvester_metric_identity.py`
  - result: `159 passed`.
- Expanded post-sweep gate after `fill_tracker` continuity patch:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py tests/test_db.py tests/test_live_safety_invariants.py tests/test_harvester_metric_identity.py -q --tb=short`
  - result: `450 passed, 19 skipped`.
- Static checks:
  - `py_compile` on touched source/test files: pass.
  - `git diff --check` on touched files: pass.
  - `topology_doctor.py --planning-lock ... --plan-evidence ...`: pass.

Noise recorded:

- Wider `tests/test_pnl_flow_and_audit.py` currently fails on unrelated
  fixture/environment gates (`position_events.env is required`,
  `UNSUPPORTED_CALIBRATION_SOURCE_ID`, missing `source_health.json`, harvester
  pair count 0). Those failures are not evidence against this pending-entry
  economics repair and were not used as a completion gate.

Critic verdict:

- `APPROVE`: producer, portfolio, persistence/read-model, fill-tracker
  continuity, downstream settlement/monitor/exit, tests, and `known_gaps`
  handling reviewed with no blocking invariant break.
