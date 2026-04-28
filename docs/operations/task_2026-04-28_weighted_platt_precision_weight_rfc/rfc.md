# RFC: Replace Binary `training_allowed` with Continuous `precision_weight`

| Field | Value |
|---|---|
| Authors | Fitz + AI |
| Created | 2026-04-28 |
| Status | DRAFT — pending operator review |
| Authority basis | `architecture/invariants.yaml::INV-15`, `src/types/metric_identity.py`, `tests/test_harvester_metric_identity.py`, `tests/test_pe_reconstruction_relationships.py` |
| Plan-evidence | `evidence/poc_summary.md` (PoC v4 OOS Brier with bootstrap CI95) |
| Scope | Schema change to `ensemble_snapshots_v2`, `calibration_pairs_v2`; calibration store + Platt fit code path; antibody tests; type system |
| Risk class | **Architecture** (changes truth ownership semantics on canonical tables) — planning lock applies |

## TL;DR

Zeus's TIGGE LOW (mn2t6) extractor uses a binary `training_allowed: bool` gate that discards 78% of LOW snapshots due to "boundary_ambiguous" 6h-step resolution issues. PoC v4 (1.7M pair, 60-day OOS holdout, 500-resample bootstrap) shows that **continuous precision-weighted MLE is statistically significantly better than the binary gate** (overall ΔBrier = −0.00018, CI95 [−0.00022, −0.00014]; Asia subset ΔBrier = −0.00021), and the recoverable training set grows **3.16× to 4.65×**.

This RFC proposes replacing `training_allowed: bool` with `precision_weight: float ∈ [0, 1]` across the ensemble_snapshots_v2 → calibration_pairs_v2 → platt_models_v2 pipeline. It is a category-impossibility fix (per Fitz Constraint #2): the type system after migration prohibits binary discard of any continuous-quality dimension.

## 1. Problem statement

`extract_tigge_mn2t6_localday_min.py:328-333` collapses four heterogeneous quality dimensions into one bool:

```python
training_allowed = (
    len(missing) == 0                  # member completeness (binary OK)
    and horizon_satisfied              # step horizon ≥ required (binary OK)
    and causality["pure_forecast_valid"]  # no observation leakage (binary OK)
    and not any_boundary_ambiguous     # 6h step resolution → daily MIN aliasing (CONTINUOUS, miscoded as binary)
)
```

The first three are physical / causal hard constraints (binary IS appropriate). The fourth is an **epistemic precision** dimension that should be continuous: "how confident are we that this snapshot's MIN value is correctly computed?" — a function of how many of 51 ensemble members had their daily-MIN bucket fall on a 6h-step boundary.

Coercing precision into binary creates an information cliff (Fitz Constraint #2: translation loss is thermodynamic). 78% of LOW data drops to weight=0 instead of receding smoothly toward weight=ε.

### Concrete impact

- LOW Asia cities: kuala-lumpur 1.8% training_allowed=True, singapore 3.0%, tokyo 5.5%, jakarta 3.4% → **Asia LOW Platt cannot be reliably per-city trained**
- LOW lead 7: 12% True, lead 0: 32.2% True → **longest leads (highest signal-value region) most starved**
- HIGH track unaffected (100% training_allowed=True) → **HIGH/LOW asymmetric system behavior** is hard-coded

### Walk-forward 10 steps (per Fitz methodology)

```
Step  1   Asia LOW thin → Per-city Platt CI wide
Step  2   Wide CI → smaller positions
Step  3   Small positions → fewer settled outcomes
Step  4   Fewer outcomes → next training cycle still thin (positive feedback)
Step  5   MM detect Zeus's Asia LOW gap, fade marginal positions
Step  6   Adverse selection → Asia LOW Sharpe degrades
Step  7   Zeus avoidance reinforces gap
Step  8   Climate drift in Asia (monsoon shift, urbanization) goes undetected
Step  9   Calibration silently rots, no antibody triggers
Step 10   Regime shock exposes systemic Asia LOW blind spot
```

## 2. First-principles framing

The job of zeus's calibration pipeline is to **convert information into edge**. Every (city, day, lead, member) datum carries SOME information about the calibration. The system should aggregate weighted information; it should NOT gate at thresholds beyond physical impossibility.

Three categories of "data quality":

| Category | Examples | Correct encoding |
|---|---|---|
| Physical impossibility | causality leak (forecast time < observation), horizon deficit (no step covers required window), member-level NaN | binary 0 (true 0-information) |
| Epistemic uncertainty | boundary_ambiguous (resolution-limited), ensemble spread, sensor noise | continuous in [0, 1] |
| External authority | source-tag whitelist (INV-15) | typed enum, NOT string-set membership |

Current zeus mixes all three into `training_allowed: bool`. This RFC separates them.

## 3. Proposed change

### 3.1 Type-system change

```python
# REMOVE
@dataclass
class Snapshot:
    members: list[Member]
    training_allowed: bool   # ← information cliff

# ADD
@dataclass(frozen=True)
class PrecisionTag:
    """Quality envelope for one (city, target_date, lead) snapshot."""
    # Physical (binary, hard rejection)
    causality_pure_forecast: bool
    horizon_satisfied: bool
    member_count_complete: bool

    # Epistemic (continuous, weighted)
    ambiguous_member_fraction: float   # ∈ [0, 1]; from boundary classification

    # Computed
    @property
    def physically_valid(self) -> bool:
        return (self.causality_pure_forecast
                and self.horizon_satisfied
                and self.member_count_complete)

    @property
    def precision_weight(self) -> float:
        """Final scalar weight for MLE. Zero iff physically invalid."""
        if not self.physically_valid:
            return 0.0
        return max(WEIGHT_FLOOR, 1.0 - self.ambiguous_member_fraction)
```

`training_allowed: bool` is deleted from the dataclass. Any code that imports it raises ImportError at compile time.

### 3.2 Schema change — `ensemble_snapshots_v2`

Add column (additive, legacy-safe):
```sql
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN precision_weight REAL
    DEFAULT 1.0
    CHECK (precision_weight >= 0.0 AND precision_weight <= 1.0);
```

Default `1.0` ensures pre-migration HIGH rows are unweighted (matching their training_allowed=True semantics). The constraint enforces type-system intent at DB level.

`training_allowed` column kept temporarily for back-compat during shadow-fit window; deleted in Phase 5.

### 3.3 Schema change — `calibration_pairs_v2`

```sql
ALTER TABLE calibration_pairs_v2 ADD COLUMN precision_weight REAL
    DEFAULT 1.0
    CHECK (precision_weight >= 0.0 AND precision_weight <= 1.0);
```

Plus new index for time-series eval queries:
```sql
CREATE INDEX idx_calibration_pairs_v2_target_date_metric
    ON calibration_pairs_v2(target_date, temperature_metric);
```

### 3.4 Code path change — Platt fit

Current: `src/calibration/platt.py::fit_platt` (uniform MLE)

Proposed: extend with `weights: np.ndarray | None`:
```python
def fit_platt(p_raw, lead, y, *, weights=None):
    """Weighted MLE Platt fit.
    
    weights=None reduces to uniform MLE (back-compat; HIGH track default).
    weights=array applies sample_weight; LOW track uses precision_weight.
    """
    ...
```

When `weights` is None, the new code path produces numerically identical fits to the legacy path (antibody test enforces this).

### 3.5 Antibody tests (Fitz Constraint #3)

New tests under `tests/`:

```python
# tests/test_no_binary_quality_gates.py
def test_calibration_pipeline_signatures_have_no_bool_quality_gates():
    """Type-level antibody: no quality dimension can be a bool param.
    
    Searches signatures of all functions in src/calibration/ and src/data/
    that touch calibration_pairs or ensemble_snapshots. Any bool param
    whose name contains 'allowed', 'valid', 'ok' must be either renamed
    to 'physically_*' (kept binary) or replaced with weight: float.
    """
    ...

# tests/test_weighted_platt_legacy_equivalence.py
def test_weighted_mle_equals_unweighted_when_weights_unit():
    """Backwards-compat antibody: w_i=1 produces identical fit to legacy."""
    ...

# tests/test_low_asia_recovers_with_weighted_pipeline.py
def test_asia_low_effective_sample_size_above_threshold():
    """Relationship test: Asia LOW eff_n with weighting must be ≥ 30% of
    Asia HIGH eff_n. Today the ratio is 1.8% (KL) which is not a viable
    training population. Post-RFC must exceed 30%."""
    ...
```

The first test makes future binary-gating impossible to add without test failure.

### 3.6 Default `precision_weight` for HIGH track

HIGH track has 100% training_allowed=True today. Migration sets `precision_weight = 1.0` for all existing HIGH rows. No behavioral change for HIGH.

## 4. Migration plan (5 phases, parallel-runnable, reversible)

### Phase 0: pre-migration antibody bar

- Land `tests/test_harvester_metric_identity.py` (already exists ✓)
- Land `tests/test_settlements_physical_quantity_invariant.py` (separate packet `task_2026-04-28_settlements_physical_quantity_migration` in flight)
- Land `tests/test_no_binary_quality_gates.py` (this RFC, P0)

### Phase 1: schema additive
- ALTER TABLEs to add `precision_weight REAL DEFAULT 1.0` (above)
- Backfill: all existing rows get `precision_weight = 1.0` (matches their current training_allowed=True semantics for those that exist)
- DB migration script: `scripts/migrations/add_precision_weight_2026-04-28.py`
- Antibody: schema introspection test

### Phase 2: extractor emits precision_weight (shadow)
- Modify `extract_tigge_mn2t6_localday_min.py` to emit `precision_weight` field in JSON output. KEEP existing `training_allowed: bool` in JSON.
- New extracts get both fields. Old extracts unchanged.
- Re-extract all LOW (or use the recovered-via-inner_min trick from PoC; both work)
- Antibody: every JSON has both fields, weights are in [0, 1]

### Phase 3: ingest_grib_to_snapshots writes precision_weight
- Modify `scripts/ingest_grib_to_snapshots.py` to write precision_weight to ensemble_snapshots_v2
- For pre-Phase-2 JSON without precision_weight field: derive from training_allowed (1.0 if True, 0.0 if False) — same semantics as current
- Antibody: snapshot rows have precision_weight matching JSON-derived value within 1e-9

### Phase 4: shadow Platt fit (parallel run)
- New `src/calibration/platt_weighted.py` with `fit_platt_weighted(weights=...)`
- Shadow fit runs in `scripts/refit_platt_shadow.py` against existing data, using precision_weight
- Compare shadow fit vs legacy fit on rolling 60-day OOS window
- Acceptance gate: 30 consecutive days of shadow Brier ≤ legacy Brier overall AND on Asia subset
- Antibody: `test_weighted_platt_legacy_equivalence` (forced w=1 path) PASS

### Phase 5: cutover + cleanup
- Replace `src/calibration/platt.py::fit_platt` to call platt_weighted internally
- Default behavior unchanged for callers that don't pass weights (HIGH track)
- LOW Platt fits now use precision_weight by default
- Drop `training_allowed` column from ensemble_snapshots_v2 and calibration_pairs_v2 (DDL migration)
- Delete `extract_tigge_mn2t6_localday_min.py`'s `training_allowed: bool` output (still emit for one more cycle behind a deprecation warning, then remove)
- Antibody: `test_calibration_pipeline_signatures_have_no_bool_quality_gates` PASS

### Rollback at any phase

| Phase | Rollback action |
|---|---|
| 0 | Drop new tests |
| 1 | `ALTER TABLE … DROP COLUMN precision_weight` (sqlite ≥3.35) |
| 2 | Revert extractor; old JSON still parses |
| 3 | Revert ingester; precision_weight column ignored |
| 4 | Don't promote shadow fit; legacy fit remains canonical |
| 5 | Revert Phase 5 commits; rollback DROP COLUMN if needed |

Each phase is independently reversible; commits are atomic per phase.

## 5. Acceptance criteria

| Criterion | Threshold | Evidence source |
|---|---|---|
| Asia LOW eff_n recovery | ≥ 3.0× | PoC v4 (3.16×) |
| Overall OOS Brier | ≤ legacy Brier (95% CI) | bootstrap CI |
| Asia OOS Brier | ≤ legacy Brier (95% CI) | bootstrap CI |
| HIGH OOS Brier (regression check) | NO change beyond noise (CI overlaps 0) | new HIGH-only PoC needed in Phase 4 |
| Per-city OOS Brier regression count | ≤ 5 / 51 cities significantly worse | PoC v4 shows 8 / 21 sampled — TBD investigation |
| Shadow stability | 30 consecutive OOS days, both overall AND Asia ≤ legacy | Phase 4 |
| Antibody tests | 100% pass on each phase boundary | CI |

The per-city regression criterion is currently unmet (8 / 21 sampled regressed in PoC). RFC implementation must include an investigation pass to either:
- Identify and remediate per-city pathology (e.g., add cluster-level smoothing for cities with few obs)
- Document regression as acceptable given aggregate improvement
- Adopt a more conservative weight scheme that protects regressed cities

## 6. Open dependencies

### 6.1 Forecast-side (this RFC)
- Per-city regression investigation (8/21 cities worse in PoC). Hypotheses: city-specific calibration bias, monsoon-season noise correlation, sensor noise unit-scale (mostly ruled out by v4).
- Decision: include cluster-level smoothing in the weighted Platt formulation, or train per-city without smoothing.

### 6.2 Settlement-side (parallel packet, not blocking)
- [`task_2026-04-28_settlements_physical_quantity_migration`](../task_2026-04-28_settlements_physical_quantity_migration/plan.md) — packet drafted 2026-04-28. Components:
  - `plan.md` — full plan with antibody extension, idempotency, atomicity, rollback
  - `scripts/migrate_settlements_physical_quantity.py` — dry-run/apply, snapshots before mutation, post-count verification
  - `tests/test_settlements_physical_quantity_invariant.py` — preventive antibody (currently FAILS until migration --apply runs, exposing the 1561-row drift)
  - **NEEDS_OPERATOR_APPROVAL** — script runs only with `--apply` flag plus operator sign-off
- This is a precondition for any future LOW settlement writer (typed `MetricIdentity` parameterization). Independent of THIS RFC (forecast skill calibration uses `observations.low_temp` ground truth, not `settlements`).
- LOW settlements backfill scoping (separate sub-agent finding) — Polymarket LOW market truth currently empty on disk; operator decision required: (a) re-scrape historical, (b) forward-only, (c) declare structurally absent. **Not blocking THIS RFC** — this RFC improves forecast skill calibration which doesn't require Polymarket settlements (uses `observations.low_temp` ground truth via Phase 4 shadow eval).

### 6.3 INV-15 reconciliation
- `src/calibration/store.py:30`: `_TRAINING_ALLOWED_SOURCES = frozenset({"tigge", "ecmwf_ens"})` — string-set membership. The right antibody for INV-15 is also typed (`SourceTag` enum), not string. Out of scope for this RFC; flagged as next-in-line.

## 7. Risks (Fitz risk classification)

| Risk | Class | Mitigation |
|---|---|---|
| Per-city regression hurts existing edge | Math (recoverable) | Phase 4 shadow eval gates on per-city threshold |
| Weight function tunability creates configuration drift | Architecture | Type-level `PrecisionTag` dataclass — only formula changes are code commits |
| Schema change breaks downstream consumers | Architecture | Additive column, default 1.0, no behavior change until Phase 4 |
| HIGH track silently regresses | Math | Phase 4 includes HIGH-only PoC with same statistical bar |
| Existing tests using `training_allowed` field break | Governance | Deprecation warnings in Phase 5 grace cycle |
| Future code re-introduces `bool training_allowed` | Architecture | `test_no_binary_quality_gates` — type-level prohibition |

## 8. Why not simpler alternatives?

### "Just relax the boundary rule" (subset of A)
- Still binary, just shifts the cutoff
- Same information cliff problem at the new threshold
- Doesn't address the systemic asymmetry across HIGH/LOW

### "Use 12Z runs to avoid Asia sunrise alignment" 
- More data of same kind, same binary rule
- Doesn't address the categorical type error
- Network cost ≫ this RFC's code cost

### "Use 3h step resolution"
- Tightens boundary localization but doesn't eliminate it
- Requires re-downloading TIGGE archive at ~2× current size
- Still treats remaining ambiguity as binary

### Why this RFC is right
- Categorically removes the error-class (Fitz Constraint #1: structural decisions > patches)
- Backwards-compatible at every phase (zero-risk Phase 4 shadow)
- Antibody-driven (Fitz Constraint #3: tests prevent regeneration)
- Provenance-aware (Fitz Constraint #4: precision_weight surfaces as data field, can be audited per-row)

## 9. What this RFC does NOT do

- Does NOT address LOW settlements backfill (separate packet; gated on operator decision about Polymarket source)
- Does NOT change forecast download or extraction physics — uses recovered `inner_min_native_unit` already in JSON
- Does NOT touch HIGH track behavior (precision_weight=1.0 default)
- Does NOT introduce new external dependencies (numpy/scipy already used)

## 10. Decision required

To proceed, operator must decide:
1. ✅ Approve / ❌ Reject the structural change (replace bool gate with float weight)
2. Approve weight function family: `D_softfloor` recommended (PoC evidence), tunable floor
3. Approve schema migration window: 5 phases over ~3 weeks (Phase 0-5 above)
4. Approve per-city regression criterion: ≤ 5 / 51 OR alternative threshold
5. Authorize the related antibody tests to land in Phase 0 (gate for downstream work)

## 11. Implementation owner / next step

Recommended owner: a single executor agent slice per phase, gated on antibody pass. After approval:
- Phase 0 → 1 day (tests only)
- Phases 1-3 → 1 week (additive schema + ingest)
- Phase 4 → 30-day shadow eval
- Phase 5 → 1 day (cutover) + 1 cycle deprecation grace

Total timeline: ~5 weeks from approval to legacy-deletion.
