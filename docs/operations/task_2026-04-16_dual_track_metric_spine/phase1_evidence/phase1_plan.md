# Phase 1 — MetricIdentity Spine + FDR Scope Split

Packet: Dual-Track Metric Spine Refactor
Date opened: 2026-04-16
Owner: main-thread
Predecessors: Phase 0 (commit `943e74d`), Phase 0b (commit `df12d9c`)
Evidence base: `phase0_evidence/mental_model_baseline.md` + three Phase-1 openers
(Explore strings, Explore FDR sites, architect relationship trace).

## 1. Goal

Kill the "temperature metric is a bare string" pattern across Zeus by introducing
a typed `MetricIdentity` at a single choke point, plus scope-parameterize the
FDR family grammar so same-scope call sites can never silently merge BH
discovery budgets with different-scope call sites.

Phase 1 deliberately does **not** implement the low Day0 nowcast signal (Phase
6), the World DB v2 tables (Phase 2), the Kelly executable-price input (Phase
9 pre-activation), or the `low_so_far` producer (Phase 3). Phase 1 is purely
the type spine + the FDR grammar.

## 2. Law anchors

- **INV-14** metric identity mandatory — machine manifest
- **INV-22 + NC-15** FDR family canonicalization — machine manifest
- `docs/authority/zeus_dual_track_architecture.md` §2 (metric identity spine),
  §6 DT#3 (FDR canonicalization)
- `docs/authority/zeus_current_architecture.md` §13, §18
- Root `AGENTS.md` dual-track forecast truth, durable boundaries

## 3. Relationship invariants (R1–R4)

These are the load-bearing semantic properties. They are encoded as failing
pytest tests **before** implementation; they pass after executor completes.

### R1 — Metric identity type safety
**Property**: `MetricIdentity` cannot hold inconsistent pairings. A `temperature_metric == "high"` identity must have `observation_field == "high_temp"` and a high-side `ensemble_field`. Cross-pairings raise `ValueError` at construction.

**Observable failure before implementation**: the module does not exist yet.

**Target file**: new `src/types/metric_identity.py`

**Minimum pytest**:
```python
def test_metric_identity_rejects_cross_pairing():
    with pytest.raises(ValueError):
        MetricIdentity(
            temperature_metric="high",
            physical_quantity="mx2t6_local_calendar_day_max",
            observation_field="low_temp",  # WRONG
            data_version="tigge_mx2t6_local_calendar_day_max_v1",
        )

def test_metric_identity_canonical_instances_exist():
    assert HIGH_LOCALDAY_MAX.temperature_metric == "high"
    assert HIGH_LOCALDAY_MAX.observation_field == "high_temp"
    assert LOW_LOCALDAY_MIN.temperature_metric == "low"
    assert LOW_LOCALDAY_MIN.observation_field == "low_temp"
```

### R2 — Day0 low branch must not silently consume high-side inputs
**Property**: `Day0Signal` constructed with `temperature_metric.is_low()` must
**refuse** (`NotImplementedError`) rather than run `p_vector()` on high-side
inputs. Phase 6 later replaces the refusal with a real low nowcast class.

**Observable failure before implementation**: today `Day0Signal(...,
temperature_metric="low", ...).p_vector(bins)` returns a bin-vector identical
to the high case — silently wrong.

**Target file**: `src/signal/day0_signal.py` constructor tail (`__init__` or a
new `__post_init__`-like check at the end of `__init__`).

**Minimum pytest**:
```python
def test_day0signal_low_metric_refuses_until_phase6():
    with pytest.raises(NotImplementedError):
        Day0Signal(
            observed_high_so_far=80.0,
            current_temp=78.0,
            hours_remaining=4.0,
            member_maxes_remaining=np.array([85., 84., 82.]),
            temperature_metric=LOW_LOCALDAY_MIN,
        )
```

### R3 — FDR family canonical identity (scope-aware)
**Property**: The FDR family grammar has **two scopes**:
- `per_candidate`: one BH budget per (cycle_mode, city, target_date, discovery_mode, decision_snapshot_id). Does NOT carry `strategy_key`.
- `per_strategy`: one BH budget per (cycle_mode, city, target_date, strategy_key, discovery_mode, decision_snapshot_id). Carries `strategy_key`.

Two separate functions encode this:
- `make_hypothesis_family_id(cycle_mode, city, target_date, discovery_mode, decision_snapshot_id=…) → str`
- `make_edge_family_id(cycle_mode, city, target_date, strategy_key, discovery_mode, decision_snapshot_id=…) → str`

Canonicalization guarantees: per-scope inputs produce a stable ID; different-scope inputs produce different IDs even if their overlapping parts match; passing `strategy_key=""` to `make_edge_family_id()` raises `ValueError` (an edge family requires a real strategy).

**Observable failure before implementation**: today one `make_family_id(..., strategy_key="")` at `:398` and one `make_family_id(..., strategy_key=real)` at `:437` co-exist; a test asserting scope separation cannot be expressed.

**Target file**: `src/strategy/selection_family.py` (rename + split).

**Minimum pytest**:
```python
def test_family_id_scope_separation():
    cand = dict(cycle_mode="opening_hunt", city="NYC", target_date="2026-04-01",
                discovery_mode="opening_hunt", decision_snapshot_id="snap-1")
    h = make_hypothesis_family_id(**cand)
    e = make_edge_family_id(**cand, strategy_key="center_buy")
    assert h != e  # scope distinction is real
    assert h == make_hypothesis_family_id(**cand)  # deterministic

def test_edge_family_refuses_empty_strategy_key():
    with pytest.raises(ValueError):
        make_edge_family_id(cycle_mode="x", city="y", target_date="z",
                            strategy_key="", discovery_mode="x")
```

### R4 — String→MetricIdentity one-way type seam
**Property**: The normalizer at `src/engine/evaluator.py:650-651` is the ONLY
source where a bare `str` enters the metric pipeline. Every downstream signal
class (`Day0Signal`, `ensemble_signal.py` helpers, `day0_window.py`) refuses a
bare `str` for `temperature_metric` — they accept only `MetricIdentity`.

**Observable failure before implementation**: today every downstream signal
happily accepts `temperature_metric: str`.

**Target files**:
- `src/signal/day0_signal.py:34, 53` constructor
- `src/signal/ensemble_signal.py:143, 238` (two classes)
- `src/signal/day0_window.py:27`

**Minimum pytest**:
```python
def test_day0signal_refuses_bare_string_metric():
    with pytest.raises(TypeError):
        Day0Signal(
            observed_high_so_far=80.0, current_temp=78.0, hours_remaining=4.0,
            member_maxes_remaining=np.array([85.]),
            temperature_metric="high",  # bare str, not MetricIdentity
        )
```

## 4. Sink surface (8 files, 17 hits — from opener A)

All sites that today thread `temperature_metric: str`. These are the
implementation targets for executor:

| File | Lines | Role |
|---|---|---|
| `src/engine/evaluator.py` | 81, 271, 650, 794, 1011 | `MarketCandidate` field + normalizer + 3 branches |
| `src/signal/day0_signal.py` | 53, 71 | constructor param + instance attr |
| `src/signal/day0_window.py` | 27, 54 | constructor param + branch |
| `src/signal/ensemble_signal.py` | 143, 155, 238, 262 | two classes + one branch |
| `src/state/portfolio.py` | 146 | `Position` field |
| `src/engine/cycle_runtime.py` | 261, 885 | two threading points |
| `src/engine/monitor_refresh.py` | 287 | one threading point |
| `src/data/market_scanner.py` | 52, 53 | `infer_temperature_metric` returns str (legal entry seam — do NOT change, B0 in path map) |

## 5. New / renamed modules

- **NEW**: `src/types/metric_identity.py` — `MetricIdentity` dataclass + two canonical instances `HIGH_LOCALDAY_MAX`, `LOW_LOCALDAY_MIN` + factory `MetricIdentity.from_raw(value)` that accepts `str | MetricIdentity` and returns `MetricIdentity`. This is the single legal string→typed conversion point.

- **SPLIT**: `src/strategy/selection_family.py` — `make_family_id()` becomes two functions (`make_hypothesis_family_id`, `make_edge_family_id`). The old name stays as a deprecated wrapper that raises `DeprecationWarning` and dispatches based on whether `strategy_key` is empty/None — prevents silent call-site breakage during migration, and lets the critic check that no caller still uses it after the migration pass.

## 6. FDR call-site migration (4 live + 1 test)

Opener B confirmed the site intent. Per that intent:

| Site | Today | After Phase 1 |
|---|---|---|
| `evaluator.py:394 (hypothesis branch)` | `make_family_id(..., strategy_key="")` | `make_hypothesis_family_id(...)` |
| `evaluator.py:433 (edge branch)` | `make_family_id(..., strategy_key=strategy_key)` | `make_edge_family_id(..., strategy_key=strategy_key)` |
| `evaluator.py:477 (post-FDR re-keying)` | `make_family_id(..., strategy_key=row["strategy_key"])` | Don't reconstruct. Read `row["family_id"]` — carry the original ID by reference. Add a test that asserts the re-keyed ID equals the original. |
| `evaluator.py:570 (full-family snapshot)` | `make_family_id(..., strategy_key="")` | `make_hypothesis_family_id(...)` |
| `tests/test_fdr.py:95` | `make_family_id(..., strategy_key="center_buy", ...)` | `make_edge_family_id(..., strategy_key="center_buy")` |

## 7. Out of scope for Phase 1

- Day0 low nowcast implementation (Phase 6).
- World DB v2 schema (Phase 2).
- `kelly_size()` signature change (Phase 9 pre-activation).
- `low_so_far` observation producer (Phase 3).
- Graceful degradation monitor lane (Phase 6).
- RED force-exit sweep (risk phase pre-Phase 9).
- `OBS_FIELD` string rewrite (Phase 3 — 31 hits, orthogonal).
- Removing the `_record_selection_hypothesis_fact` anti-pattern (opener B flagged `family_id` foreign key that doesn't resolve; Phase 1.5 follow-up, not blocking).

## 8. Execution plan

1. **Main thread writes this plan + R1–R4 spec.** (done by the act of writing this file)
2. **`test-engineer`**: implement R1–R4 as pytest failing tests. All 6 tests (R1×2, R2×1, R3×2, R4×1) must FAIL today because `MetricIdentity` and the new FDR functions do not exist. Save under `tests/test_metric_identity_spine.py` and `tests/test_fdr_family_scope.py`.
3. **`executor`**: implement `src/types/metric_identity.py`, rewrite the 8 sink files, split `make_family_id()` into the two scope-aware variants, and migrate the 4+1 call sites. All 6 R1–R4 tests must pass. Phase 0b stub `test_fdr_family_key_is_canonical` in `test_dual_track_law_stubs.py` may now be un-skipped if its assertion fits R3 — otherwise leave it skipped.
4. **`critic` (opus) adversarial review** of the full Phase 1 diff. Must confirm: (a) R1–R4 tests fail-before / pass-after, (b) no sink file still threads `temperature_metric: str` after migration, (c) the `make_family_id` deprecated wrapper routes correctly, (d) no INV-13 / INV-21 regression (cascade-constants law still enforced via `require_provenance("kelly_mult")`).
5. **Main thread**: commit + push.

## 9. Gate for Phase 2

Phase 2 may open only when:
- R1–R4 all green
- `test_dual_track_law_stubs.py::test_fdr_family_key_is_canonical` un-skipped and green
- Critic verdict PASS
- No file in `src/**` still declares `temperature_metric: str` as a public parameter type (private type aliases OK)

## 10. Risks to watch

- **False green on R2**: a test that passes because `Day0Signal` raises on any `temperature_metric` argument — must assert specifically the low branch raises and the high branch still works.
- **Call-site silent drift**: an evaluator branch that reconstructs `row["family_id"]` via string concatenation instead of by-reference read. Opener B flagged the post-FDR path (`:477`) specifically.
- **Test-file fixture drift**: opener A found 9 `TEST_FIXTURE` OBS_FIELD hits — Phase 3 problem, but make sure none accidentally exercise `temperature_metric="low"` through Day0Signal in a way R2 would now break.
