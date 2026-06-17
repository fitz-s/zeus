# Stage 1 Foundation Report — q-kernel rebuild data contracts

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md (Stage 1 + "Forecast center μ*"),
docs/rebuild/q_engine_violation_ledger.md (Layer 0, V3/V4/V19),
docs/rebuild/forecast_center_diagnosis.md. Wired to the live
src/contracts/settlement_semantics.py.

Constraint honored: NEW modules only. No existing live file was modified
(git status shows only untracked new files). No commit by this agent except the
worktree self-merge step. Not wired into the reactor.

## Files created

- `src/forecast/types.py` — `ForecastCase`, `RawModelMember`, `FreshModelSet`
  (frozen dataclasses).
- `src/probability/__init__.py` — package marker (siblings all use `__init__.py`;
  added for consistency).
- `src/probability/event_resolution.py` — `EventResolution` (frozen dataclass),
  `event_resolution_for_city(city, target_date, metric)`, `ResolutionError`,
  `SEMANTICS_VERSION="settlement_semantics_v1"`. Wires to
  `SettlementSemantics.for_city`.
- `src/probability/outcome_space.py` — `OutcomeBin`, `OutcomeSpace` + `validate()`,
  `OutcomeSpaceError`, `compute_topology_hash`. Reuses the live
  `src/types/market.py::validate_bin_topology` + `Bin` for the MECE check.
- `tests/probability/__init__.py`, `tests/probability/test_outcome_space_contract.py`,
  `tests/probability/test_settlement_preimage_threading.py`.

## Exact dataclass fields used (verbatim from spec)

`ForecastCase`: city, city_id, station_id, settlement_source_type,
target_local_date, metric, issue_time_utc, lead_hours, season, regime_key, unit,
resolution (EventResolution), family_id, source_cycle_time_utc.

`RawModelMember`: model_id, product_id, source_run_id, source_cycle_time_utc,
available_at_utc, value_native, station_mapping_id, raw_forecast_artifact_id,
data_version.

`FreshModelSet`: case, members (tuple[RawModelMember,...]),
member_values_native (np.ndarray), min_native, max_native, model_set_hash.

`EventResolution`: city, station_id, settlement_source_type, resolution_source,
target_local_date, settlement_timezone, metric, measurement_unit,
settlement_step_native, precision, rounding_rule
(Literal[wmo_half_up,oracle_truncate,floor,ceil]), finalization_local_time
(datetime.time), semantics_version.

`OutcomeBin`: bin_id, condition_id, label, lower_native, upper_native,
yes_token_id, no_token_id, executable, rounding_rule.

`OutcomeSpace`: family_id, resolution (EventResolution), bins
(tuple[OutcomeBin,...]), topology_hash. `validate()` enforces ≥2 bins, every bin's
rounding_rule == resolution.rounding_rule, and a complete non-overlapping integer
partition (MECE); fails closed via `OutcomeSpaceError`.

## Tests written + pass output

- `tests/probability/test_outcome_space_contract.py::test_incomplete_family_fails_closed_and_complete_family_sums_mass`
  — complete °C partition validates AND its settlement-preimage mass sums to 1.0
  (±1e-9); gap / single-bin / rounding-rule-mismatch families each raise
  `OutcomeSpaceError`.
- `tests/probability/test_settlement_preimage_threading.py::test_hk_oracle_truncate_reaches_emos_and_band_builders`
  — HK resolves to `oracle_truncate` (station `HKO_HQ`), Tokyo/CWA to `wmo_half_up`,
  all sourced from `settlement_semantics` (not a default); the carried rule fed
  into `settlement_preimage_offsets` yields the asymmetric HK preimage `(0.0,1.0)`
  vs symmetric WMO `(-0.5,0.5)`.
- Plus `test_event_resolution_fails_closed_on_missing_station`.

Run command (worktree has no local `.venv`; used the main-tree interpreter on the
worktree `PYTHONPATH` — see ambiguity #5):

```
PYTHONPATH=<worktree> /Users/leofitz/zeus/.venv/bin/python -m pytest -q \
  tests/probability/test_outcome_space_contract.py \
  tests/probability/test_settlement_preimage_threading.py \
  --rootdir=<worktree>
```

Output: `3 passed in 0.88s` (full files); `2 passed in 0.82s` (the two exact
spec-named test IDs).

## Spec-vs-real-code ambiguities resolved

1. **`EventResolution.station_id` — no `station_id` on `SettlementSemantics`.**
   Real `SettlementSemantics` exposes `resolution_source`, not `station_id`.
   Resolved per the spec's own `event_resolution_for_city` pseudocode: WU cities →
   `city.wu_station` (ICAO); non-WU (HKO/CWA/NOAA) → `sem.resolution_source`
   (e.g. HKO → `"HKO_HQ"`). Fail-closed when empty or the literal `"None"` (the
   V19 defect). Verified: Tokyo→`RJTT`, Hong Kong→`HKO_HQ`.

2. **`finalization_local_time: time` vs real `finalization_time: str` (`"12:00:00Z"`).**
   Parsed the `SettlementSemantics.finalization_time` string into a
   `datetime.time` (strip trailing `Z`, parse `HH:MM[:SS]`), raising
   `ResolutionError` on a malformed value. (The diagnosis flags the hardcoded
   `12:00:00Z` as a V19 issue, but fixing the finalization-time source is out of
   Stage-1 scope; I faithfully thread the existing value as a `time`.)

3. **`settlement_step_native` / `precision` — no `settlement_step_native` field
   on `SettlementSemantics`.** `precision` is taken from `sem.precision` (1.0 for
   every live constructor); `settlement_step_native` set to the 1.0 integer
   settlement grid constant (`DEFAULT_SETTLEMENT_STEP_NATIVE`) — all current Zeus
   markets settle on a 1-degree grid (°C point bins, °F 2-degree range bins both
   resolve to integer values).

4. **`OutcomeBin` is a NEW richer type, not the live `Bin`.** The live
   `src/types/market.py::Bin` has only `low/high/unit/label` and no token/condition
   ids. The spec's `OutcomeBin` (with `bin_id/condition_id/yes_token_id/...`) is a
   new contract. For the MECE partition check I map each `OutcomeBin` →
   `Bin(low=lower_native, high=upper_native, unit=resolution.measurement_unit,
   label=...)` and reuse the live `validate_bin_topology` (the canonical
   complete-non-overlapping-integer-partition validator: leftmost open-low,
   rightmost open-high, interior edges `prev.high+1 == next.low`). This reuses the
   live MECE authority rather than reinventing it. NOTE: the live `Bin` enforces
   °F non-shoulder bins span exactly 2 settled degrees and °C exactly 1 — so an
   `OutcomeSpace` built with the wrong bin width for its unit also fails closed
   (an extra, free invariant inherited from the live `Bin`).

5. **No `.venv` in the worktree.** The worktree carries no local `.venv`; the
   interpreter is the main-tree `/Users/leofitz/zeus/.venv/bin/python`. Confirmed
   it resolves the worktree's `src`/`tests` when run with
   `PYTHONPATH=<worktree>` + `--rootdir=<worktree>` (so the worktree's
   `pytest.ini` + `conftest.py` are used). The shared interpreter is read-only
   here (no package installs); the live daemon is unaffected.

No silent guesses: every divergence above is driven by matching the real
`SettlementSemantics` / `Bin` / `City` shapes.
