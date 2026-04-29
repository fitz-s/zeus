# docs/operations/calibration_observation/

Created: 2026-04-29
Authority basis: round3_verdict.md §1 #2 (FOURTH edge packet) + ULTIMATE_PLAN.md §4 #2
(Extended Platt parameter monitoring) + CALIBRATION_HARDENING packet boot §1
+ critic 28th cycle GO_BATCH_3 dispatch (per-bucket threshold + bootstrap
usable_count + sys.path bootstrap pre-applied).

## What lives here

JSON reports emitted by `scripts/calibration_observation_weekly.py` — one
file per weekly Platt-parameter-drift run, named `weekly_<YYYY-MM-DD>.json`
where the date is the inclusive window-end day.

Each report carries:

- `current_window`: per-bucket-key Platt parameter snapshot from
  `src.state.calibration_observation.compute_platt_parameter_snapshot_per_bucket` —
  `param_A`, `param_B`, `param_C`, `n_samples`, `brier_insample`,
  `fitted_at`, `input_space`, `sample_quality`, `source` (`v2` | `legacy`),
  `in_window`, `bootstrap_count`, `bootstrap_usable_count`,
  `bootstrap_A_std` / `bootstrap_B_std` / `bootstrap_C_std`,
  `bootstrap_A_p5` / `bootstrap_A_p95` (and B, C similarly),
  `temperature_metric` (v2-only), `cluster` (v2-only), `season` (v2-only),
  `data_version` (v2-only), `window_start`, `window_end`.
- `drift_verdicts`: per-bucket `ParameterDriftVerdict` from
  `src.state.calibration_observation.detect_parameter_drift` over the
  trailing `n_windows_for_drift` history — `kind` ∈ {`drift_detected`,
  `within_normal`, `insufficient_data`}, optional `severity` ∈ {`warn`,
  `critical`}, plus per-coefficient `evidence` (param_A / param_B /
  param_C ratios + trailing means + drifting_coefficients list).
- `per_bucket_thresholds`: drift-multiplier threshold dict actually used
  this run (defaults + any operator overrides applied — see below).
- Generation metadata: `report_kind`, `report_version`, `generated_at`,
  `end_date`, `window_days`, `n_windows_for_drift`, `critical_ratio_cutoff`,
  `db_path`.

## Authority class

**Derived context — NOT authority.** These reports are evidence the
operator can use to spot Platt-parameter-trajectory drift; they do NOT
gate trading or risk decisions on their own and they do NOT trigger
retrains. Per K1 contract documented at
`src/state/calibration_observation.py` module docstring.

The actual operator-gated calibration retrain seam lives at
`src/calibration/retrain_trigger.py` (env-flag-gated + frozen-replay
required); this packet does NOT touch that surface.

## Per-bucket threshold rationale

`drift_threshold_multiplier` is the ratio at which any of (A, B, C)
coefficient movement (`|current - trailing_mean| / trailing_std`) is
flagged `drift_detected`. Strict greater-than semantics: `ratio ==
multiplier` is `within_normal`. Defaults differ per temperature_metric
because alpha-decay pressure differs:

| temperature_metric | default | rationale                                                                                                |
|--------------------|---------|----------------------------------------------------------------------------------------------------------|
| **high**           | **1.3** | Alpha decays fastest on HIGH metric (HKO/CWA/JMA fast-shifting per `src/calibration/AGENTS.md`); tightest gap discipline. |
| low                | 1.5     | Standard — slower-decay LOW metric.                                                                      |
| legacy (no metric) | 1.5     | Legacy `platt_models` rows are HIGH-only by Phase 9C L3 convention; treated as standard.                 |
| insufficient (n<30)| SUPPRESS | Buckets with current-window `sample_quality='insufficient'` skip drift detection (mirrors EO/AD insufficient_data graceful pattern). |

Operator can override per-bucket via repeated `--override-bucket
KEY=VALUE` flags, e.g.:

```
python3 scripts/calibration_observation_weekly.py \
    --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=1.1 \
    --override-bucket low:Tokyo:JJA:ecmwf_ens_v2:width_normalized_density=1.4
```

Severity tier: `critical` when `max(valid_ratios) >= --critical-ratio-cutoff`
(default 2.0); `warn` otherwise.

## Known limitations

Per `src/state/calibration_observation.py` module docstring §"KNOWN LIMITATIONS":

- **PATH A per-bucket-key snapshot.** The dispatch's "(city, target_date,
  strategy_key)" framing is the EVALUATION-TIME identity; PERSISTENCE-TIME
  identity is BUCKET-KEYED only:
  - `platt_models` (legacy): UNIQUE on `bucket_key TEXT` (= `f"{cluster}_{season}"`)
  - `platt_models_v2`: UNIQUE on `(temperature_metric, cluster, season,
    data_version, input_space, is_active)`
  - Neither table carries `strategy_key`, `city` as a separate column,
    or `target_date`. Platt's `lead_days` is an INPUT FEATURE, not a key.
- **PATH B (decision-log JOIN attribution via
  `trade_decisions.calibration_model_version`)** is deferred as a future
  packet that would provide synthetic strategy attribution at measurement
  time.
- **PATH C (writer-side strategy_key column on platt tables)** is
  explicitly OUT-OF-SCOPE per dispatch §NOT-IN-SCOPE on calibration table
  mutations.
- **drift.py vs detect_parameter_drift.** `src/calibration/drift.py`
  implements the Hosmer-Lemeshow chi-squared test on (forecast, outcome)
  pairs — that measures FORECAST-CALIBRATION drift (output drift). This
  module's `detect_parameter_drift` measures PARAMETER-TRAJECTORY drift
  over consecutive refits. They are parametrically different signals;
  both valuable; neither subsumes the other. drift.py is NOT modified by
  this packet.
- **`bootstrap_count` vs `bootstrap_usable_count`.** Per LOW-NUANCE-
  CALIBRATION-1-2 fix (critic 27th cycle), `bootstrap_count` is the raw
  row count of the persisted `bootstrap_params_json`;
  `bootstrap_usable_count` is the count that passed the type-guard at
  read time. Non-iterable rows (a malformed JSON row that round-trips
  as a scalar, e.g.) are silently skipped from per-coefficient
  aggregation; the count gap is surfaced so operators can investigate.
- **HEAD substrate has no append-only Platt history table.** Each
  historical-window snapshot returns the CURRENTLY-active fit (because
  the `platt_models_v2` UNIQUE constraint is on `(..., is_active=1)` —
  prior fits are deactivated, not preserved). Re-running
  `compute_platt_parameter_snapshot_per_bucket` across N trailing windows
  yields the same active row N times → trailing_std=0 →
  `insufficient_data` (defense-in-depth, not false drift). Genuine
  parameter trajectory tracking would require an append-only history
  table — out of scope; potential future packet.

## Operator runbook

When a bucket reports `drift_detected`:

1. **Read `evidence` carefully.** It carries per-coefficient ratios
   (`param_A.ratio`, `param_B.ratio`, `param_C.ratio`) + the
   `drifting_coefficients` list. WHICH coefficient drifted matters for
   diagnosis:
   - param_A drifted → logit slope changed (re-calibration needed?)
   - param_B drifted → lead_days slope changed (forecast-aging behavior shifted?)
   - param_C drifted → intercept changed (systematic bias appearing?)
2. **Check `severity`.**
   - `warn` (1.5x ≤ ratio < 2.0x): monitor; investigate adjacent buckets.
   - `critical` (ratio ≥ 2.0x): operator triage; consider retrain via
     `src/calibration/retrain_trigger.py` (operator-gated; frozen-replay
     required; THIS PACKET does NOT trigger retrains automatically).
3. **Verify `bootstrap_usable_count` ≈ `bootstrap_count`.** If they
   differ significantly, the bootstrap_params persistence may be
   malformed (investigate the source data before acting on the drift).
4. **Cross-reference `src/calibration/drift.py` (HL χ² test)** for
   forecast-vs-outcome drift on the same bucket. Parameter-trajectory
   drift + HL drift firing together is a much stronger signal than
   either alone.

## How to regenerate

Manual run (BATCH 3 packet does not wire automation):

```
python3 scripts/calibration_observation_weekly.py
python3 scripts/calibration_observation_weekly.py --end-date 2026-04-28
python3 scripts/calibration_observation_weekly.py --window-days 7 --n-windows 6
python3 scripts/calibration_observation_weekly.py --critical-ratio-cutoff 2.5
python3 scripts/calibration_observation_weekly.py --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=1.1
python3 scripts/calibration_observation_weekly.py --report-out /tmp/cal.json --stdout
python3 scripts/calibration_observation_weekly.py --db-path state/zeus-shared.db
```

Exit code: 0 if no bucket is `drift_detected`; 1 if at least one bucket
has `drift_detected` (cron-friendly).

## Retention

Operator-managed evidence. There is no auto-purge; old reports remain
until operator decides to archive or delete. A history of weekly reports
IS itself useful (lets the operator see parameter-stability trajectory
over many weeks).

## Out-of-scope

- Cron / launchd wiring (operator decides; not in scope of this packet).
- ANY mutation of `platt_models` / `platt_models_v2` / `calibration_pairs[_v2]`
  tables (writer-side change; out-of-scope per dispatch).
- Modifying `src/calibration/{platt,manager,retrain_trigger,blocked_oos,drift}.py`
  (out-of-scope HIGH-RISK surfaces per dispatch).
- α-fusion weight tuning (deferred to future packet).
- Double-bootstrap CI tightness adjustment (deferred to future packet).
- Schema migrations (out of scope).
- LEARNING_LOOP integration (separate packet — Week 21+).
- Append-only Platt history table for genuine multi-fit trajectory
  reconstruction (deferred; potential future PATH-D packet).

## CORRECTION (LEARNING_LOOP cycle-31 cross-link)

**The "Append-only Platt history table for genuine multi-fit trajectory
reconstruction (deferred; potential future PATH-D packet)" item above is
WRONG.** The append-only history exists at `calibration_params_versions`
(`src/calibration/retrain_trigger.py:242-264` schema) — autoincrement
`version_id`, `promoted_at` + `retired_at` lifecycle columns, INSERT on
every retrain attempt (PASS → promoted; FAIL → COMPLETE_DRIFT_DETECTED,
kept for audit), UPDATE only sets `retired_at` (never DELETE).

The CALIBRATION_HARDENING BATCH 3 boot evidence + this AGENTS.md misread
the substrate by claiming "no append-only Platt history table." That
claim was based on `platt_models_v2 UNIQUE (..., is_active=1)` reasoning
WITHOUT grep-tracing the FULL retrain pipeline.

LEARNING_LOOP packet BATCH 1 (commit 1014ff2) caught this misread during
boot evidence reading and added the
`src.calibration.retrain_trigger.list_recent_retrain_versions` pure-SELECT
reader in BATCH 1 to leverage the actual append-only history. See
`docs/operations/learning_loop_observation/AGENTS.md` §"HONEST DISCLOSURE
cross-link" for the full disclosure note.

This correction is a textbook dividend of the LOW-CITATION-CALIBRATION-3-1
cycle-29 sustained discipline lesson: **grep-verify CONTENT not just line
ranges**. The cite to `platt_models_v2 UNIQUE on is_active=1` was correct
at the line level but incomplete at the system level.

## See also

- `src/state/calibration_observation.py` — the K1-compliant projection
  module (BATCH 1: `compute_platt_parameter_snapshot_per_bucket`;
  BATCH 2: `detect_parameter_drift` + `ParameterDriftVerdict`).
- `tests/test_calibration_observation.py` — relationship tests for the
  per-bucket projection + ratio-test detector.
- `tests/test_calibration_observation_weekly.py` — end-to-end runner tests.
- `src/calibration/store.py` — `list_active_platt_models_v2` +
  `list_active_platt_models_legacy` read functions added by BATCH 1
  (pure SELECT; mirrors load_platt_model[_v2] read filter).
- `src/calibration/manager.py` L172-189 — model-fallback-load precedent
  this packet's v2-then-legacy dedup mirrors.
- `src/calibration/drift.py` — existing forecast-calibration drift
  detector (Hosmer-Lemeshow chi-squared test); parametrically different
  from this packet's parameter-trajectory drift.
- `docs/operations/edge_observation/` — sibling packet (same operator
  pattern: weekly JSON + per-strategy summary + cron-friendly exit).
- `docs/operations/attribution_drift/` — sibling packet.
- `docs/operations/ws_poll_reaction/` — sibling packet (per-strategy
  threshold + override flag + sys.path bootstrap fix carry-forward
  precedent for this packet's per-bucket threshold + override + sys.path).
- `architecture/source_rationale.yaml` — registry entry for
  `src/state/calibration_observation.py`.
- `architecture/script_manifest.yaml` — registry entry for
  `scripts/calibration_observation_weekly.py`.
- `architecture/test_topology.yaml` — registry entries for the two test
  files.
