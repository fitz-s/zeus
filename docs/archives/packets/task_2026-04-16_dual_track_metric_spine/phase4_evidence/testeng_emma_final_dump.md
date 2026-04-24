# testeng-emma Final Discipline Dump — Phase 4

Written at retirement after Phases 1–4 test authorship.

---

## 1. Relationship-Invariant Library (R-A through R-P)

### Type Safety
- **R-A** (MetricIdentity spine): A `MetricIdentity` object must map `temperature_metric` to exactly one `observation_field`. Wrong field on a correct metric must be a `TypeError`, not a wrong number.
- **R-B** (Day0Signal type seam): Constructing `Day0Signal` with a string `temperature_metric` instead of a `MetricIdentity` must raise at the boundary. The type seam must exist before the implementation uses it.
- **R-4 / R-metric-cannot-be-string**: At minimum one choke-point must enforce `MetricIdentity` over bare `str`. The choke-point is the antibody.

### Scope Separation
- **R-C** (FDR canonical identity): `make_family_id(strategy_key="")` and `make_family_id(strategy_key=None)` must produce the same canonical family. Two call sites that differ in `strategy_key` handling silently split a market into two FDR families.
- **R-D / R-4D** (Platt family isolation): High and low Platt models must have distinct `model_key`s and must not share rows. A high refit must not touch any low-track row.
- **R-N** (schema cleanliness): `platt_models_v2` must not have `city` or `target_date` columns — bucket-keyed, not city-keyed.
- **R-G** (single source of station truth): No parallel `CITY_STATIONS` map in `daily_obs_append.py`; `cities.json` is authoritative.

### Commit Ordering
- **R-E / DT#1**: `store_artifact` DB commit must fire before JSON portfolio write. Phase 2 made this structurally impossible via `commit_then_export`; Phase 4 verifies the new path honours it too.

### Quarantine / Versioning
- **R-P** (peak-window quarantine): `tigge_mx2t6_local_peak_window_max_v1` and any `tigge_mx2t6_local_peak_window*` prefix must be rejected by `assert_data_version_allowed`. Version-bumped variants (`_v2`, `_v3`) must also be caught by the prefix guard — not just the exact string.

### Provider Closure
- **R-F** (observation provider): Every live provider that returns a valid observation must include both `high_so_far` and `low_so_far` as non-None floats. Returning `low_so_far=None` is forbidden at the public seam; the provider must raise a typed exception instead.
- **R-H** (evaluator low unblock): A city with a valid `low_so_far` must not trigger `OBSERVATION_UNAVAILABLE_LOW` rejection.

### Calibration Write-Time Contracts
- **R-I** (calibration pair requires MetricIdentity): `add_calibration_pair_v2` must require `metric_identity`; calling without it raises `TypeError`.
- **R-J** (INV-15 source whitelist): Non-whitelisted `data_version` prefix forces `training_allowed=0` regardless of requested value. Gate is the `data_version.startswith()` check, not a separate `source=` argument.
- **R-K** (members_unit column): `ensemble_snapshots_v2` has `members_unit TEXT NOT NULL DEFAULT 'degC'`; explicit NULL must raise `IntegrityError`.
- **R-L** (ingest provenance fields): The GRIB ingest path must write all 7 provenance fields (`physical_quantity`, `members_unit`, `local_day_start_utc`, `local_day_end_utc`, `step_horizon_hours`, `manifest_hash`, `provenance_json`) as non-trivial, non-null values.
- **R-M** (calibration pair identity fields): A written pair must carry correct `temperature_metric`, `observation_field`, `data_version`, `training_allowed`; no NULL in any identity field.
- **R-O** (Kelvin guard): `validate_members_unit("K")` and `validate_members_unit(None)` and `validate_members_unit("")` must raise `MembersUnitInvalidError`. `"degC"` and `"degF"` must pass.

### Numerical Parity
- **MAJOR-3 / TestFitBucketNumericalParity**: `ExtendedPlattCalibrator` with `n_bootstrap=0` is fully deterministic. Two instances fitted on identical inputs must produce `|ΔA| + |ΔB| + |ΔC| < 1e-9`. Any divergence means input-shaping upstream differs between paths.

---

## 2. Test Anti-Patterns to Spot

**Fixture-bypasses-function-under-test (4B MAJOR-1, 4CD MAJOR-2):** A test that directly INSERTs into the table via raw SQL instead of calling the production writer. The writer enforces contracts (FK, UNIQUE, whitelist, metric_identity); the raw INSERT bypasses them. The test passes even when the writer is broken. Rule: if the R-invariant is "function X enforces Y," the test must call X.

**isinstance dual-gating:** A test that does `if isinstance(result, int): assert result == 1` — it passes when `result` is `None` because the branch is skipped. Every contract assertion must be unconditional.

**Trivially-green tests:** A test that passes GREEN before the implementation exists because it only imports a symbol and calls `assert True`. A failing test that turns green only when the implementation ships is the correct antibody. If it was green on day 0, it proved nothing.

**Pre-migration test fragility:** Tests written against `data_version="tigge_mx2t6_local_peak_window_max_v1"` before 4A.1 would oscillate depending on worktree state. Anchor tests to the post-migration canonical tag immediately; use `QUARANTINED_DATA_VERSION_PREFIXES` assertions to confirm the old tag is dead.

---

## 3. R-Draft Heuristics

**Load-bearing vs decorative:** An R-invariant is load-bearing if its failure mode is a *silent wrong answer* — the system runs, produces a number, and that number is wrong. Decorative tests catch crashes; antibody tests catch silent wrongness.

Ask: "If this constraint is violated, does the system crash or does it silently return a plausible-but-wrong value?" If the answer is the latter, the R-invariant is load-bearing and must be written before implementation.

**Antibody vs alert:** An antibody is a test that makes the violation *impossible to ship undetected*. An alert is a monitoring check that discovers the violation after it ships. Tests are antibodies only when they are in CI and block merge. A doc that says "remember to set training_allowed=False" is an alert, not an antibody. `_resolve_training_allowed` enforced in the writer is an antibody.

**The dual-path pattern:** For any enforcement function `f(x) -> bool`, write both `f(bad_x) -> rejected` AND `f(good_x) -> accepted` in the same test class. A test that only checks rejection can pass when `f` always returns `False`. Always test the positive path too.

---

## 4. Phase 5/6/7 R-Drafts Already in Scope

The next testeng should not re-derive these:

- **R-Q (Day0LowNowcast split):** `Day0LowNowcastSignal(observed_low_so_far=X)` must actually use `X` in its probability computation. The current `Day0Signal` with `temperature_metric="low"` silently uses high-semantics (`obs_high`). Phase 6 splits the class; the first test is: `p_vector` without `observed_low_so_far` raises, not silently uses zero.
- **R-R (boundary_ambiguous quarantine):** For `mn2t6` ingest, if any member's minimum falls in the boundary bucket (first or last bin), `training_allowed` must be `False`. Test: synthetic ensemble where member 0 min = boundary value → `training_allowed=0`.
- **R-S (schema v2 live writer cutover):** Once Phase 4 live writer cutover lands, `wu_daily_collector.py` must write to `settlements_v2` with explicit `temperature_metric`. The legacy single-metric assumption at `wu_daily_collector.py:142` must be gone. Test: `write_settlement(city, date, value, temperature_metric="high")` succeeds; calling without `temperature_metric` raises.
- **R-T (Kelly distributional price):** `kelly_size(p_posterior, entry_price=X)` where `X` is a bare float must raise `TypeError` once Phase 9 wraps entry price in a typed distributional object. Today `entry_price: float` — the R-invariant exists when the type changes.
- **R-U (CHAIN_UNKNOWN three-state):** After DT#4 fix, `classify_chain_state()` must return `CHAIN_UNKNOWN` (not `CHAIN_EMPTY` or `CHAIN_POPULATED`) when the reconciliation timestamp is stale (> 6h). Test: mock `_now()` to be 7h after last reconcile → `state == CHAIN_UNKNOWN`.

---

## 5. Critic–Test Coordination Gotchas

**Grep before flagging missing:** Before sending a MAJOR about a missing test, grep the actual test file on disk. Context-window hallucinations about what was written are common after compaction. critic-alice's MODERATE-7 (false dispatch) was caught because the test existed at line 94.

**Avoid fixture-bypass in R drafts:** When drafting an R-invariant for the critic's sketch, always specify *which production function* the test must call, not just which table it must assert on. If the sketch says "insert a row and assert column X," the executor will INSERT directly and bypass the writer. The R-draft must say "call `add_calibration_pair_v2(...)` and assert column X."

**Dual-path R-invariant pattern:** Every whitelist/guard R-invariant needs both a rejection test (bad input → fails) and an acceptance test (good input → passes). Critic sketches often only sketch the rejection path; the testeng must add the acceptance path or the test suite cannot detect an always-False bug.

**n_bootstrap=0 over seeded RNG for parity tests:** When the critic asks for a numerical parity test on a fit function, use `n_bootstrap=0` (deterministic point fit) rather than seeding two RNGs identically. The point fit is reproducible across machines and Python versions; seeded bootstrap is not guaranteed to be. Tighten tolerance to 1e-9, not 1e-5.
