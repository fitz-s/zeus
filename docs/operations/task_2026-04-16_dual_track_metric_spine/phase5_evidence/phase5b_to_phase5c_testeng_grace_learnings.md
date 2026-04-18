# Phase 5B → 5C: testeng-grace learnings

**Written**: 2026-04-17, pre-retirement
**Author**: testeng-grace (Test Engineer, zeus-dual-track team)
**Scope**: 30+ R-letter tests across Phase 4.5 + 5A + 5B (R-Q..R-AO)

---

## 1. Latent test-coverage issues — things seen but not flagged

### R-AG: importability-only is a polarity-swap footgun

`tests/test_phase5b_low_historical_lane.py::TestExtractorModuleExports` asserts that
`classify_boundary_low`, `extract_city_vectors_low`, etc. can be imported. That is the
weakest possible test. `classify_boundary_low` has a polarity convention
(`boundary_ambiguous=True` means the snapshot is unreliable) that is easy to invert.
No test currently exercises:
- `classify_boundary_low` with `inner_min > boundary_min` → `boundary_ambiguous=True`
- `classify_boundary_low` with inner-only members → `boundary_ambiguous=False`
- Tokyo UTC+9 Day0 causality vs Los Angeles UTC-8 Day0 causality
- West-coast Day7 step horizon ≥ 204

Team-lead reserved R-AP for exactly this gap. It should be the first dispatch for the
fresh 5C testeng. Until R-AP lands, every extractor caller is trusting a function whose
polarity is checked by no machine.

### `test_phase6_causality_status.py` — fails with SystemExit at import, not at test logic

`tests/test_phase6_causality_status.py` imports `Day0ObservationContext` from
`src/data/observation_client.py`, which raises `SystemExit` at module level if
`WU_API_KEY` is absent (line 87). All 3 tests fail with `SystemExit`, not with
`ImportError` or assertion failure. The tests were presumably written against a version
of `observation_client.py` that didn't have the module-level guard, or intended to run
only in CI with the key set. They are currently "never work in dev" tests — they give
false confidence that causality invariants have coverage when in fact they cannot run.

Fix: move the `WU_API_KEY` guard inside the class `__init__` or an explicit `.connect()`
method, not at module import time. Or isolate `Day0ObservationContext` into its own
module without the key dependency.

### `src/data/observation_client.py` module-level guard poisons all downstream importers

The `raise SystemExit` at line 87 is a test-topology land mine. Any future test that
imports anything transitively depending on `observation_client` will fail with
`SystemExit` instead of a meaningful error. Tracked as: `src/data/observation_client.py:87`.

### Older phase test files lack provenance headers (mandatory per CLAUDE.md)

`tests/test_phase2_idempotency.py`, `tests/test_phase3_observation_closure.py`,
`tests/test_phase4_foundation.py`, `tests/test_phase4_ingest.py`,
`tests/test_phase4_parity_gate.py`, `tests/test_phase4_rebuild.py`,
`tests/test_phase4_platt_v2.py` — none carry the mandatory
`# Lifecycle: / # Purpose: / # Reuse:` header block. CLAUDE.md calls these
"legacy-until-audited." A fresh testeng cannot tell whether these tests are CURRENT_REUSABLE
or STALE_REWRITE without running a full audit.

### `test_cross_module_invariants.py` tests hit live DB state

`tests/test_cross_module_invariants.py::test_calibration_pairs_use_same_bias_correction_as_live`
and siblings reach into the live Zeus DB. Under the Zero-Data Golden Window, v2 tables
have zero rows, so these tests pass trivially (vacuously true assertions on empty sets).
They will produce different results post-ingest. They are not antibodies — they are
state-dependent sensors that report nothing during the Golden Window.

### No INV coverage test for INV-21 (Kelly executable-price) or INV-22 (FDR family)

The architecture doc (`docs/authority/zeus_dual_track_architecture.md`) defines INV-21
and INV-22 as machine-checkable invariants landed in Phase 0b.
`tests/test_architecture_contracts.py` is empty (zero grep hits for any INV-## test).
`tests/test_cross_module_invariants.py` has 4 tests, none targeting INV-21/22.
These invariants have no executable coverage; they exist only as documentation.

---

## 2. Cross-phase patterns in test design

**What worked**: the three-tier shape proved reliable.

1. **Contract gate tests** (R-AF, R-AH, R-AJ): assert that the public entry point
   enforces the law before any domain logic runs. These tests fail fast, run in <10ms,
   and anchor executor scope precisely. Fastest RED-to-GREEN cycle observed.

2. **Structural antibodies** (R-AK, R-AL): assert that the data structure covers both
   tracks. These tests are also fast and rarely require implementation iteration — the
   executor either adds the structure or not.

3. **Behavioral traces** (R-AN, R-AO): assert that a side-effecting function threads
   the right identity through to downstream calls. These require mock/spy patterns and
   are slower to get GREEN because executors must trace the full call chain.

**What slowed iteration**: disjunctive assertions (`accepted is False OR training_allowed
is False` in R-AH/R-AJ). Both are semantically correct (either outcome prevents the
forbidden move) but the dual-OR form requires the executor to understand why both are
acceptable rather than pinning to one. Critic-alice pre-review on these was essential to
confirm the pattern before executors started — without it, executors would likely have
chosen one arm and broken the other.

**Over-constraint risk**: R-AF originally asserted `decision.training_allowed is True`
for the high-track boundary test. This over-constrained the design space — it pinned an
implementation detail that the spec didn't mandate. Caught by critic-alice pre-review
(widen note 1) and corrected before executor implemented. The lesson: for acceptance
tests, assert the minimum required property, not the full expected state.

---

## 3. Forward hazards for 5C / Phase 6 / Phase 7

**5C — B093 replay typed status fields**: the challenge is that `StatusField` values
come from live runtime decisions. Testing them without fabricating reality requires
building synthetic `ReplayEvent` fixtures that carry known `typed_status` payloads.
The test must verify that the replay consumer reads `typed_status`, not `raw_status`.
Pattern: construct a minimal `ReplayEvent` dict with `typed_status` set to a known
`MetricIdentity`-bearing object; assert the consumer's output reflects the typed field
without running a live replay. Do not mock `ReplayEvent` — call the real constructor
with synthetic args.

**Phase 6 — Day0LowNowcastSignal**: the "refuse historical Platt path" behavior requires
calling `refit_v2` or an equivalent signal builder with `metric_identity=LOW_LOCALDAY_MIN`
and `mode=nowcast`. Without a running runtime, the test must construct a minimal
in-memory SQLite DB with zero calibration_pairs_v2 rows and assert that the signal
builder returns `signal=None` (or raises `InsufficientDataError`) rather than silently
falling back to the high-track Platt family. The test must NOT mock the DB — the
Zero-Data Golden Window is the test condition, not an obstacle.

**Phase 7 — metric-aware rebuild cutover**: full parity requires running `rebuild_v2`
for both `HIGH_LOCALDAY_MAX` and `LOW_LOCALDAY_MIN` specs against the same synthetic
snapshot set and asserting that the two output calibration families are disjoint
(`temperature_metric` in every row matches the spec used to generate it). A parametrized
pytest fixture (`@pytest.mark.parametrize("spec", METRIC_SPECS)`) would express this
cleanly and prevent future specs from being omitted.

---

## 4. Fresh testeng inheritance

**R-letter namespace ledger** (locked entries):
- R-A..R-P: Phases 1-4
- R-Q..R-U: Phase 4.5
- R-AA: Phase 4.6 (cities cross-validate)
- R-AB..R-AE: Phase 5A
- R-AF..R-AO: Phase 5B
- R-AP: reserved (extractor behavioral — 3 `classify_boundary_low` cases)

**Canonical test patterns** (non-negotiable):
1. No fixture bypass: call the real public entry point. Mocks are only for side-effecting
   functions (DB writes, model saves). The function under test must be the real one.
2. Provenance header on every new test file: `# Lifecycle: / # Purpose: / # Reuse:`.
3. Disjunctive assertions for "either outcome prevents the forbidden move" — but require
   critic pre-review before executors implement.
4. Acceptance tests: assert minimum required property only. Never pin implementation details.
5. Failing tests first. Run `pytest` on the new test before notifying executors — confirm RED.

**What fresh testeng must re-derive**: the specific `CalibrationMetricSpec` constructor
signature and `METRIC_SPECS` 2-tuple layout (read `scripts/rebuild_calibration_pairs_v2.py`
fresh — it may have evolved). The `validate_snapshot_contract` 3-law mapping
(read `src/contracts/snapshot_ingest_contract.py` fresh).

**What fresh testeng can inherit directly**: the R-letter counting convention (class per
R-letter, test per behavior), the `_write_manifest` / `_city_entry` / `_zeus_city`
helper pattern from `test_phase4_6_cities_drift.py`, and the spy mock pattern for
`save_platt_model_v2` / `deactivate_model_v2` from `TestRefitPlattV2LowMetricIsolation`.

---

## 5. Tooling observations

**`observation_client.py` module-level guard is the #1 pytest topology hazard**. Until
it's moved inside a method, any test file that imports from `src/data/` transitively
risks `SystemExit` instead of a test failure. When this fires, pytest reports
`INTERNALERROR` and `no tests ran` — it looks like a pytest bug, not a code bug.
Always run the phase-specific test file in isolation first (`pytest tests/test_phaseN_*.py`)
before running the full suite.

**Full-suite run requires `--ignore` or env var**: `WU_API_KEY` must be set or
`test_phase6_causality_status.py` and `test_automation_analysis.py` must be excluded.
The standard dev invocation should be documented in AGENTS.md or `pytest.ini` (currently
neither has this guard).

**`topology_doctor.py` navigation mode** is useful for finding the authoritative module
for a given R-letter target, but it can lag behind new modules (e.g., it did not index
`src/contracts/snapshot_ingest_contract.py` immediately after exec-emma created it).
Always verify with `ls src/contracts/` after a new module creation rather than trusting
the topology index.

**`test_topology.yaml`**: not confirmed to exist in this repo. If it does not exist, the
fresh testeng should not create it — the R-letter doc in the handoff file is the de facto
test registry.
