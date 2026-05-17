# docs/operations/learning_loop_observation/

Created: 2026-04-29
Authority basis: round3_verdict.md §1 #2 (FIFTH and FINAL edge packet) +
ULTIMATE_PLAN.md §4 #4 (LEARNING_LOOP_PACKET — settlement-corpus →
calibration update → parameter-drift → re-fit pipeline) + LEARNING_LOOP
packet boot §1 + critic 31st cycle GO_BATCH_3 dispatch (3-kind composable
detector + cross-module orchestration + per-bucket threshold dict +
LOW-DESIGN-LL-2-1 documentation requirement).

## What lives here

JSON reports emitted by `scripts/learning_loop_observation_weekly.py` —
one file per weekly LEARNING_LOOP run, named `weekly_<YYYY-MM-DD>.json`
where the date is the inclusive window-end day.

Each report carries:

- `current_window`: per-bucket-key learning-loop pipeline state from
  `src.state.learning_loop_observation.compute_learning_loop_state_per_bucket` —
  4-stage shape:
    * Calibration-pair: `n_pairs_total`, `n_pairs_verified`,
      `n_pairs_canonical`, `n_decision_groups`
    * Retrain: `retrain_status` (process-level), `n_retrain_attempts_in_window`,
      `n_retrain_passed_in_window`, `n_retrain_failed_in_window`,
      `last_retrain_attempted_at`, `last_retrain_promoted_at`,
      `days_since_last_promotion`
    * Active model: `active_model_fitted_at`, `active_model_n_samples`
    * Provenance: `bucket_key`, `source` (`v2` | `legacy`),
      `temperature_metric`, `cluster`, `season`, `data_version`,
      `input_space`
    * Sample quality: `sample_quality` (driven by `n_pairs_canonical`)
    * Window: `window_start`, `window_end`
- `stall_verdicts`: per-bucket `ParameterStallVerdict` from
  `src.state.learning_loop_observation.detect_learning_loop_stall` over
  the trailing `n_windows_for_stall` history — `kind` ∈
  {`stall_detected`, `within_normal`, `insufficient_data`}, `stall_kinds`
  list (subset of 3 kind names), optional `severity` ∈ {`warn`,
  `critical`}, plus per-kind `evidence` (per-kind status + numeric
  details + reason on insufficient).
- `per_bucket_thresholds`: 3-tuple threshold dict actually used per
  bucket (defaults + any operator overrides applied — see below).
- `drift_detected_map`: per-bucket result of
  `src.state.calibration_observation.detect_parameter_drift` over the
  parameter-snapshot history. Values: `True` (drift detected), `False`
  (within normal), `None` (insufficient data). The runner is the ONLY
  place that performs this cross-packet join — keeps
  `detect_learning_loop_stall` pure-Python with caller-provided drift_detected.
- Generation metadata: `report_kind`, `report_version`, `generated_at`,
  `end_date`, `window_days`, `n_windows_for_stall`, `db_path`.

## Authority class

**Derived context — NOT authority.** These reports are evidence the
operator can use to spot learning-loop stalls; they do NOT gate trading
or risk decisions on their own and they do NOT trigger retrains. Per K1
contract documented at `src/state/learning_loop_observation.py` module
docstring.

The actual operator-gated retrain seam lives at
`src/calibration/retrain_trigger.py` (env-flag-gated + frozen-replay
required); this packet does NOT touch that surface beyond a single
read-only addition (`list_recent_retrain_versions`) added in BATCH 1.

## Per-bucket threshold rationale

Each stall_kind has an independent threshold; defaults differ per
temperature_metric because alpha-decay pressure differs:

| temperature_metric | pair_growth | pairs_ready | drift | rationale |
|--------------------|-------------|-------------|-------|-----------|
| **high**           | **1.3**     | **20 days** | **10 days** | Alpha decays fastest on HIGH metric (HKO/CWA/JMA fast-shifting per `src/calibration/AGENTS.md`); tightest discipline across all 3 stall_kinds. |
| low                | 1.5         | 30 days     | 14 days | Standard — slower-decay LOW metric. |
| legacy (no metric) | 1.5         | 30 days     | 14 days | Legacy `platt_models` rows are HIGH-only by Phase 9C L3 convention; treated as standard. |
| insufficient (sample) | SUPPRESS | SUPPRESS    | SUPPRESS | Buckets with current-window `sample_quality='insufficient'` skip stall detection (mirrors EO/AD/WP/CALIBRATION insufficient_data graceful pattern). |

Operator can override per-bucket per-FIELD via repeated `--override-bucket
KEY=FIELD=VALUE` flags, e.g.:

```
python3 scripts/learning_loop_observation_weekly.py \
    --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=pair_growth=1.1 \
    --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=pairs_ready=15
```

## Severity tier rationale (LOW-DESIGN-LL-2-1 documentation requirement)

Per critic 31st cycle LOW-DESIGN-LL-2-1: the
`pairs_ready_no_retrain` and `drift_no_refit` stall_kinds escalate to
`critical` severity (not `warn`) when `days_since_last_promotion` is
`None` (the bucket has never been promoted at all).

**Reasoning** (operator-readable):

- **never_promoted IS operationally more concerning than stale**.
  - `days_since_last_promotion = N` (stale) means: a model exists in
    `platt_models_v2` (or fallback to legacy), it just hasn't been
    refit recently. The active model still produces calibrated outputs.
  - `days_since_last_promotion = None` (never_promoted) means: NO
    model has ever passed frozen-replay for this bucket. Either (a)
    the bucket only has legacy fallback (and v2 is missing), OR (b)
    every retrain attempt failed frozen-replay and was blocked from
    promotion. Either is a higher-severity operator-attention signal
    than "old model still serving."

- **Operator can override per-bucket** if local context disagrees:
  ```
  --override-bucket <bucket>=pairs_ready=999  # effectively suppress this kind
  ```

- **Sibling-coherent honest precision-favored framing** (per WP/CALIBRATION
  precedent): the detector emits its strongest available signal rather
  than smoothing it; operator interprets via the per_kind evidence dict.

This tradeoff was discussed in critic 31st cycle review and confirmed
KEEP-CURRENT per the dispatch GO_BATCH_3 §CARRY-FORWARD-LOW resolution.
The alternative (default `warn` for never_promoted) was rejected as
under-signalling.

## Known limitations

Per `src/state/learning_loop_observation.py` module docstring §"HONEST
DISCLOSURE" + boot evidence §1 KEY OPEN QUESTION #1:

- **PATH A per-bucket-key snapshot.** Same dispatch's "(city, target_date,
  strategy_key)" framing is the EVALUATION-TIME identity; PERSISTENCE-TIME
  identity is BUCKET-KEYED only (mirror CALIBRATION packet PATH A).
- **PATH B (settlement-event JOIN attribution)** deferred as future
  packet.
- **LEARNING_LOOP_TRIGGERING (would modify retrain_trigger.py
  arm/trigger paths)** explicitly OUT-OF-SCOPE per dispatch — separate
  operator-authorized packet. This packet is MEASUREMENT-ONLY.
- **Cross-module drift integration uses the caller-provided seam**
  (per GO_BATCH_2 §3 + GO_BATCH_3 §CROSS-MODULE ORCHESTRATION). The
  weekly runner is the ONLY place that performs the join with
  CALIBRATION's `detect_parameter_drift`; the `detect_learning_loop_stall`
  detector itself stays pure-Python with caller-provided
  `drift_detected`. This keeps the detector unit-testable in isolation
  AND avoids cross-module DB-read coupling.
- **Apr26 §11 corpus + Phase 4 fixtures (high/low split + DST resolved
  fixtures)** are out-of-scope per dispatch — operator-decision; future
  packet.
- **Cascading-cause masking**: when multiple stall_kinds fire
  simultaneously (e.g. corpus_vs_pair_lag → pairs_ready_no_retrain
  cascade), the verdict reports them all in `stall_kinds` but does NOT
  attempt to identify the root cause among them. Operator interpretation
  via `evidence.per_kind` is the seam (per critic 31st cycle review +
  cycle-29 cite-discipline).

## HONEST DISCLOSURE cross-link

The CALIBRATION_HARDENING packet BATCH 3 boot evidence + AGENTS.md
known-limitations stated "HEAD substrate has no append-only Platt
history table." THAT WAS WRONG. The append-only history exists at
`calibration_params_versions` (`src/calibration/retrain_trigger.py::_ensure_versions_table`
schema). It IS append-only:
  - `version_id` AUTOINCREMENT
  - `promoted_at` + `retired_at` lifecycle columns
  - INSERT on every retrain attempt (PASS → promoted; FAIL →
    COMPLETE_DRIFT_DETECTED, kept for audit)
  - UPDATE only sets `retired_at` on prior live row (never DELETEs)

The CALIBRATION packet's "no append-only history" finding led to a
defensive `insufficient_data` verdict whenever drift detection was
attempted — that defense is honest given the (incomplete) substrate
read at the time, but on the proper substrate (which LEARNING_LOOP BATCH 1
now leverages via `list_recent_retrain_versions`), drift detection becomes
much more meaningful.

This was exactly the failure mode that LOW-CITATION-CALIBRATION-3-1
cycle-29 sustained discipline note warned about: cite was made without
grep-tracing the FULL retrain pipeline. The history was in
`retrain_trigger.py`, one module away from where I was looking. Cycle-29
discipline lesson paid dividends within 24h.

See also `docs/operations/calibration_observation/AGENTS.md` for the
correction note at the source.

## Operator runbook

When a bucket reports `stall_detected`:

1. **Check `stall_kinds`** for which kind(s) fired:
   - **corpus_vs_pair_lag**: settlement→pair pipeline is producing
     fewer pairs than baseline. Investigate `src/execution/harvester.py`
     + `scripts/generate_calibration_pairs.py` for a write-side issue
     (settlement events not arriving? harvester stalled?).
   - **pairs_ready_no_retrain**: canonical pairs are accumulating but
     no retrain has happened in the threshold window. Operator triage
     via `src/calibration/retrain_trigger.py` (operator-gated; needs
     ZEUS_CALIBRATION_RETRAIN_ENABLED env + evidence path).
   - **drift_no_refit**: parameter trajectory drift detected by
     CALIBRATION packet AND no refit has fired. Higher-priority operator
     triage — current model is stale AND drifting.
2. **Check `severity`**:
   - `warn`: monitor; investigate during next operator window.
   - `critical`: operator triage during current cycle; consider operator-
     gated retrain (PACKET DOES NOT TRIGGER RETRAINS).
3. **Cross-check `drift_detected_map[bucket_key]`**:
   - `True`: drift confirmed by CALIBRATION packet.
   - `False`: drift not detected; pairs_ready_no_retrain alone fired.
   - `None`: insufficient drift history; pairs_ready_no_retrain or
     corpus_vs_pair_lag alone fired.
4. **Cross-reference `docs/operations/calibration_observation/weekly_<date>.json`**
   for the same bucket's parameter-trajectory drift evidence.

## How to regenerate

Manual run (BATCH 3 packet does not wire automation):

```
python3 scripts/learning_loop_observation_weekly.py
python3 scripts/learning_loop_observation_weekly.py --end-date 2026-04-28
python3 scripts/learning_loop_observation_weekly.py --window-days 7 --n-windows 6
python3 scripts/learning_loop_observation_weekly.py --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=pair_growth=1.1
python3 scripts/learning_loop_observation_weekly.py --report-out /tmp/ll.json --stdout
python3 scripts/learning_loop_observation_weekly.py --db-path state/zeus-shared.db
```

Exit code: 0 if no bucket is `stall_detected`; 1 if at least one bucket
has `stall_detected` (cron-friendly).

## Retention

Operator-managed evidence. There is no auto-purge; old reports remain
until operator decides to archive or delete. A history of weekly reports
IS itself useful (lets the operator see learning-loop stall trajectory
over many weeks).

## Out-of-scope

- Cron / launchd wiring (operator decides; not in scope of this packet).
- ANY mutation of `platt_models` / `platt_models_v2` / `calibration_pairs[_v2]`
  / `position_events` / `calibration_params_versions` / `venue_trade_facts`
  tables (writer-side change; out-of-scope per dispatch).
- Modifying `src/calibration/{platt,manager,blocked_oos,drift,effective_sample_size}.py`
  (out-of-scope HIGH-RISK surfaces per dispatch).
- Modifying `src/calibration/retrain_trigger.py` writers (arm /
  trigger_retrain / _insert_version / _ensure_versions_table /
  load_confirmed_corpus). Only the BATCH 1 pure-SELECT addition
  `list_recent_retrain_versions` is touched.
- α-fusion weight tuning (deferred to future packet).
- Double-bootstrap CI tightness adjustment (deferred to future packet).
- KL-divergence + L2-norm aggregate detectors (deferred).
- Schema migrations (out of scope).
- LEARNING_LOOP_TRIGGERING (separate operator-authorized packet that
  WOULD modify retrain_trigger.py writers).
- Apr26 §11 corpus + Phase 4 high/low split + DST resolved fixtures
  (operator-decision; future packet).

## See also

- `src/state/learning_loop_observation.py` — the K1-compliant projection
  module (BATCH 1: `compute_learning_loop_state_per_bucket`; BATCH 2:
  `detect_learning_loop_stall` + `ParameterStallVerdict`).
- `tests/test_learning_loop_observation.py` — relationship tests for
  the per-bucket projection + 3-kind composable stall detector.
- `tests/test_learning_loop_observation_weekly.py` — end-to-end runner tests.
- `src/calibration/retrain_trigger.py` — `list_recent_retrain_versions`
  read function added by BATCH 1 (pure SELECT; sibling-coherent with
  CALIBRATION BATCH 1 store.py read additions). HIGH-risk file otherwise
  (operator-gated retrain seam) — not modified beyond the read addition.
- `src/calibration/store.py` — `list_active_platt_models_v2` +
  `list_active_platt_models_legacy` (CALIBRATION BATCH 1 reads, reused).
- `src/calibration/manager.py` L172-189 — model-fallback-load precedent
  this packet's v2-then-legacy dedup mirrors (cycle-29 cite-CONTENT
  discipline).
- `docs/operations/edge_observation/` — sibling packet (1st of 5).
- `docs/operations/attribution_drift/` — sibling packet (2nd of 5).
- `docs/operations/ws_poll_reaction/` — sibling packet (3rd of 5).
- `docs/operations/calibration_observation/` — sibling packet (4th of 5);
  contains the HONEST DISCLOSURE cross-link correcting the prior
  "no append-only history" misread.
- `architecture/source_rationale.yaml` — registry entry for
  `src/state/learning_loop_observation.py`.
- `architecture/script_manifest.yaml` — registry entry for
  `scripts/learning_loop_observation_weekly.py`.
- `architecture/test_topology.yaml` — registry entries for the two test
  files.
