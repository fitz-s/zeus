# LOW/HIGH Calibration Alignment Recovery Plan

Created: 2026-05-07

Authority basis: operator directive to resume LOW/HIGH asymmetry recovery while
deferring the 0.25 raw-grid investigation.

## Scope

Recover LOW/HIGH calibration alignment without changing live trading authority
until the contract-bin object chain is proven.

This packet covers:

1. Persisting contract outcome and explicit forecast-window evidence on
   `ensemble_snapshots_v2`.
2. Replacing boolean LOW boundary rejection with a bin-preserving attribution
   proof path in shadow first.
3. Rebuilding LOW calibration pairs only from snapshots that prove the same
   settlement object.
4. Refitting/gating LOW calibration authority after pair recovery evidence.
5. Blocking fallback/live promotion unless contract-bin authority is explicit.

## Non-Scope

- No 0.25 raw-grid download or validation work.
- No live evaluator promotion in the persistence slice.
- No mutation of production/cloud raw data.
- No relabeling of old 0.5 raw data.
- No relaxation of LOW Law 1 without persisted forecast-window evidence.

## Implementation Slices

### Slice A - Evidence Persistence

Add nullable, shadow-only fields to `ensemble_snapshots_v2` and wire the ingest
snapshot writer to persist:

- settlement source type/station/unit/rounding;
- bin grid/schema version;
- forecast window start/end in UTC and local time;
- overlap hours and attribution status;
- whether the component contributes to the target local-day extrema;
- block reasons.

Acceptance:

- existing HIGH behavior unchanged;
- existing LOW `training_allowed` remains conservative;
- missing window evidence cannot become training/live authority;
- fresh schema exposes the required contract-outcome fields.

### Slice B - Contract Attribution Law

Introduce contract-bin-preserving LOW attribution in shadow mode:

- fully inside target local day -> training candidate;
- previous/next local day -> deterministic reassignment candidate only with
  explicit provenance;
- ambiguous cross-boundary aggregate -> remains blocked;
- issue-after-window -> blocked.

Acceptance:

- no adjacent-day LOW can enter target-day training;
- HIGH never reads `low_temp`;
- LOW never reads `high_temp`.

### Slice C - Pair Rebuild Gate

Update `rebuild_calibration_pairs_v2.py` to require contract/window evidence for
LOW recovery data versions.

Acceptance:

- recovered LOW pairs are traceable to the same settlement/bin object;
- old LOW pairs remain isolated by data version;
- dry-run before/after reports show recovered counts and block reasons.

### Slice C2 - Recovery Snapshot Backfill Dry-Run

Add a guarded repair script that reads extracted LOW JSON evidence and copies
matched legacy LOW snapshots into contract-window recovery data versions.

Acceptance:

- dry-run is default and read-only;
- apply mode requires both `--apply` and `--force`;
- old LOW rows are never mutated in place;
- fully attributable windows may become recovery training candidates;
- ambiguous or adjacent-day windows remain blocked with explicit evidence;
- output cannot authorize live promotion or calibration sharing.

### Slice D - Calibration Authority Gate

Wrap calibration reads in an authority envelope before any live use:

- requested domain;
- served domain;
- route;
- model key;
- n_eff/n_samples;
- bin/settlement/source/cycle/horizon/local-day compatibility;
- live eligibility and block reasons.

Acceptance:

- primary exact routes remain unchanged;
- incompatible fallback is blocked from live;
- quarantined primary cannot silently look like primary calibrated evidence.

## Verification

Focused tests per slice:

- `tests/test_forecast_calibration_domain.py`
- `tests/test_schema_v2_gate_a.py`
- `tests/test_low_high_alignment_report.py`
- `tests/test_snapshot_ingest_contract.py` or equivalent contract test
- `tests/test_rebuild_calibration_pairs_v2*` before pair rebuild changes
- evaluator/fallback tests before live authority wiring

Before any live-eligibility change, run a dry-run report comparing:

- LOW/HIGH training eligibility;
- LOW boundary rejection;
- recovered/blocked LOW counts;
- n_eff/n_samples distribution;
- fallback/quarantine route counts.

## Stop Conditions

Stop and re-plan before:

- production DB mutation;
- raw-data deletion or redownload;
- live evaluator promotion;
- fallback live eligibility changes;
- calibration refit activation;
- cross-source OpenData/TIGGE sharing.
