# Object Invariance Wave 25 - Confirmed Trade Fact Economics Authority

Status: IMPLEMENTED FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT DB MUTATION AUTHORITY

Created: 2026-05-07
Last reused or audited: 2026-05-07
Authority basis: root AGENTS.md object-meaning invariance goal; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md remaining-mainline ledger; src/state/AGENTS.md; src/execution/AGENTS.md; src/calibration/AGENTS.md

## Scope

Repair one boundary class:

`venue trade lifecycle state -> executable / learnable trade economics evidence`

This wave does not mutate live/canonical databases, run backfills, relabel legacy rows, publish reports, or authorize live unlock. It is source/test enforcement only.

## Topology Record

- `python3 scripts/topology_doctor.py --navigation --task "create operation planning packet for object-meaning invariance wave25 venue_trade_facts confirmed economics authority downstream sweep" --intent create_new --write-intent add --files docs/operations/task_2026-05-07_object_invariance_wave25/PLAN.md docs/operations/AGENTS.md`
  - Result: `scope_expansion_required`.
  - `PLAN.md` admitted; `docs/operations/AGENTS.md` rejected even though map maintenance later requires packet registry rows.
- `python3 scripts/topology_doctor.py --navigation --task "object-meaning invariance wave25 venue_trade_facts confirmed economics authority downstream sweep: CONFIRMED trade facts must carry positive finite fill economics before persistence, backtest readiness, or calibration retrain consumption" --intent modify_existing --write-intent edit --files src/state/venue_command_repo.py src/backtest/economics.py src/calibration/retrain_trigger.py tests/test_provenance_5_projections.py tests/test_backtest_skill_economics.py tests/test_calibration_retrain.py docs/operations/task_2026-05-07_object_invariance_wave25/PLAN.md docs/operations/AGENTS.md`
  - Result: `advisory_only`.
  - Cause: high-fanout `src/state/venue_command_repo.py` matched multiple historical profiles; route required packet first and smaller structural-decision slices.
- `python3 scripts/topology_doctor.py --task-boot-profiles`
  - Result: failed before usable boot because `architecture/task_boot_profiles.yaml:agent_runtime` references missing `architecture/topology_schema.yaml`.
- Smaller admitted implementation slices:
  - U2 persistence route: `venue trade facts U2 raw provenance schema object invariance: fill-progress venue_trade_facts require positive finite fill economics` admitted `src/state/venue_command_repo.py` and `tests/test_provenance_5_projections.py`.
  - F2 retrain route: `F2 Calibration retrain loop: confirmed trade facts with invalid fill economics fail closed before promotion corpus` admitted `src/calibration/retrain_trigger.py`, `tests/test_calibration_retrain.py`, `tests/test_provenance_5_projections.py`, and `docs/operations/AGENTS.md`.
  - Phase 5 readiness route: `Phase 5B forward substrate DSA-19 economics readiness contract requires confirmed execution substrate with fill economics` admitted `src/backtest/economics.py` and `tests/test_backtest_skill_economics.py`.
- Critic-driven M5 route: `M5 exchange reconciliation sweep object invariance: journal positions must ignore malformed venue_trade_facts without positive finite fill economics`
  - Result: admitted `src/execution/exchange_reconcile.py` and `tests/test_exchange_reconcile.py`; rejected unrelated Wave25 docs/backtest files in the same command, so implementation stayed within the admitted M5 slice and this packet records the cross-slice rationale.

Topology compatibility finding: housekeeping improvements reduced some process tax, but cross-module object-invariance repairs still need a better route for shared persistence seams and required registry maintenance.

## Money-Path Map

Relevant path:

`submitted order -> venue trade fact -> command state -> position lot -> reconciliation/current exposure -> replay/report/economics readiness -> calibration retrain corpus`

Authority surfaces observed:

- Persistence authority: `src/state/db.py` `venue_trade_facts` schema and `src/state/venue_command_repo.py` append/read APIs.
- Execution producers: `src/ingest/polymarket_user_channel.py`, `src/execution/fill_tracker.py`, `src/execution/exchange_reconcile.py`.
- Lifecycle/position consumers: `position_lots` via `append_position_lot`, command events, exchange reconciliation journal views.
- Learning/reporting consumers: `src/calibration/retrain_trigger.py`, `src/backtest/economics.py`.
- Tests: `tests/test_provenance_5_projections.py`, `tests/test_live_safety_invariants.py`, `tests/test_calibration_retrain.py`, `tests/test_backtest_skill_economics.py`.

## Boundary Selection

Candidates after Wave 24:

| Boundary | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Canonical settlement env downstream sweep | Settlement/report/replay/learning cohorting | `position_events.env`, `event_type=SETTLED`, settlement authority payload | Direct legacy rows and pre-existing DBs may still need operator-approved audit | Wave 24 already repaired local source/test slice; remaining DB audit is operator-decision territory |
| Shared `venue_trade_facts` downstream sweep | Fill authority, optimistic/confirmed lots, command recovery, portfolio risk, replay/report/economics readiness, calibration retrain | `state`, `filled_size`, `fill_price`, `source`, `trade_id`, `venue_order_id`, `command_id`, `observed_at`, `raw_payload_json` | Direct SQL/test/legacy rows can still use `CONFIRMED` as a lifecycle label without positive finite economics | Safe as source/test fail-closed enforcement; no DB mutation |

Selected: shared `venue_trade_facts`, because it sits earlier in the live-money path than settlement reports and can affect position exposure, risk, and learning semantics.

## Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `state` | Venue trade lifecycle phase, not by itself an economic fill object | WS/REST/exchange reconciliation producers; `append_trade_fact` | Venue observation, with producer-specific source | execution/lifecycle evidence | enum at observed/venue timestamp | Producer normalizes to `MATCHED/MINED/CONFIRMED/RETRYING/FAILED` | `venue_trade_facts.state` | command events, lots, reconciliation, calibration, economics readiness | Preserved as lifecycle phase, but ambiguous when consumed as economics authority alone |
| `filled_size` | Shares filled for the specific trade fact | WS/REST trade payload or failed-state compatibility value | Venue observation only if explicit and positive finite | economics evidence | shares at observed/venue timestamp | decimal text | `venue_trade_facts.filled_size`; provenance payload | lots, exposure reconciliation, calibration corpus, readiness | Broken if `CONFIRMED` can persist/flow with zero, missing, nonnumeric, or nonfinite value |
| `fill_price` | Average realized fill price for that trade | WS/REST trade payload | Venue observation only if explicit and positive finite | economics evidence | probability/share price, 0..1 market range at observed/venue timestamp | decimal text | `venue_trade_facts.fill_price`; provenance payload | lots, PnL/economics readiness, calibration corpus | Broken if lifecycle state is accepted without positive finite price |
| `source` | Observation channel that supplied the fact | Producer call | REST/WS/CHAIN/etc. | source provenance | captured/observed timestamp | enum validation | `venue_trade_facts.source` | audit/report/replay | Preserved; this wave does not reclassify source hierarchy |
| `raw_payload_json` | Raw venue/provenance payload and optional calibration identity | Producer call | producer-specific raw evidence | raw/provenance and calibration identity evidence | payload time, often venue timestamp | JSON coercion | `venue_trade_facts.raw_payload_json` | calibration identity filter, audit | Identity filter exists; economics validity still must be checked |

## Findings

### W25-F1 - CONFIRMED state can be treated as complete economic evidence without positive finite fill economics

Classification: S1 active for learning/readiness authority; S2 for tombstoned backtest execution; source-level persistence risk for any direct producer/test/legacy path.

Object meaning that changes:

`state='CONFIRMED'` denotes lifecycle/finality. Downstream calibration and readiness consumers need a different object: confirmed trade economics with explicit positive finite `filled_size` and `fill_price`.

Boundary:

`venue_trade_facts` persistence/read APIs -> calibration retrain corpus and economics readiness.

Code paths:

- `src/state/venue_command_repo.py::append_trade_fact` validates enum/source/hash but not positive finite economics for fill-progress states.
- `src/state/venue_command_repo.py::load_calibration_trade_facts` filters only `state='CONFIRMED'`.
- `src/backtest/economics.py::check_economics_readiness` counts only `state='CONFIRMED'`.
- `src/calibration/retrain_trigger.py::load_confirmed_corpus` checks state and calibration identity but not fill economics.

Economic impact:

Malformed `CONFIRMED` rows can become retrain evidence, readiness evidence, or replay/report substrate even though they do not carry the economic object required to price, size, attribute PnL, or learn from a trade.

Reachability:

Producer paths inspected already guard most live WS/REST messages, but the shared repository and downstream consumers remain a bypass for direct SQL, legacy, diagnostic, or future producer paths. This makes the corrected producer semantics incomplete.

## Repair Design

Invariant restored:

For `MATCHED`, `MINED`, and `CONFIRMED` trade facts, lifecycle state and economic evidence are separate required components. A fill-progress or fill-finality row is valid only when `filled_size` and `fill_price` are positive finite decimal values. `FAILED` and `RETRYING` remain allowed to carry non-economic compatibility values so failure rollback evidence is not blocked.

Durable mechanisms:

- Add a central repository predicate for positive finite fill economics.
- Enforce the predicate in `append_trade_fact` for fill-progress states (`MATCHED`, `MINED`, `CONFIRMED`).
- Enforce the same predicate in `load_calibration_trade_facts` so legacy/direct malformed confirmed rows fail closed before learning consumers.
- Make `load_confirmed_corpus` surface the repository failure as `UnsafeCorpusFilter` for retrain semantics.
- Make economics readiness require both schema columns and at least one confirmed row with valid economics before reporting the confirmed-trade substrate as present.
- Relationship tests cover producer/persistence and downstream consumer seams.

## Verification Plan

Minimum gates:

- `python -m py_compile src/state/venue_command_repo.py src/backtest/economics.py src/calibration/retrain_trigger.py tests/test_provenance_5_projections.py tests/test_backtest_skill_economics.py tests/test_calibration_retrain.py`
- Targeted pytest for:
  - trade fact persistence rejects malformed fill-progress economics;
  - `FAILED` rollback compatibility remains allowed;
  - calibration corpus rejects malformed confirmed economics;
  - economics readiness does not count `CONFIRMED` without fill economics contract.
- `git diff --check`
- `topology_doctor --planning-lock` and `--map-maintenance --map-maintenance-mode closeout` when changed files are known.
- Critic review after several related repairs land.

## Implemented Repair

- `src/state/venue_command_repo.py`
  - Added `trade_fact_has_positive_fill_economics()`.
  - `append_trade_fact()` now rejects `MATCHED`, `MINED`, and `CONFIRMED` rows unless `filled_size` and `fill_price` are positive finite decimals.
  - `FAILED` and `RETRYING` remain allowed to carry non-fill economics so failure evidence can roll back optimistic exposure.
  - `load_calibration_trade_facts()` now fails closed on legacy/direct malformed `CONFIRMED` rows before they can enter calibration consumers.
- `src/calibration/retrain_trigger.py`
  - Converts repository economics failures into `UnsafeCorpusFilter` so retrain/promotion remains fail-closed.
- `src/backtest/economics.py`
  - Readiness no longer treats lifecycle-only `CONFIRMED` rows as confirmed economics substrate.
  - It requires the `filled_size`/`fill_price` schema contract and at least one positive finite confirmed row before clearing that substrate blocker.
  - After critic review, readiness now also fails closed if any malformed `CONFIRMED` row exists, even when other confirmed rows are valid.
- `src/execution/exchange_reconcile.py`
  - After critic review, local journal exposure no longer counts `MATCHED`, `MINED`, or `CONFIRMED` trade facts unless both `filled_size` and `fill_price` satisfy the same positive finite economics predicate.
- Tests
  - Added relationship tests for persistence rejection, `FAILED/RETRYING` rollback compatibility, calibration fail-closed behavior, and economics readiness rejection of lifecycle-only or zero-economics confirmed rows.
  - Added critic-regression tests for mixed valid/invalid confirmed readiness rows and malformed direct-SQL confirmed rows flowing into exchange reconciliation journal exposure.
  - Minimally aligned `tests/test_calibration_retrain.py` Platt fixture with current cycle/source/horizon stratification to remove unrelated F2 verification noise.

## Verification Results

- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/state/venue_command_repo.py src/calibration/retrain_trigger.py src/backtest/economics.py tests/test_provenance_5_projections.py tests/test_calibration_retrain.py tests/test_backtest_skill_economics.py` -> pass.
- Focused relationship tests:
  - `tests/test_provenance_5_projections.py::test_fill_progress_trade_facts_require_positive_finite_economics`
  - `tests/test_provenance_5_projections.py::test_failed_and_retrying_trade_facts_allow_non_fill_economics`
  - `tests/test_provenance_5_projections.py::test_calibration_training_rejects_confirmed_without_fill_economics`
  - `tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only`
  - `tests/test_calibration_retrain.py::test_confirmed_corpus_missing_fill_economics_fails_closed`
  - `tests/test_calibration_retrain.py::test_arm_then_trigger_consumes_confirmed_trades_only`
  - `tests/test_backtest_skill_economics.py::test_economics_readiness_requires_confirmed_trade_fill_economics`
  - `tests/test_backtest_skill_economics.py::test_economics_readiness_rejects_confirmed_trade_lifecycle_only_schema`
  - `tests/test_backtest_skill_economics.py::test_economics_readiness_full_substrate_still_blocks_until_engine_implemented`
  - Result: `9 passed`.
- File-level suites: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_provenance_5_projections.py tests/test_calibration_retrain.py tests/test_backtest_skill_economics.py -q --tb=short` -> `49 passed`.
- PR67 rollback regression guard: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_user_channel_ingest.py::test_failed_without_fill_economics_after_fill_observation_rolls_back_optimistic_projection tests/test_user_channel_ingest.py::test_failed_after_matched_quarantines_or_reverses_optimistic_projection tests/test_user_channel_ingest.py::test_failed_after_mined_quarantines_optimistic_projection -q --tb=short` -> `4 passed`.
- Post-critic file-level suites: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_provenance_5_projections.py tests/test_calibration_retrain.py tests/test_backtest_skill_economics.py tests/test_exchange_reconcile.py -q --tb=short` -> `83 passed`.
- Post-critic producer/recovery sweep: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_user_channel_ingest.py tests/test_command_recovery.py -q --tb=short` -> `64 passed`.
- `git diff --check` -> pass.

Observed verification noise:

- Pytest emits the current writer-lock informational warning about direct `sqlite3.connect()` sites. This is mainline noise and not repaired in this wave.
- `tests/test_user_channel_ingest.py` includes Python 3.14 sqlite datetime adapter deprecation warnings on rollback tests. This is non-failing and not repaired in this wave.

## Downstream Sweep Targets

- monitor/exit paths: no direct confirmed-economics consumer identified in this wave except existing fill authority tests.
- settlement paths: out of scope; Wave 24 covers canonical settlement env semantics.
- replay/report/economics paths: `src/backtest/economics.py` readiness must reject lifecycle-only `CONFIRMED`.
- reconciliation paths: `src/execution/exchange_reconcile.py` journal exposure must reject malformed fill-progress trade facts before comparing exchange position balances.
- learning/calibration paths: `load_calibration_trade_facts` and `load_confirmed_corpus` must reject malformed confirmed economics.
- legacy/compatibility/fallback paths: direct SQL malformed rows must fail closed at consumers; existing DB rows require separate operator-approved audit/backfill/relabel plan if physical contamination is suspected.

## Residual Risk

This patch prevents new malformed fill-progress facts through the repository seam and blocks malformed direct/legacy `CONFIRMED` rows at calibration/readiness consumers. It does not audit, migrate, relabel, or delete any existing physical DB rows. If operators suspect historical malformed `CONFIRMED` rows in live databases, that requires a separate dry-run audit and rollback/relabel plan.

## Critic Loop

- First critic verdict: `REVISE`.
  - Important 1: economics readiness accepted a mixed set of valid and malformed `CONFIRMED` rows. Fixed by counting invalid confirmed rows and blocking whenever any malformed confirmed economics exists.
  - Important 2: exchange reconciliation journal exposure used only `filled_size` and could materialize a direct/legacy `CONFIRMED` row with `fill_price='0'`. Fixed by applying the shared positive fill-economics predicate before journal exposure.
- Second critic verdict: `APPROVE`.
  - Confirmed both REVISE paths are closed.
  - Confirmed `append_trade_fact()` rejects malformed fill-progress rows while preserving `FAILED`/`RETRYING` appendability.
  - Confirmed calibration/retrain fails closed through the repository seam.
  - Confirmed no remaining reviewed learning/readiness/reconciliation path promotes lifecycle-only `CONFIRMED`.
