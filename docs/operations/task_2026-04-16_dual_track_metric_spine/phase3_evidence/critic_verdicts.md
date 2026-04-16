# Phase 3 — critic-alice verdict

Date: 2026-04-16
Scope reviewed: exec-bob diff (`src/data/observation_client.py`, `src/engine/evaluator.py`, `src/engine/monitor_refresh.py`) + exec-carol diff (`src/data/daily_obs_append.py`, `tests/test_k2_live_ingestion_relationships.py`) + testeng-emma diff (`tests/test_phase3_observation_closure.py`, `tests/test_phase6_causality_status.py`).

## Final verdict (post-iterate): **PASS**

## Round 1 verdict: ITERATE (historical — preserved below)

Phase 3's narrow R-F / R-G / R-H goals landed. But the wide pass surfaces one CRITICAL (silent-wrong at runtime seam) and one MAJOR (unmarked phase-leak in the test suite that blocks CI green). Both are cheap fixes; re-verify after.

## Narrow check list

| Check | Result | Evidence |
|---|---|---|
| V1 (R-F provider closure, no silent None) | PASS | `observation_client.py:51` `__post_init__` rejects `low_so_far=None`; WU `:262`, IEM `:319-327` (fail-closed return None), Open-Meteo `:397` all compute low from same local-day sample set. |
| V2 (R-G CITY_STATIONS gone) | PASS in-scope | `daily_obs_append.py` has zero `CITY_STATIONS` (grep clean in `src/`). Residual in `scripts/backfill_wu_daily_all.py:141` + `scripts/oracle_snapshot_listener.py:59` are scoped out per brief — see MODERATE-1 below for forward risk. |
| V3 (R-H evaluator low-reject gated) | PASS | `evaluator.py:800-809` still gates on `low_so_far is None`; `rejection_stage="OBSERVATION_UNAVAILABLE_LOW"` (line 804). When the new provider returns a context, `__post_init__` blocks None; no gate fires. |
| V4 (backward-compat shim deprecation) | PASS structurally, but see CRITICAL-1 | `observation_client.py:55-88` — `as_dict()`, `get()`, `__getitem__` all emit `DeprecationWarning`. Shim is correctly wired. |
| V5 (Phase 0/1/2 regressions green) | PASS | `tests/test_phase3_source_registry_single_truth.py` + `test_metric_identity_spine.py` + `test_schema_v2_gate_a.py` + `test_k2_live_ingestion_relationships.py`: 51 passed, 7 subtests passed. No Phase 0b stub regression. |
| V6 (INV-13 `require_provenance("kelly_mult")`) | PASS | `src/strategy/kelly.py:74` verbatim. |

## Wide pass (W1-W6)

### CRITICAL-1 — Evaluator + monitor_refresh call dict-style on Day0ObservationContext every Day0 evaluation
**File:line:**
- `src/engine/evaluator.py:800, 812, 814, 815, 818, 828, 829` — 7 dict-style accesses on `candidate.observation`.
- `src/engine/monitor_refresh.py:258, 278, 279, 318, 319` — 5 dict-style accesses on `obs`.

**What's wrong:** `get_current_observation` now returns `Day0ObservationContext` (exec-bob's new typed dataclass). Every `candidate.observation.get(...)` and `candidate.observation["key"]` lookup in the evaluator and monitor_refresh goes through the compat shim, firing `DeprecationWarning` at **every Day0 evaluation in production**. Worse: `Candidate.observation: Optional[dict]` on `evaluator.py:89` is still type-annotated `dict` — so mypy/IDE affordances and downstream readers are actively misled about the seam.

**Why CRITICAL (not MAJOR):**
1. Silent-wrong risk at the evaluation seam. DeprecationWarning in trading-hot code is noise that will train operators/agents to mute the category, which later lets a real deprecation through.
2. NC-08 adjacent: the compat shim was advertised as *deprecation*. For it to deprecate, the planned follow-up migration must be named and scheduled. If the shim is the permanent live path, it is not a deprecation — it is a silent contract inversion (the method names lie).
3. Stricter-than-prose? No — this is **looser than authority prose**: §8 dual-track arch lists `Day0ObservationContext` as a typed-boundary object; evaluator is treating it as a dict.

**Fix:** Either
- (a) In this phase, migrate `evaluator.py:800-829` + `monitor_refresh.py:254-319` to attribute access (`candidate.observation.low_so_far` etc.) and retype `Candidate.observation: Optional[Day0ObservationContext]` on `evaluator.py:89`. That's 12 lines.
- (b) If (a) is genuinely out-of-scope for Phase 3, add a filterwarnings gate narrowly scoped to these callsites so DeprecationWarning from the compat shim only fires when the module is imported outside those two files — AND record a Phase-4 migration ticket with explicit deadline. But (a) is cleaner and removes the deprecation cliff.

### CRITICAL-2 — testeng-emma wrote 3 Phase-6 causality_status tests as Phase-3-failing; they now block green CI
**File:line:** `tests/test_phase3_observation_closure.py:397-468` — `TestCausalityStatusRejectAxis` class (3 tests).

**Evidence:**
```
FAILED test_causality_status_reject_is_distinct_from_observation_unavailable
FAILED test_day0_observation_context_carries_causality_status
FAILED test_evaluator_has_causality_status_reject_gate_for_low_track
```

All three test `causality_status` ∈ {`OK`, `N/A_CAUSAL_DAY_ALREADY_STARTED`, ...} machinery. Per the brief §18 ("Out of scope: Day0 low nowcast signal (Phase 6)") and per `zeus_dual_track_architecture.md:126` + `zeus_current_architecture.md:327`, causality_status + nowcast routing is the **§5 Day0 causality law** that belongs to Phase 6, not Phase 3. exec-bob is correctly not implementing it.

**Why CRITICAL (not MODERATE):** These failures will block the Phase 3 commit gate. They mean `pytest tests/test_phase3_observation_closure.py` is **not green**, contradicting exec-bob's "all R-F tests green" message. The brief's V5 "Phase 1/2 tests still green" excluded these because they're new — but they are in the same file as the R-F tests the brief names as the gate. If team-lead merges on exec-bob's report alone, he will merge a red test file.

**L5 phase-leak classification:** testeng-emma's test file leaked a Phase 6 concern into the Phase 3 gate. The test bodies literally say `"Phase 3 not yet implemented"` for their setUp fail message — so the author's mental model was "Phase 3 delivers this too." That mental model is wrong per the brief.

**Fix (pick one):**
- (a) Mark the 3 tests `@unittest.expectedFailure` or skip with `@unittest.skip("Phase 6 INV-16 causality_status — not yet landed")`. Lowest-risk option.
- (b) Move the `TestCausalityStatusRejectAxis` class to a new `tests/test_phase6_causality_status.py` file that Phase 3 CI does not include. Cleanest.
- (c) Reject option — do NOT silently drop the tests, they encode a real future invariant.

**Owner:** testeng-emma, not exec-bob/exec-carol. Flag this back through team-lead so the right teammate fixes it.

### MAJOR-1 — `Candidate.observation: Optional[dict]` type annotation stale
**File:line:** `src/engine/evaluator.py:89` (and `:620` signature mirroring it).

The field is still typed `Optional[dict]` but now holds `Day0ObservationContext | None`. If CRITICAL-1 is fixed option (a), this gets fixed incidentally. If option (b), this becomes a standalone MAJOR: readers of the evaluator will believe the contract is dict-shaped and write more dict accesses, compounding the problem.

**Fix:** `observation: Optional["Day0ObservationContext"] = None` with a TYPE_CHECKING import guard to avoid circular imports.

### MAJOR-2 — `_get_asos_wu_offset` behavior change is silent
**File:line:** `src/data/observation_client.py:412-464`.

`_fetch_iem_asos` (line 316) calls `_get_asos_wu_offset(city, target_date=target_day)`. On the old dict-contract path, when the calibrated offset didn't exist, IEM was skipped silently (provider returned None). Now that IEM can raise `MissingCalibrationError` (line 452), and `_fetch_iem_asos`'s try/except at line 340 catches `(httpx.HTTPError, KeyError, ValueError)` — **not `MissingCalibrationError`** — so a missing calibration now propagates up through `get_current_observation` as an uncaught exception in the live tick.

Check: `MissingCalibrationError` does inherit from `Exception`, not from any of httpx/KeyError/ValueError. The `except` clause will not catch it. This is a change from "fall through to Open-Meteo" (old: skip silently) to "crash the daemon tick" (new: crash).

**Why MAJOR (not CRITICAL):** The death-trap law (§21 DT#5) actively wants fail-closed over silent degrade. But a raised exception at this seam crashes the whole observation fetch rather than trying the next provider — which is silent-wrong in the other direction (it takes a live calibration gap and promotes it to a hard daemon failure, skipping Open-Meteo which *would* succeed).

**Fix:** Catch `MissingCalibrationError` at `_fetch_iem_asos` (line 340) and return None to fall through to Open-Meteo. That preserves IEM fail-closed semantics (no silent offset fabrication) while preserving provider-chain graceful degradation.

### MODERATE-1 — Script-tier CITY_STATIONS divergence (exec-carol flagged this)
**File:line:** `scripts/backfill_wu_daily_all.py:141`, `scripts/oracle_snapshot_listener.py:59`.

Scout scoped these out of Phase 3, and exec-carol correctly flagged this. Now that `daily_obs_append.py` reads cities.json and the scripts don't, adding a city to cities.json with `settlement_source_type=wu_icao` will silently fail to be picked up by backfill. This is a **data-provenance divergence** (L4 checklist item) between the live lane and the backfill lane.

**Why MODERATE (not MAJOR):** Detectable by L4 `tests/test_cities_config_authoritative.py:78` which already asserts scripts/backfill `CITY_STATIONS` matches cities.json. The divergence path is bounded — adding a city is not routine.

**Fix:** Record a Phase C (K2 packet Phase C per `daily_obs_append.py:35-38` docstring) ticket to lift to shared `wu_icao_client.py` module. Not this phase.

### MODERATE-2 — `Day0ObservationContext.observation_time: object` weakens type guarantee
**File:line:** `src/data/observation_client.py:47`.

The field is typed `object` with a `# raw timestamp — str | int | float | None` comment. If downstream code branches on `isinstance(observation_time, ...)` based on provider identity, the type check happens at consumption, not production — that's the classic translation-loss pattern (authority's §20 provenance law).

**Why MODERATE:** The providers do emit different raw types (WU epoch-int, IEM local_valid string, Open-Meteo ISO8601 string). A `Union[str, int, float, None]` annotation captures that without erasure; `object` is strictly looser than the prose contract in the docstring.

**Fix:** `observation_time: str | int | float | None`.

### LOW-1 — `ObservationUnavailableError` defined twice
**File:line:** `src/data/observation_client.py:27` (module-level) + `src/contracts/exceptions` (imported at `:205`).

`get_current_observation` defines a local `ObservationUnavailableError` class at line 27, then `raise`s a DIFFERENT `ObservationUnavailableError` imported lazily from `src.contracts.exceptions` at line 205-213. Callers that `except ObservationUnavailableError` importing the local one will NOT catch the raised one. Silent-wrong at the exception seam.

**Fix:** Either remove the local definition and always import from `src.contracts.exceptions`, or remove the `src.contracts.exceptions` import and use the local one. Do not keep both.

### LOW-2 — `Day0ObservationContext.__post_init__` allows implicit None on other required fields
**File:line:** `src/data/observation_client.py:50-52`.

Only `low_so_far is None` is rejected. `current_temp=None`, `high_so_far=None`, `unit=None`, `source=None` would all pass, even though the dataclass fields are annotated non-Optional. `@dataclass(frozen=True)` does not enforce non-None at construction.

**Fix:** Expand `__post_init__` to reject None on all required fields, or require the call sites to pre-validate. Small cost; makes NC-08 stricter.

### INFO-1 — FDR test regressions predate Phase 3
3 tests in `tests/test_fdr.py::TestSelectionFamilySubstrate` fail both with and without this phase's diff. They are pre-existing baseline failures in the `data-improve` branch, not introduced by Phase 3. Not blocking this phase but should be tracked in the bug audit backlog.

### INFO-2 — `_fetch_iem_asos` / `_fetch_openmeteo_hourly` return types drifted
Signature says `Optional[dict]` on lines 283 and 351, but both actually return `Optional[Day0ObservationContext]`. Cosmetic only; not blocking.

---

## Big-picture paragraph

Phase 3 is materially correct: R-F/R-G/R-H land, the context is typed, fail-closed semantics replace NC-8 violations in IEM, CITY_STATIONS is gone from the live lane. The concerning pattern is that Phase 3's real surface — the evaluator and monitor_refresh — continues to treat observation as a dict through a compat shim that fires DeprecationWarning at every Day0 evaluation. This is exactly the "encode insight into structure" failure from the four constraints: a new type was introduced but its callers were not migrated, so the type guarantees evaporate one call site later. Combined with the Phase-6 test-suite phase-leak that blocks green CI, the phase needs one more iteration. The fix set is small (~15 lines each for CRITICAL-1 and CRITICAL-2) and non-architectural — this is not a redesign, this is finishing the seam. Forward risk for Phase 4/5: the `scripts/` tier still has its own CITY_STATIONS (MODERATE-1), the exception-class duplication (LOW-1) will bite harder when Phase 6 adds `CausalityStatusNotOK` errors, and the untyped `observation_time` field (MODERATE-2) will force every downstream Day0 consumer to re-do isinstance gymnastics. Fix CRITICAL-1/2 now; defer the rest to their natural phases with tickets.

---

## Fix dispatch (if team-lead agrees)

- **exec-bob**: CRITICAL-1 (evaluator + monitor_refresh attribute migration), MAJOR-1 (Candidate.observation type), MAJOR-2 (MissingCalibrationError catch), LOW-1 (exception dedup).
- **testeng-emma**: CRITICAL-2 (skip or move causality_status tests).
- **backlog** (team-lead logs): MODERATE-1, MODERATE-2, LOW-2, INFO-1.

Re-verify after exec-bob + testeng-emma return.

---

## Round 2 — re-verification (all fixes landed)

**Final verdict: PASS.** Phase 3 gate unlocks.

### CRITICAL-1 (exec-bob): RESOLVED
- `src/engine/evaluator.py:92` — `observation: Optional["Day0ObservationContext"] = None` with TYPE_CHECKING guard. Stale `Optional[dict]` gone.
- `src/engine/evaluator.py:803, 815-828` — all 7 dict accesses converted to attribute (`.low_so_far`, `.high_so_far`, `.current_temp`, `.source`, `.observation_time`). Grep confirms zero `.observation.get(` / `.observation[` remaining.
- `src/engine/evaluator.py:623` — `_get_day0_temporal_context` signature mirrored.
- `src/engine/monitor_refresh.py:258-315` — all 8 accesses on `obs` converted to attribute. Grep confirms zero `obs.get(` / `obs[` remaining.
- Compat shim on `Day0ObservationContext` now fires only from legacy/unknown callers, not from the hot Day0 evaluation path. No runtime DeprecationWarning in trading code.

### CRITICAL-2 (testeng-emma): RESOLVED
- `tests/test_phase6_causality_status.py` created (new file). Contains `TestCausalityStatusRejectAxis` (3 tests) — assertions preserved verbatim, correctly red-fail pending Phase 6 implementation.
- `tests/test_phase3_observation_closure.py` — class removed; file now 21/21 green.
- Green CI gate for Phase 3 established.

### MAJOR-2 (exec-bob): RESOLVED
- `src/data/observation_client.py:331` — `except MissingCalibrationError` clause added before generic `(httpx.HTTPError, KeyError, ValueError)`. Returns None → falls through to Open-Meteo. Chain degradation preserved, fail-closed semantics intact.

### LOW-1 (exec-bob): RESOLVED
- `src/data/observation_client.py:24` — `ObservationUnavailableError` single-sourced from `src.contracts.exceptions`. Local class definition removed; no silent catch/miss between duplicate exception classes.

### Deferred (as noted round 1)
MODERATE-1 (scripts-tier `CITY_STATIONS` divergence) → Phase C (K2 packet Phase C per `daily_obs_append.py:35` docstring).
MODERATE-2 (`observation_time: object` → `str | int | float | None`) → Phase 4 when schema v2 writer lands.
LOW-2 (`__post_init__` only rejects None on `low_so_far`, not other required fields) → nice-to-have.
INFO-1 (pre-existing FDR baseline regressions) → bug audit backlog.
INFO-2 (cosmetic return-type annotations on internal `_fetch_*` functions) → housekeeping.

### Final test battery
```
tests/test_phase3_observation_closure.py + tests/test_phase3_source_registry_single_truth.py: 21/21 PASS
Full regression set (metric_identity_spine + schema_v2_gate_a + k2_live_ingestion_relationships + fdr_family_scope + dual_track_law_stubs): 85 passed, 4 skipped, 7 subtests passed.
tests/test_phase6_causality_status.py: 3 RED (correct — Phase 6 gate not yet open).
```

Phase 3 PASS. Gate B opens. Ready for commit.
