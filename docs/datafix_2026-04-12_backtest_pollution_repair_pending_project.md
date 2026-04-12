# RALPLAN — Backtest Pollution Repair And Backtest System Hardening

> Status: **Pending project / Not active packet**.
> This plan is parked until upstream raw/observed/statistical ingestion is repaired and re-audited.
> Do not execute this plan before validating that raw-to-DB settlement/statistical ingestion no longer writes unit-polluted or duplicated calibration data.

## Requirements Summary

Fix Zeus's backtest pollution problem at the system level. The goal is not to make the current report prettier; it is to prevent contaminated calibration/probability data, synthetic market prices, or incomplete trade-history evidence from guiding mathematical and statistical changes.

Inputs:

- context: `.omx/context/backtest_pollution_repair-20260412T183535Z.md`
- deep exploration: `.omx/context/backtest_pollution_deep_exploration-20260412T183535Z.md`
- latest WU sweep run: `231ee279-7b9`

Critical facts from exploration:

- `calibration_pairs`: `745679` rows, all with `decision_group_id`, no unique constraint on `(decision_group_id, range_label)`.
- All-history probability groups: `45535` total, `22564` valid, `22971` invalid.
- Invalid reasons: `p_sum_not_one=22842`, `yes_count_not_one=22386`, `duplicate_labels=19331`.
- Mutation paths include pair creators, metadata mutators, and model refitters. The problem is broader than `wu_settlement_sweep`.

## RALPLAN-DR Summary

### Principles

1. **Topology before math**: invalid probability groups are data/bin topology failures until proven otherwise.
2. **One identity**: every probability group must use one canonical `decision_group_id` helper.
3. **Derived means derived**: backtest and group-audit output may evaluate; it may not authorize live changes or mutate world/trade truth.
4. **Complete mutator coverage**: every calibration/platt writer or mutator must be known, classified, and gated or waived.
5. **No fake economics**: hypothetical PnL remains `N/A` without decision-time market price linkage.

### Decision Drivers

1. Stop bad backtest evidence from driving Kelly/FDR/alpha/calibration changes.
2. Preserve forensic evidence while preventing new pollution.
3. Make run eligibility machine-readable and visible in CLI output.

### Options Considered

#### Option A — Derived Audit + Mutator Matrix + Strict Offline Writes (Chosen)

Build a derived group-audit layer, centralize identity, classify all mutators, and add strict offline write helpers while preserving live harvester behavior.

Pros:

- Safe for live runtime.
- Gives immediate visibility into dirty groups.
- Prevents future offline scripts from adding more pollution.
- Preserves historical evidence for root-cause repair.

Cons:

- Does not immediately clean historical polluted rows.
- Requires a later remediation packet.

#### Option B — Clean `calibration_pairs` In Place Immediately

Deduplicate and normalize historical rows directly.

Pros:

- Fast visible improvement if done perfectly.

Cons:

- Rejected for first packet. It is destructive, may erase root-cause evidence, and would be unsafe before exact duplicate-vs-conflict classification is complete.

#### Option C — Tune Only On Clean Subset

Use `valid_group_forecast_skill` and ignore invalid groups.

Pros:

- Fastest path to some metrics.

Cons:

- Rejected. Clean subset may be selection-biased, and invalid groups represent roughly half of historical probability groups.

## Plan

### Phase 0 — Stop Using Multiple Group Identities

Touchpoints:

- `src/calibration/store.py`
- `src/calibration/effective_sample_size.py`
- `src/engine/replay.py`
- `scripts/audit_replay_fidelity.py`
- tests in `tests/test_calibration_manager.py`, `tests/test_backtest_settlement_value_outcome.py`, or a new focused test

Implementation:

1. Add one shared helper:

   ```python
   def decision_group_id_for(
       city: str,
       target_date: str,
       forecast_available_at: str,
       lead_days: float,
   ) -> str:
       return f"{city}|{target_date}|{forecast_available_at}|lead={float(lead_days):g}"
   ```

2. Replace local identity formatting in:
   - `src/calibration/store.py`
   - `src/calibration/effective_sample_size.py`
   - `src/engine/replay.py`
   - `scripts/audit_replay_fidelity.py`
3. Change replay fallback helpers so legacy rows without stored `decision_group_id` derive from full `(city, target_date, forecast_available_at, lead_days)`. The weak `calibration_pair:{available_at}:lead=...` shape is forbidden.

Acceptance:

- `rg "calibration_pair:" src/engine/replay.py scripts/audit_replay_fidelity.py src/calibration` returns no group identity fallback.
- A test proves missing-`decision_group_id` rows group identically in store/effective_sample_size/replay/fidelity.

### Phase 1 — Add Derived Probability Group Audit Storage

Touchpoints:

- `src/state/db.py`
- `src/engine/replay.py`
- `scripts/run_replay.py`
- tests for backtest schema and WU sweep

Implementation:

1. Add `zeus_backtest.db.backtest_probability_group_audit`:

   ```sql
   CREATE TABLE IF NOT EXISTS backtest_probability_group_audit (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       run_id TEXT NOT NULL,
       lane TEXT NOT NULL,
       decision_group_id TEXT NOT NULL,
       city TEXT NOT NULL,
       target_date TEXT NOT NULL,
       forecast_available_at TEXT NOT NULL,
       lead_days REAL NOT NULL,
       row_count INTEGER NOT NULL,
       distinct_label_count INTEGER NOT NULL,
       p_raw_sum REAL,
       yes_count INTEGER NOT NULL,
       valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
       invalid_reason_json TEXT NOT NULL,
       label_set_hash TEXT NOT NULL,
       sample_row_ids_json TEXT NOT NULL,
       sample_rows_json TEXT NOT NULL,
       authority_scope TEXT NOT NULL DEFAULT 'diagnostic_non_promotion'
           CHECK (authority_scope = 'diagnostic_non_promotion'),
       created_at TEXT NOT NULL,
       UNIQUE(run_id, decision_group_id)
   );
   ```

2. `sample_rows_json` is a bounded array of row evidence:

   ```json
   {
     "row_id": 123,
     "range_label": "74-75°F",
     "p_raw": 0.94,
     "stored_outcome": 0,
     "derived_wu_outcome": 0,
     "forecast_available_at": "2026-04-01T08:00:00Z",
     "lead_days": 1.0,
     "decision_group_id": "city|date|available_at|lead=1",
     "source_hint": "calibration_pairs"
   }
   ```

3. `wu_settlement_sweep` writes one audit row per probability group.
4. CLI reads report summary and prints validity counts, root-cause reasons, and clean-subset metrics.

Acceptance:

- One audit row exists per `(run_id, decision_group_id)`.
- Audit rows have `authority_scope='diagnostic_non_promotion'`.
- Invalid groups include reproducible row-level sample evidence.

### Phase 2 — Build Calibration/Platt Mutator Matrix

Touchpoints:

- `scripts/audit_replay_fidelity.py`
- optionally new helper module if the audit grows too large
- tests for static mutator inventory

Implementation:

1. Add or generate a mutator matrix from:

   ```text
   rg "calibration_pairs|platt_models|add_calibration_pair|_fit_from_pairs|UPDATE calibration|DELETE FROM platt|INSERT INTO calibration" scripts src -g '*.py'
   ```

2. Initial required classifications:
   - live writer: `src/execution/harvester.py`
   - live refit trigger: `src/execution/harvester.py -> src/calibration/manager.py:maybe_refit_bucket() -> _fit_from_pairs() -> save_platt_model()`
   - live on-demand calibrator trigger: `src/engine/evaluator.py` / `src/engine/monitor_refresh.py -> src/calibration/manager.py:get_calibrator() -> _fit_from_pairs() -> save_platt_model()`
   - pair row creators: `scripts/etl_tigge_calibration.py`, `scripts/etl_tigge_direct_calibration.py`, `scripts/etl_tigge_ens.py`, `scripts/generate_calibration_pairs.py`
   - pair metadata mutators: `src/calibration/effective_sample_size.py`, `scripts/backfill_cluster_taxonomy.py`
   - model mutators/refitters: `scripts/refit_platt.py`, TIGGE ETL refit sections, `src/calibration/manager.py`
   - read-only analyzers: classify explicitly as read-only
3. `audit_replay_fidelity.py` reports unclassified mutators as blockers.

Acceptance:

- `scripts/backfill_cluster_taxonomy.py` is included.
- Any new calibration/platt mutator fails audit until classified.
- The audit distinguishes live writer, offline writer, metadata mutator, model refitter, and read-only analyzer.

### Phase 3 — Strict Offline Write Path Without Breaking Live Harvester

Touchpoints:

- `src/calibration/store.py`
- offline pair creators listed in Phase 2
- tests for idempotency and live harvester non-breakage

Implementation:

1. Preserve current `add_calibration_pair()` behavior for live harvester in this packet.
2. Add strict offline helper:
   - suggested name: `add_calibration_pair_strict()` or `write_calibration_group_strict()`
   - identity: `(decision_group_id, range_label)`
   - same-key same-value: returns `"noop_same"`
   - new row: returns `"inserted"`
   - same-key conflicting value: raises `CalibrationPairConflictError`
3. Strict ETL scripts catch only `CalibrationPairConflictError`, append to `quarantined_pairs`, and continue.
4. Remove broad `except Exception: pass` around strict calibration writes.
5. Do not add a raw-table unique index until historical polluted rows are cleaned or isolated. Derived audit table can be unique by `(run_id, decision_group_id)`.

Acceptance:

- Re-running strict offline ETL does not increase pair count.
- Same-key conflicting rows are quarantined, not appended.
- Live `harvest_settlement()` regression still passes unchanged.
- Static audit proves offline pair creators no longer use non-strict helper unless explicitly waived.

### Phase 4 — Refitter And Model Training Guardrails

Touchpoints:

- `src/calibration/manager.py`
- new module candidate: `src/calibration/model_training_gate.py`
- `scripts/refit_platt.py`
- TIGGE ETL refit sections
- `scripts/audit_replay_fidelity.py`
- `tests/test_cluster_taxonomy_backfill.py`

Implementation:

1. Add a hard eligibility gate for *model-training writes* to `platt_models`.
   - This gate does not block live order execution directly.
   - It does block writing new or replacement Platt models from polluted bucket inputs.
   - Existing active models remain as-is unless a separate governance packet deactivates them.
2. Create one central gated model-training service. Concrete contract:

   ```python
   @dataclass(frozen=True)
   class ModelTrainingGateResult:
       bucket_key: str
       cluster: str
       season: str
       status: Literal[
           "fit_saved",
           "blocked_contaminated_input",
           "insufficient_samples",
           "fit_failed",
       ]
       model_written: bool
       n_pairs: int
       valid_probability_groups: int
       invalid_probability_groups: int
       blocking_reasons: tuple[str, ...]
       brier_insample: float | None = None
       error: str | None = None

   def fit_and_save_platt_if_eligible(
       conn: sqlite3.Connection,
       cluster: str,
       season: str,
       *,
       source: Literal["runtime_get_calibrator", "runtime_harvest", "refit_script", "etl_script"],
   ) -> ModelTrainingGateResult: ...
   ```

   Rules:

   - only `status == "fit_saved"` may call `save_platt_model()`;
   - `blocked_contaminated_input` returns `model_written=False` and includes `invalid_probability_groups`;
   - `insufficient_samples` returns `model_written=False` and preserves existing models;
   - `fit_failed` returns `model_written=False` with `error`.
3. It may wrap or replace `src/calibration/manager.py:_fit_from_pairs()`, but all paths that can write `platt_models` must pass through this gate or be explicitly rejected:
   - `get_calibrator()` on-demand runtime refit;
   - `maybe_refit_bucket()` after harvest;
   - offline `scripts/refit_platt.py`;
   - TIGGE ETL refit sections.
   Direct `INSERT OR REPLACE INTO platt_models` in scripts is forbidden after this packet.
4. For the central gate and `scripts/refit_platt.py`:
   - before fitting a bucket, compute group integrity for the bucket's input groups;
   - if invalid groups are present, skip the write and return/report a structured blocked status;
   - do not call `save_platt_model()` for contaminated buckets.
5. For live runtime callers:
   - `get_calibrator()` may still return an existing active model if present;
   - if it would otherwise refit from polluted rows, it must skip training and return `None` / raw-probability fallback according to existing behavior;
   - this prevents new contaminated model writes without directly blocking live evaluation or monitoring.
6. For the live harvester refit trigger:
   - `harvest_settlement()` may still write settlement calibration rows through the existing live path;
   - `maybe_refit_bucket()` must skip refit and return `False` or a structured no-refit result when bucket input is contaminated;
   - this is a model-training safety gate, not a live trading gate.
7. `scripts/refit_platt.py` should fail closed by default:
   - default: no write for contaminated buckets;
   - implementation must call the central gated model-training service instead of writing `platt_models` directly;
   - optional future `--force-contaminated` would require explicit governance and must label model output as contaminated. Do not add force mode in this packet.
8. `scripts/backfill_cluster_taxonomy.py` must not unconditionally delete `platt_models`.
   - The current delete-then-refit behavior is unsafe while inputs are polluted.
   - In this packet, change it to dry-run/report-only for model invalidation, or route model rebuild through the central gated training service.
   - If gated refit is blocked, existing models remain untouched and the report says cluster taxonomy changed but model rebuild was blocked by contamination.
   - Update `tests/test_cluster_taxonomy_backfill.py`; the old expectation `platt_models_cleared == 1` becomes invalid. The new test must assert that models are preserved when gated refit is blocked, and that the report exposes a blocked/ineligible model rebuild status.
   Concrete replacement report fields:

   ```python
   {
       "calibration_pairs_updated": int,
       "calibration_pairs_skipped": int,
       "platt_models_existing": int,
       "platt_models_deleted": 0,
       "platt_models_preserved": int,
       "platt_model_rebuild_status": "blocked_contaminated_input" | "fit_saved" | "not_attempted",
       "platt_model_rebuild_blocking_reasons": list[str],
   }
   ```

   Replacement test contract:

   - no unconditional delete;
   - old model row remains when gated rebuild is blocked;
   - `platt_models_deleted == 0`;
   - `platt_models_preserved == platt_models_existing`;
   - `platt_model_rebuild_status == "blocked_contaminated_input"` for polluted fixture.
9. `audit_replay_fidelity.py` emits:
   - `math_decision_eligible`
   - `model_tuning_allowed`
   - `blocking_reasons`
10. Authority boundary:
   - These eligibility fields are computed from canonical world inputs (`calibration_pairs`, `settlements`, `ensemble_snapshots`) plus deterministic derived group-integrity checks.
   - They are mirrored into `zeus_backtest.db` for reporting.
   - `zeus_backtest.db` is not the source of truth for the gate.
   - They are advisory for live order execution but hard blockers for new model-training writes to `platt_models`.

Stable blocking reason codes:

- `invalid_probability_groups`
- `temporal_violation`
- `no_valid_probability_groups`
- `market_price_unavailable`
- `no_trade_history_subjects`
- `unclassified_calibration_mutator`

Acceptance:

- Current polluted DB reports `model_tuning_allowed=false`.
- Refitter paths skip `platt_models` writes when bucket input is contaminated.
- Live `harvest_settlement()` can still complete settlement harvesting, but its downstream refit trigger must not write a new contaminated Platt model.
- No backtest/refit advisory field is used by live runtime.
- `scripts/refit_platt.py` has no direct `INSERT OR REPLACE INTO platt_models` path outside the central gate.
- `scripts/backfill_cluster_taxonomy.py` no longer deletes all `platt_models` before a gated refit succeeds.
- `tests/test_cluster_taxonomy_backfill.py` no longer expects unconditional model deletion; it expects preserved models or gated rebuild status.

### Phase 5 — Price Linkage Remains Separate

Touchpoints:

- `src/engine/replay.py`
- `scripts/audit_replay_fidelity.py`
- `market_events`
- `token_price_log`

Implementation:

1. Keep PnL `N/A` until price linkage passes:
   - same city/date;
   - same bin topology;
   - timestamp at or before decision time;
   - mapped bid/ask/mid source;
   - no blank range labels unless token mapping is resolved independently.
2. Do not mix price-linkage repair with probability topology cleanup.

Acceptance:

- `audit` mode prints PnL denominator only for price-linked subjects.
- Missing market prices cannot produce synthetic PnL.

## Pre-Mortem

1. **Mutator inventory misses a path**: a script mutates calibration/platt state outside strict checks.
   - Mitigation: static mutator matrix gate with unclassified-mutator blocker.
2. **Derived audit becomes de facto authority**: future agents use `zeus_backtest.db` to authorize live changes.
   - Mitigation: `authority_scope='diagnostic_non_promotion'`, AGENTS warning, and tests confirming live code does not read backtest DB.
3. **Strict helper breaks live harvester**: shared function changes throw exceptions in live settlement harvest.
   - Mitigation: preserve `add_calibration_pair()` in first packet; add strict helper only for offline writers; regression test harvester.
4. **Clean subset is over-trusted**: valid groups are interpreted as full-system performance.
   - Mitigation: always print all-row contamination and valid subset denominator.
5. **Historical cleanup erases evidence**: dedupe script deletes rows before root cause is classified.
   - Mitigation: no destructive cleanup in this plan; cleanup is a later packet after audit storage exists.

## Test Plan

### Unit

- shared `decision_group_id_for()` parity across store/effective_sample_size/replay/fidelity.
- group audit detects duplicate labels, p_sum errors, multi-YES, parse failures.
- invalid groups excluded from top-k denominators.
- `sample_rows_json` has required fields.
- strict helper: inserted / noop_same / conflict.

### Integration

- synthetic DB with clean + dirty groups through `wu_settlement_sweep`.
- static mutator matrix includes `backfill_cluster_taxonomy.py`.
- repeated strict ETL fixture does not inflate `calibration_pairs`.
- refit audit reports contaminated input when dirty groups exist.
- `maybe_refit_bucket()` / `_fit_from_pairs()` does not call `save_platt_model()` when bucket group integrity is invalid.
- `get_calibrator()` runtime path does not write a new model when bucket group integrity is invalid.
- live harvester settlement path still returns success while downstream contaminated refit is skipped.
- cluster taxonomy backfill preserves existing `platt_models` if gated rebuild is blocked and updates its test contract accordingly.

### E2E

- `python scripts/run_replay.py --mode wu_settlement_sweep --start 2026-04-01 --end 2026-04-07`
- `python scripts/audit_replay_fidelity.py`
- `python scripts/run_replay.py --mode audit --allow-snapshot-only-reference --start 2026-04-01 --end 2026-04-07`

### Verification Commands

```bash
python -m pytest tests/test_backtest_settlement_value_outcome.py tests/test_run_replay_cli.py tests/test_backtest_outcome_comparison.py tests/test_backtest_trade_subject_identity.py tests/test_tigge_snapshot_p_raw_backfill.py tests/test_replay_time_provenance.py -q
python scripts/audit_replay_fidelity.py
python scripts/run_replay.py --mode wu_settlement_sweep --start 2026-04-01 --end 2026-04-07
python scripts/run_replay.py --mode audit --allow-snapshot-only-reference --start 2026-04-01 --end 2026-04-07
git diff --check
```

## Acceptance Criteria

- One shared canonical `decision_group_id` helper is used by all relevant paths.
- Backtest DB has a derived group-audit table with row-level sample evidence.
- Every calibration/platt mutator is classified.
- Offline calibration writers use strict idempotent writes or explicit waiver.
- Live harvester remains unchanged or regression-proven safe.
- Dirty probability groups block `model_tuning_allowed`.
- Refits cannot write new `platt_models` while their bucket inputs are polluted.
- PnL remains `N/A` without price linkage.

## ADR

Decision: Repair backtest trustworthiness by first centralizing identity, adding derived group-audit storage, classifying all calibration/platt mutators, and adding strict offline write paths. Do not mutate historical rows or change live harvester behavior in the first packet.

Drivers:

- Bad backtests create worse math.
- Half of historical probability groups are invalid.
- Live runtime must not be disturbed by diagnostic repair.
- Forensic evidence must be preserved before cleanup.

Alternatives:

- In-place cleanup first: rejected as destructive before root causes are classified.
- Clean-subset-only tuning: rejected because it hides systemic pollution.
- Price-linkage first: rejected because PnL is downstream of probability topology.

Consequences:

- The system becomes stricter and may report more `N/A` / blocker states.
- Implementation spans schema, replay, audit, calibration store, ETL scripts, and tests.
- Historical cleanup remains a follow-up after classification.

Follow-ups:

- Historical remediation/deduplication packet.
- Market-price linkage packet.
- Backtest dashboard/export once report contract is stable.

## Available Agent Types Roster

- `explore`: fast codebase/search mapping.
- `architect`: schema/truth-surface/live boundary review.
- `debugger`: root-cause analysis for dirty groups.
- `executor`: implementation.
- `test-engineer`: synthetic DB and regression tests.
- `code-reviewer`: module coupling review.
- `critic`: adversarial metric/replay review.
- `verifier`: final evidence validation.

## Staffing Guidance

### Ralph Path

Use `$ralph` for conservative sequential execution:

1. debugger maps dirty root causes and mutator matrix.
2. architect validates schema and live boundary.
3. executor implements identity/helper/schema/report changes.
4. test-engineer builds clean/dirty synthetic fixtures.
5. critic attacks metric eligibility and synthetic PnL assumptions.
6. verifier runs full command set.

### Team Path

Use `$team` for parallel execution:

- Lane A: identity + mutator matrix.
- Lane B: backtest DB schema + replay writer.
- Lane C: strict offline helper + ETL conversion.
- Lane D: refit/model eligibility reporting.
- Lane E: tests and adversarial dirty fixtures.

Launch hint:

```text
$team "Implement .omx/plans/datafix_2026-04-12_backtest_pollution_repair_plan.md. Preserve live harvester behavior, add derived group audit, classify all calibration/platt mutators, add strict offline write paths, and verify with WU sweep/fidelity/audit."
```

Team verification path:

1. Each lane proves its tests.
2. Leader runs all verification commands.
3. Critic confirms no contaminated metric is reported as eligible.
4. Verifier confirms live runtime does not read `zeus_backtest.db`.
