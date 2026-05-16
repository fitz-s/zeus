# Object Invariance Wave 27 - Trade Fact to Position Lot Exposure Authority

Status: PLANNING-LOCK EVIDENCE FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT DB MUTATION AUTHORITY

Created: 2026-05-08
Last reused or audited: 2026-05-08
Authority basis: root AGENTS.md object-meaning invariance goal; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md remaining-mainline ledger; src/state/AGENTS.md; src/ingest/AGENTS.md; src/execution/AGENTS.md; src/risk_allocator/AGENTS.md

## Scope

Repair one bounded boundary class:

`venue trade fact -> position_lots exposure object`

This wave does not mutate live/canonical databases, run migrations, backfill or relabel legacy rows, publish reports, harvest settlement, redeem, use venue credentials, or authorize live unlock. It is source/test enforcement only.

## Phase 0 - Repo-Reconstructed Map

Money path for this wave:

`submitted order -> venue_trade_facts -> position_lots -> risk allocator / monitor gap guard / reports / replay / learning`

Authority surfaces:

- Venue trade facts: `src/state/db.py` schema and `src/state/venue_command_repo.py::append_trade_fact`.
- Exposure lots: `src/state/venue_command_repo.py::append_position_lot`, consumed as canonical exposure by `src/risk_allocator/governor.py::load_position_lots`.
- Producers: `src/ingest/polymarket_user_channel.py` and `src/execution/fill_tracker.py` append trade facts and then lots.
- Downstream consumers: risk allocation, websocket gap clearance, reconciliation/report/replay/learning readers that treat latest `position_lots` rows as exposure truth.

Truth hierarchy:

`venue/CLOB/chain observation -> canonical DB append API -> derived read models/reports/tests`

Derived JSON, test fixtures, reports, replay artifacts, and backlog text are not authority and must not promote malformed exposure rows into canonical truth.

## Phase 1 - Boundary Selection

Candidates after Waves 24-26:

| Boundary | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Full `venue_trade_facts` downstream sweep | Exposure, risk, monitor gap clearance, reports, replay, learning | `state`, `filled_size`, `fill_price`, `source_trade_fact_id`, `position_lot.state` | Direct/legacy lots can bypass corrected producer semantics | Safe if repaired at append seam; broader consumers are read-only in this route |
| Monitor/exit probability side semantics | Can trigger wrong exits | position side, native probability, posterior, exit threshold | Requires exit route and side-specific tests | High value, but separate execution wave |
| Historical DB contamination audit | Could reveal polluted live/prod rows | physical DB rows | Requires dry-run and rollback/relabel plan | Operator decision required |

Selected: `venue_trade_facts -> position_lots`, because `position_lots` is the shared exposure authority for risk and operational consumers. Correct producer semantics remain incomplete if a lot can be appended as exposure without a valid trade-fact authority object.

## Phase 2 - Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Downstream consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `venue_trade_facts.trade_fact_id` | Unique venue trade observation row | `append_trade_fact` | canonical DB append API | venue observation identity | local sequence / observed time | DB autoincrement | `venue_trade_facts` | `position_lots.source_trade_fact_id`, calibration/replay/reports | Preserved only if referenced explicitly |
| `venue_trade_facts.state` | Venue trade lifecycle phase | WS/REST/fill tracker producers | venue observation | lifecycle/fill-progress evidence | observed/venue time | normalized enum | `venue_trade_facts.state` | lot append, command events, learning | Broken if treated as exposure authority without state-specific lot semantics |
| `filled_size` | Shares filled for that trade fact | venue payload | venue economics evidence when positive finite | executable economics | shares at observed/venue time | decimal text | `venue_trade_facts.filled_size` | lot shares, exposure, PnL/learning | Guarded by Wave25 for fill-progress rows |
| `fill_price` | Average fill price for that trade fact | venue payload | venue economics evidence when positive finite | executable economics | probability/share price at observed/venue time | decimal text | `venue_trade_facts.fill_price` | entry economics, PnL/learning | Guarded by Wave25 for fill-progress rows |
| `position_lots.state` | Zeus exposure/lifecycle projection for a position | `append_position_lot` | canonical DB append API | exposure authority only for exposure states | latest local sequence / state change time | producer maps trade lifecycle to lot state | `position_lots.state` | risk, gap guard, reports/replay | Ambiguous if not tied to allowed trade fact state |
| `source_trade_fact_id` | Evidence link from lot to venue trade fact | producer call | canonical DB append API | provenance/authority pointer | trade observed time through lot state-change time | foreign-key-like semantic link | `position_lots.source_trade_fact_id` | audit, rollback, downstream lineage | Broken if optional for active exposure or if it references the wrong trade lifecycle |

## Phase 3 - Failure Classification

### W27-F1 - Active exposure lots can be materialized without a state-compatible trade fact

Severity: S1 for live decision/risk quality; S2/S1 for reports/replay/learning depending on consumer.

Object meaning that changes:

`position_lots.state='OPTIMISTIC_EXPOSURE'` or `CONFIRMED_EXPOSURE` denotes Zeus exposure authority, but `append_position_lot` currently accepts those states without proving the underlying venue fact denotes matching fill-progress or confirmed economics.

Boundary:

`venue_trade_facts` producer evidence -> `position_lots` canonical exposure authority.

Code path:

- `src/state/venue_command_repo.py::append_position_lot` validates lot enum/source/time/hash and then writes the lot.
- It does not require `source_trade_fact_id` for active exposure states.
- It does not verify that an optimistic lot references `MATCHED`/`MINED`, or that a confirmed lot references `CONFIRMED`.
- `src/risk_allocator/governor.py::load_position_lots` reads latest lots as canonical exposure without joining trade facts, so the append seam is the durable enforcement point for this route.

Economic impact:

A direct, legacy, diagnostic, or future producer path can materialize risk-visible exposure that no longer denotes a valid venue fill object. Downstream sizing/risk/report/replay/learning consumers may then treat a lifecycle-only or mismatched fact as executable economic exposure.

Reachability:

Active WS/REST producers usually pass the just-appended trade fact id, but the shared append API remains a bypass and existing test setup already uses active exposure lots without trade-fact authority. That is the exact class this wave repairs.

## Phase 4 - Repair Design

Invariant restored:

An active exposure lot must carry a state-compatible venue trade fact authority:

- `OPTIMISTIC_EXPOSURE` requires `source_trade_fact_id` pointing to a `MATCHED` or `MINED` trade fact with positive finite fill economics.
- `CONFIRMED_EXPOSURE` requires `source_trade_fact_id` pointing to a `CONFIRMED` trade fact with positive finite fill economics.
- Active exposure lot `shares` and `entry_price_avg` must equal the referenced trade fact `filled_size` and `fill_price`; the lot cannot carry a different economic object under the same trade-fact pointer.
- The referenced trade fact must belong to an `ENTRY` / `BUY` command, matching current `position_lots` exposure semantics.
- `FAILED` and `RETRYING` trade facts remain non-exposure facts and can still drive rollback/quarantine paths.
- Non-exposure lot states are not widened in this wave.

Durable mechanism:

- Centralize the validation in `src/state/venue_command_repo.py::append_position_lot` so all API producers, future call sites, tests, reports, and compatibility paths use the same guard before downstream consumers can see the row.
- Add non-destructive `position_lots` insert triggers in `src/state/db.py` so direct SQL cannot create active exposure rows that bypass the API-level authority check.

## Phase 5 - Verification Plan

Required proof:

- Relationship tests in `tests/test_provenance_5_projections.py` proving active exposure lots fail closed when missing, mismatched, or malformed trade-fact authority is supplied.
- Producer compatibility tests proving WS/fill-tracker paths that use valid trade facts still append expected lots and failed-trade rollback still quarantines optimistic exposure.
- Focused compile/test gates for touched state and ingest tests.
- Downstream contamination sweep by grep/source inspection for remaining direct `position_lots` producers and consumers; any path that can still append active exposure without the API is residual risk or a separate route.
- `topology_doctor --planning-lock` using this plan as evidence.

## Implemented Repair

- `src/state/venue_command_repo.py`
  - Added active exposure authority validation for `append_position_lot`.
  - `OPTIMISTIC_EXPOSURE` now requires `source_trade_fact_id` referencing a `MATCHED` or `MINED` trade fact with positive finite fill economics.
  - `CONFIRMED_EXPOSURE` now requires `source_trade_fact_id` referencing a `CONFIRMED` trade fact with positive finite fill economics.
  - Active exposure lots must carry `source_command_id`, and that command id must match the referenced trade fact.
  - Active exposure lot `shares` and `entry_price_avg` must equal the referenced trade fact `filled_size` and `fill_price`.
  - Active exposure lots require the referenced command to be `ENTRY` / `BUY`.
- `src/state/db.py`
  - Added insert triggers that reject direct SQL `position_lots` inserts for active exposure states unless a state-compatible trade fact with positive fill economics and matching command exists.
  - The triggers use registered `zeus_positive_decimal_text` and `zeus_decimal_text_equal` SQLite functions so malformed text such as numeric-looking prefixes cannot pass as economics evidence and lot economics cannot drift from the referenced trade fact.
  - `get_connection()` and `init_provenance_projection_schema()` install these functions; unregistered external connections fail closed at trigger execution rather than silently accepting rows.
- `tests/test_provenance_5_projections.py`
  - Added relationship tests for missing authority, state mismatch, command mismatch, malformed direct trade facts, lot/trade economics mismatch, command lifecycle mismatch, and direct-SQL lot bypass.
- `tests/test_user_channel_ingest.py`
  - Updated WS gap guard fixtures so active exposure lots are seeded with canonical trade-fact authority instead of impossible standalone exposure rows.

## Verification Results

- `python3 scripts/topology_doctor.py --navigation --task "venue trade facts downstream object invariance sweep: position_lots must not consume malformed or lifecycle-only fill facts as active exposure authority" --intent modify_existing --write-intent edit --files src/state/venue_command_repo.py tests/test_provenance_5_projections.py` -> admitted.
- `python3 scripts/topology_doctor.py --navigation --task "venue trade facts downstream object invariance sweep: position_lots schema guard prevents direct SQL active exposure rows without state-compatible trade fact authority" --intent modify_existing --write-intent edit --files src/state/db.py tests/test_provenance_5_projections.py` -> admitted.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py src/state/venue_command_repo.py tests/test_provenance_5_projections.py tests/test_user_channel_ingest.py docs/operations/task_2026-05-08_object_invariance_wave27/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave27/PLAN.md` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/state/db.py src/state/venue_command_repo.py tests/test_provenance_5_projections.py tests/test_user_channel_ingest.py` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_provenance_5_projections.py tests/test_user_channel_ingest.py -q --tb=short` -> `64 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_command_recovery.py tests/test_exchange_reconcile.py tests/test_risk_allocator.py -q --tb=short` -> `76 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_backtest_skill_economics.py tests/test_calibration_retrain.py tests/test_live_safety_invariants.py tests/test_db.py -q --tb=short` -> `201 passed, 17 skipped`.

Observed verification noise:

- `tests/test_user_channel_ingest.py` still emits Python 3.12 sqlite datetime adapter deprecation warnings from pre-existing command insert paths. Non-failing and not repaired in this wave.

## Downstream Sweep

- Producers: `src/ingest/polymarket_user_channel.py` and `src/execution/fill_tracker.py` append trade facts before appending active lots and pass the returned `trade_fact_id`.
- API bypass: direct `position_lots` SQL inserts into the canonical schema are now blocked for active exposure states by triggers.
- Risk allocator: `src/risk_allocator/governor.py::load_position_lots` remains a read-only consumer of latest lots. This wave protects it upstream rather than adding duplicate joins.
- Reports/replay/learning: no new write path found in `src`, `tests`, or `scripts` that appends active exposure through `append_position_lot` without trade-fact authority.
- Test-only simplified tables remain in `tests/test_risk_allocator.py` and `tests/test_backtest_skill_economics.py`; they do not use the canonical schema and are not persistence authority.

Residual risk:

- Existing physical DB rows were not audited, relabeled, deleted, or backfilled. If historical `position_lots` rows are suspected, that requires operator-approved dry-run queries plus rollback/relabel plan.
- The trigger applies when `init_schema`/`init_provenance_projection_schema` runs; it is not a silent rewrite of legacy rows.

## Critic Loop

- First critic verdict: `REVISE`.
  - Critical: active exposure lot economics could diverge from the referenced trade fact because the first repair checked positive fill economics but did not compare `shares`/`entry_price_avg` to `filled_size`/`fill_price`.
  - Important: central seam did not enforce that active exposure lots come from `ENTRY` / `BUY` commands, leaving producer-local command eligibility as a bypass.
- Revision:
  - Added API and trigger equality checks for lot economics versus trade-fact economics.
  - Added API and trigger command eligibility checks for `ENTRY` / `BUY`.
  - Added API and direct-SQL relationship tests for both gaps.

## Stop Conditions

Stop and request operator decision if repair requires:

- physical DB audit, migration, backfill, deletion, or relabeling;
- schema migration or historical row rewrite;
- venue/account mutation or credential use;
- settlement harvest, redemption, or report publication;
- changing risk allocator or report/replay/learning consumers outside topology admission.

## Topology Notes

- `--task-boot-profiles` currently fails before usable boot because `architecture/task_boot_profiles.yaml:agent_runtime` references missing `architecture/topology_schema.yaml`.
- The route admits the central state seam and provenance tests for this wave, but rejects broad consumer/test updates unless routed separately. This reinforces the design choice to repair at the canonical append seam first.
- `docs/operations/current_state.md` currently contains missing historical packet references. This is unrelated route noise for this wave and is not repaired here.
